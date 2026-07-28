"""CRUD Bộ cấu hình và điều phối việc áp dụng (F6).

Không import `gi`: mọi thứ chạm vào mạng đi qua đối tượng `network` được tiêm.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from uuid import uuid4

from ..domain.models import (
    Binding,
    BindingAction,
    ConnType,
    OpResult,
    Profile,
    ProfileApplyReport,
)
from .profile_applier import ProfileApplier

log = logging.getLogger(__name__)


class ProfileService:
    def __init__(self, network, proxy, store, *, applier: ProfileApplier | None = None):
        self._network = network
        self._proxy = proxy
        self._store = store
        self._applier = applier or ProfileApplier(network, proxy)
        self._profiles: list[Profile] = []
        self._active_id: str | None = None
        self.reload()

    # ── trạng thái ──────────────────────────────────────────────────────────

    def reload(self) -> None:
        self._profiles = self._store.load()
        self._active_id = self._store.load_active_id()
        if self._active_id and self.get(self._active_id) is None:
            self._active_id = None
        self._refresh_broken_flags()

    @property
    def profiles(self) -> list[Profile]:
        return list(self._profiles)

    @property
    def active(self) -> Profile | None:
        return self.get(self._active_id) if self._active_id else None

    @property
    def busy(self) -> bool:
        return self._applier.busy

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self._profiles if p.id == profile_id), None)

    def _refresh_broken_flags(self) -> None:
        """Đánh dấu Bộ cấu hình trỏ tới thứ không còn tồn tại.

        Tính lại mỗi lần reload thay vì lưu xuống file: connection có thể bị xoá
        từ bên ngoài app bất cứ lúc nào.
        """
        snapshot = self._network.snapshot()
        for profile in self._profiles:
            profile.broken_reason = self._find_break(profile, snapshot)

    def _find_break(self, profile: Profile, snapshot) -> str | None:
        for binding in profile.bindings:
            if binding.action is not BindingAction.ACTIVATE:
                continue
            if binding.connection_uuid and \
                    snapshot.connection_by_uuid(binding.connection_uuid) is None:
                return f"Cấu hình kết nối của '{binding.device_match}' đã bị xoá"
        if profile.touches_proxy and not profile.disables_proxy:
            if self._proxy.get(profile.proxy_id) is None:
                return "Cấu hình proxy đã bị xoá"
        return None

    # ── CRUD ────────────────────────────────────────────────────────────────

    def save(self, profile: Profile) -> OpResult:
        if not profile.name.strip():
            return OpResult(False, "Tên Bộ cấu hình không được để trống")

        existing = self.get(profile.id)
        if existing is None:
            profile.order = len(self._profiles)
            self._profiles.append(profile)
        else:
            self._profiles[self._profiles.index(existing)] = profile

        self._persist()
        return OpResult.success()

    def delete(self, profile_id: str) -> OpResult:
        profile = self.get(profile_id)
        if profile is None:
            return OpResult(False, "Không tìm thấy Bộ cấu hình")

        self._profiles.remove(profile)
        if self._active_id == profile_id:
            # Chỉ bỏ đánh dấu active, KHÔNG hoàn tác cấu hình mạng: người dùng
            # xoá một bối cảnh chứ không yêu cầu ngắt mạng đang dùng.
            self._active_id = None
        self._persist()
        return OpResult.success()

    def duplicate(self, profile_id: str, new_name: str) -> Profile | None:
        source = self.get(profile_id)
        if source is None:
            return None
        clone = replace(
            source,
            id=str(uuid4()),
            name=new_name,
            order=len(self._profiles),
            bindings=[replace(b) for b in source.bindings],
            broken_reason=None,
        )
        self._profiles.append(clone)
        self._persist()
        return clone

    def reorder(self, profile_id: str, delta: int) -> None:
        profile = self.get(profile_id)
        if profile is None:
            return
        index = self._profiles.index(profile)
        target = max(0, min(len(self._profiles) - 1, index + delta))
        if target == index:
            return
        self._profiles.insert(target, self._profiles.pop(index))
        for position, item in enumerate(self._profiles):
            item.order = position
        self._persist()

    # ── tạo từ trạng thái hiện tại (FR-PR4) ─────────────────────────────────

    def capture_current(self, name: str) -> Profile:
        """Chụp trạng thái mạng + proxy hiện tại thành một Bộ cấu hình.

        Đây là cách tạo Bộ cấu hình tự nhiên nhất: người dùng cấu hình tay cho
        chạy được rồi mới lưu lại, thay vì phải hình dung trước mọi thiết lập.
        """
        snapshot = self._network.snapshot()
        bindings: list[Binding] = []

        for device in snapshot.devices:
            uuid = device.active_connection_uuid
            if uuid:
                bindings.append(
                    Binding(
                        device_match=device.interface,
                        action=BindingAction.ACTIVATE,
                        connection_uuid=uuid,
                        # Thiết bị USB/dock có thể vắng mặt ở lần áp dụng sau;
                        # mặc định không bắt buộc để không báo lỗi vô cớ.
                        required=False,
                    )
                )
            else:
                bindings.append(
                    Binding(device_match=device.interface, action=BindingAction.DISCONNECT)
                )

        active_proxy = self._proxy.active
        profile = Profile(
            id=str(uuid4()),
            name=name,
            bindings=bindings,
            wifi_enabled=snapshot.wifi_enabled if snapshot.has_wifi_hardware else None,
            proxy_id=active_proxy.id if active_proxy else "",
            order=len(self._profiles),
        )
        self._profiles.append(profile)
        self._persist()
        return profile

    # ── áp dụng ─────────────────────────────────────────────────────────────

    def apply(self, profile_id: str, *, on_done, on_progress=None) -> None:
        profile = self.get(profile_id)
        if profile is None:
            report = ProfileApplyReport("")
            on_done(report)
            return

        def finished(report: ProfileApplyReport) -> None:
            if report.ok:
                self._active_id = profile.id
                self._persist()
            on_done(report)

        self._applier.apply(profile, on_done=finished, on_progress=on_progress)

    def clear_active(self) -> OpResult:
        """"Không dùng Bộ cấu hình" (FR-PR9) — trả máy về cấu hình gốc.

        Route của Bộ cấu hình chỉ tồn tại ở runtime, nên khôi phục = reapply
        connection đã lưu. Kết nối nào đang chạy thì vẫn chạy: người dùng chỉ
        nói "thôi không dùng bối cảnh nữa", không nói "ngắt mạng".
        """
        previous = self.active
        self._active_id = None
        self._persist()

        interfaces = self._touched_interfaces(previous)
        for iface in interfaces:
            self._network.restore_runtime(iface, lambda _r: None)

        if interfaces:
            return OpResult.success(
                f"Đã trả {len(interfaces)} thiết bị về cấu hình gốc của máy"
            )
        return OpResult.success("Đã bỏ Bộ cấu hình")

    def restore_all(self) -> None:
        """Trả mọi thiết bị về cấu hình gốc — gọi khi thoát app."""
        for iface in self._touched_interfaces(self.active):
            self._network.restore_runtime(iface, lambda _r: None)

    def _touched_interfaces(self, profile: Profile | None) -> list[str]:
        """Interface mà Bộ cấu hình này có đặt lại bảng route.

        Dùng chung `Profile.routing_bindings` với lúc áp dụng. Trước đây hàm này
        tự lọc theo `binding.routes` nên bỏ sót thiết bị được đưa về Automatic:
        áp dụng thì đổi runtime, mà "Không dùng Bộ cấu hình" lại không khôi phục.
        """
        if profile is None:
            return []
        snapshot = self._network.snapshot()
        out: list[str] = []
        for binding in profile.routing_bindings:
            for device in snapshot.devices:
                if binding.matches(device) and device.interface not in out:
                    out.append(device.interface)
        return out

    # ── mô tả cho UI ────────────────────────────────────────────────────────

    def describe(self, profile: Profile) -> str:
        parts: list[str] = []
        snapshot = self._network.snapshot()

        on: list[str] = []
        off: list[str] = []
        for binding in profile.bindings:
            if binding.action is BindingAction.ACTIVATE:
                # Binding kiểu công tắc để trống connection_uuid — NetworkManager
                # tự chọn. Trước đây nhánh này đòi có uuid nên mọi Bộ cấu hình
                # đều bị mô tả là "Chưa cấu hình gì".
                if binding.connection_uuid:
                    conn = snapshot.connection_by_uuid(binding.connection_uuid)
                    on.append(
                        f"{binding.device_match} → "
                        f"{conn.display_name if conn else '?'}"
                    )
                else:
                    on.append(binding.device_match)
            elif binding.action is BindingAction.DISCONNECT:
                off.append(binding.device_match)

        if on:
            parts.append("Bật " + ", ".join(on))
        if off:
            parts.append("Tắt " + ", ".join(off))

        route_count = sum(len(b.routes) for b in profile.bindings)
        if route_count:
            parts.append(f"{route_count} route riêng")

        if profile.wifi_enabled is not None:
            parts.append("Wi-Fi " + ("bật" if profile.wifi_enabled else "tắt"))

        if profile.disables_proxy:
            parts.append("Proxy tắt")
        elif profile.touches_proxy:
            config = self._proxy.get(profile.proxy_id)
            # Dấu hai chấm để đọc được cả khi tên cấu hình bắt đầu bằng "Proxy"
            # ("Proxy Proxy Công ty" trông như lỗi).
            parts.append(f"Proxy: {config.name if config else '?'}")

        return " · ".join(parts) if parts else "Chưa cấu hình gì"

    def candidate_connections(self, interface: str):
        """Các connection có thể gán cho một interface."""
        snapshot = self._network.snapshot()
        device = snapshot.device_by_interface(interface)
        if device is None:
            return []
        wanted = ConnType.WIFI if device.type is ConnType.WIFI else ConnType.ETHERNET
        return [
            c
            for c in snapshot.connections
            if c.type is wanted
            and (c.interface_name in (None, interface) or c.type is ConnType.WIFI)
        ]

    def _persist(self) -> None:
        self._store.save(self._profiles, self._active_id)
