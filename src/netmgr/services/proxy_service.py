"""Điều phối các lớp proxy (§3.1).

Điểm mấu chốt của thiết kế: bật/tắt proxy không phải MỘT thao tác mà là nhiều
thao tác lên nhiều lớp độc lập. Một lớp hỏng không được làm hỏng các lớp khác,
và người dùng phải thấy được lớp nào đang bật — nếu không sẽ gặp đúng tình
huống "tôi tắt proxy rồi mà `apt` vẫn qua proxy".

Không import `gi` trực tiếp: các layer được tiêm vào, nên service test được
bằng layer giả.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from ..domain.models import OpResult, ProxyConfig, ProxyLayerId, ProxyMode
from ..domain.validators import validate_proxy

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ApplyReport:
    """Kết quả áp dụng lên từng lớp.

    Cố tình KHÔNG rút gọn thành một bool: "thành công một phần" là trạng thái có
    thật và người dùng cần biết chính xác lớp nào hỏng.
    """

    config_name: str
    results: dict[ProxyLayerId, OpResult]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results.values())

    @property
    def failed_layers(self) -> list[ProxyLayerId]:
        return [layer for layer, r in self.results.items() if not r.ok]

    def summary(self) -> str:
        if self.ok:
            notes = [r.message for r in self.results.values() if r.message]
            return "; ".join(notes) if notes else "Đã áp dụng"
        return "; ".join(
            r.message for r in self.results.values() if not r.ok
        )


class ProxyService:
    def __init__(self, layers, store, secrets) -> None:
        self._layers = {layer.id: layer for layer in layers}
        self._store = store
        self._secrets = secrets
        self._configs: list[ProxyConfig] = []
        self._active_id: str | None = None
        #: Cấu hình bật gần nhất, để `toggle()` bật lại đúng cái đó.
        self._last_used_id: str | None = None
        self.reload()

    # ── trạng thái ──────────────────────────────────────────────────────────

    def reload(self) -> None:
        self._configs = self._store.load()
        self._active_id = self._store.load_active_id()
        if self._active_id and not self.get(self._active_id):
            # Config bị xoá tay khỏi file mà active_id còn trỏ tới nó.
            self._active_id = None

    @property
    def configs(self) -> list[ProxyConfig]:
        return list(self._configs)

    @property
    def active(self) -> ProxyConfig | None:
        return self.get(self._active_id) if self._active_id else None

    @property
    def is_enabled(self) -> bool:
        config = self.active
        return bool(config and config.is_enabled)

    def get(self, config_id: str) -> ProxyConfig | None:
        return next((c for c in self._configs if c.id == config_id), None)

    def get_password(self, config_id: str) -> str | None:
        """Mật khẩu đã lưu, để điền sẵn vào form sửa."""
        return self._secrets.load_password(config_id)

    def summary(self) -> str | None:
        """Tóm tắt cho tray. None nghĩa là proxy đang tắt."""
        config = self.active
        if config is None or not config.is_enabled:
            return None
        return f"{config.name} — {config.summary()}"

    def layer_statuses(self) -> list:
        """Dữ liệu cho màn hình chẩn đoán proxy (FR-P4 / R4)."""
        return [layer.status() for layer in self._layers.values()]

    # ── CRUD ────────────────────────────────────────────────────────────────

    def save(self, config: ProxyConfig, password: str | None = None) -> OpResult:
        result = validate_proxy(config)
        if not result.ok:
            return OpResult(False, result.errors[0].message)

        existing = self.get(config.id)
        if existing is None:
            self._configs.append(config)
        else:
            self._configs[self._configs.index(existing)] = config

        if config.auth_enabled and password:
            self._secrets.save_password(config.id, password)
        elif not config.auth_enabled:
            self._secrets.delete_password(config.id)

        self._persist()
        return OpResult.success()

    def delete(self, config_id: str) -> OpResult:
        config = self.get(config_id)
        if config is None:
            return OpResult(False, "Không tìm thấy cấu hình proxy")

        if self._active_id == config_id:
            # Xoá cấu hình đang bật mà không tắt trước sẽ để proxy chạy mồ côi:
            # người dùng không còn cách nào tắt nó từ trong app.
            self.disable()

        self._configs.remove(config)
        self._secrets.delete_password(config_id)
        self._persist()
        return OpResult.success()

    def duplicate(self, config_id: str, new_name: str) -> ProxyConfig | None:
        from uuid import uuid4

        source = self.get(config_id)
        if source is None:
            return None
        clone = replace(
            source,
            id=str(uuid4()),
            name=new_name,
            ignore_hosts=list(source.ignore_hosts),
            layers=set(source.layers),
        )
        self._configs.append(clone)
        self._persist()
        return clone

    # ── áp dụng ─────────────────────────────────────────────────────────────

    def activate(self, config_id: str) -> ApplyReport:
        config = self.get(config_id)
        if config is None:
            return ApplyReport("", {ProxyLayerId.DESKTOP: OpResult(False, "Không tìm thấy cấu hình")})

        password = self._secrets.load_password(config.id) if config.auth_enabled else None
        results: dict[ProxyLayerId, OpResult] = {}

        for layer_id, layer in self._layers.items():
            if not layer.is_available():
                continue
            if layer_id in config.layers:
                results[layer_id] = layer.apply(config, password)
            else:
                # Lớp không được chọn phải bị TẮT, không phải bỏ qua — nếu không,
                # cấu hình của profile trước sẽ còn sót lại ở đó.
                results[layer_id] = layer.clear()

        self._active_id = config.id
        self._persist()
        return ApplyReport(config.name, results)

    def disable(self) -> ApplyReport:
        results = {
            layer_id: layer.clear()
            for layer_id, layer in self._layers.items()
            if layer.is_available()
        }
        self._active_id = None
        self._persist()
        return ApplyReport("Tắt", results)

    def toggle(self) -> ApplyReport | None:
        """Bật/tắt nhanh từ tray (FR-P1).

        Tắt thì nhớ cấu hình vừa dùng để bật lại đúng cái đó.
        """
        if self.is_enabled:
            self._last_used_id = self._active_id
            return self.disable()

        target = self._last_used_id or (self._configs[0].id if self._configs else None)
        return self.activate(target) if target else None

    # ── import từ hệ thống ──────────────────────────────────────────────────

    def import_current(self, name: str = "Cấu hình hiện có") -> ProxyConfig | None:
        """Tạo một cấu hình từ trạng thái proxy đang có của hệ thống.

        Quan trọng với người đã cấu hình proxy sẵn: không có nó thì `ignore-hosts`
        họ đã dày công gõ (vd `*.viettel.vn`, `10.*.*.*`) sẽ bị mất khi app ghi
        đè bằng giá trị mặc định.
        """
        from uuid import uuid4

        layer = self._layers.get(ProxyLayerId.DESKTOP)
        if layer is None or not layer.is_available():
            return None
        current = layer.read()
        if current is None:
            return None

        imported = replace(current, id=str(uuid4()), name=name)
        if imported.mode is ProxyMode.NONE:
            # Hệ thống đang tắt proxy: vẫn giữ ignore-hosts để người dùng không
            # phải gõ lại, nhưng cấu hình sinh ra là "manual chưa điền".
            imported.mode = ProxyMode.MANUAL

        password = getattr(layer, "read_password", lambda: "")()
        self._configs.append(imported)
        if imported.auth_enabled and password:
            self._secrets.save_password(imported.id, password)
        self._persist()
        return imported

    def export_snippet(self) -> str:
        """Đoạn `export ...` cho shell đang mở (FR-P6)."""
        layer = self._layers.get(ProxyLayerId.ENVIRONMENT)
        if layer is None or not hasattr(layer, "export_snippet"):
            return ""
        config = self.active
        return layer.export_snippet(config) if config else layer.export_snippet(ProxyConfig())

    def _persist(self) -> None:
        self._store.save(self._configs, self._active_id)
