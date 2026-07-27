"""Test điều phối nhiều lớp proxy — dùng layer giả, không đụng hệ thống."""

from __future__ import annotations

import pytest

from netmgr.domain.models import (
    OpResult,
    ProxyConfig,
    ProxyEndpoint,
    ProxyLayerId,
    ProxyMode,
)
from netmgr.infra.proxy.base import LayerStatus, ProxyLayer
from netmgr.infra.proxy_store import ProxyStore, new_config
from netmgr.infra.secrets import NullSecretStore
from netmgr.services.proxy_service import ProxyService


class FakeLayer(ProxyLayer):
    def __init__(self, layer_id: ProxyLayerId, *, available=True, fails=False) -> None:
        self.id = layer_id
        self.name = f"fake-{layer_id.value}"
        self._available = available
        self._fails = fails
        self.calls: list[tuple[str, object]] = []
        self.current: ProxyConfig | None = None

    def is_available(self) -> bool:
        return self._available

    def apply(self, config, password=None):
        self.calls.append(("apply", (config.name, password)))
        if self._fails:
            return OpResult(False, f"{self.name} hỏng")
        self.current = config
        return OpResult.success()

    def clear(self):
        self.calls.append(("clear", None))
        if self._fails:
            return OpResult(False, f"{self.name} hỏng")
        self.current = None
        return OpResult.success()

    def read(self):
        return self.current

    def status(self):
        return LayerStatus(
            self.id, self.name, self._available,
            active=self.current is not None, summary="fake",
        )


def manual(name="Công ty") -> ProxyConfig:
    cfg = new_config(name, mode=ProxyMode.MANUAL)
    cfg.http = ProxyEndpoint("10.0.0.8", 3128)
    return cfg


@pytest.fixture
def desktop() -> FakeLayer:
    return FakeLayer(ProxyLayerId.DESKTOP)


@pytest.fixture
def env() -> FakeLayer:
    return FakeLayer(ProxyLayerId.ENVIRONMENT)


@pytest.fixture
def service(tmp_path, desktop, env) -> ProxyService:
    return ProxyService(
        layers=[desktop, env],
        store=ProxyStore(directory=tmp_path / "netmgr"),
        secrets=NullSecretStore(),
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_starts_empty(service):
    assert service.configs == []
    assert service.active is None
    assert service.summary() is None


def test_save_adds_config(service):
    assert service.save(manual()).ok
    assert len(service.configs) == 1


def test_save_rejects_invalid_config(service):
    invalid = new_config("", mode=ProxyMode.MANUAL)      # thiếu tên và endpoint
    assert not service.save(invalid).ok
    assert service.configs == []


def test_save_updates_existing_by_id(service):
    cfg = manual()
    service.save(cfg)
    cfg.name = "Đổi tên"
    service.save(cfg)
    assert len(service.configs) == 1
    assert service.configs[0].name == "Đổi tên"


def test_config_persists_across_reload(service):
    service.save(manual())
    service.reload()
    assert len(service.configs) == 1


def test_delete_removes_config(service):
    cfg = manual()
    service.save(cfg)
    assert service.delete(cfg.id).ok
    assert service.configs == []


def test_delete_unknown_id_fails(service):
    assert not service.delete("không-có").ok


def test_duplicate_creates_new_id(service):
    cfg = manual()
    service.save(cfg)
    clone = service.duplicate(cfg.id, "Bản sao")
    assert clone.id != cfg.id
    assert clone.name == "Bản sao"
    assert len(service.configs) == 2


def test_duplicate_does_not_share_mutable_state(service):
    cfg = manual()
    service.save(cfg)
    clone = service.duplicate(cfg.id, "Bản sao")
    clone.ignore_hosts.append("thêm.vn")
    assert "thêm.vn" not in cfg.ignore_hosts


# ── áp dụng ──────────────────────────────────────────────────────────────────


def test_activate_applies_to_all_selected_layers(service, desktop, env):
    cfg = manual()
    service.save(cfg)
    report = service.activate(cfg.id)

    assert report.ok
    assert ("apply", (cfg.name, None)) in desktop.calls
    assert ("apply", (cfg.name, None)) in env.calls


def test_activate_clears_layers_not_selected(service, desktop, env):
    """Lớp bị bỏ chọn phải TẮT, nếu không cấu hình cũ còn sót lại ở đó."""
    cfg = manual()
    cfg.layers = {ProxyLayerId.DESKTOP}
    service.save(cfg)
    service.activate(cfg.id)

    assert ("apply", (cfg.name, None)) in desktop.calls
    assert ("clear", None) in env.calls


def test_activate_sets_active_and_summary(service):
    cfg = manual()
    service.save(cfg)
    service.activate(cfg.id)
    assert service.active.id == cfg.id
    assert service.is_enabled
    assert "Công ty" in service.summary()


def test_activate_unknown_id_reports_failure(service):
    assert not service.activate("không-có").ok


def test_partial_failure_is_reported_per_layer(tmp_path, desktop):
    broken = FakeLayer(ProxyLayerId.ENVIRONMENT, fails=True)
    service = ProxyService(
        layers=[desktop, broken],
        store=ProxyStore(directory=tmp_path / "netmgr"),
        secrets=NullSecretStore(),
    )
    cfg = manual()
    service.save(cfg)
    report = service.activate(cfg.id)

    assert not report.ok
    assert report.failed_layers == [ProxyLayerId.ENVIRONMENT]
    assert report.results[ProxyLayerId.DESKTOP].ok       # lớp kia vẫn chạy


def test_unavailable_layer_is_skipped(tmp_path, desktop):
    absent = FakeLayer(ProxyLayerId.APT, available=False)
    service = ProxyService(
        layers=[desktop, absent],
        store=ProxyStore(directory=tmp_path / "netmgr"),
        secrets=NullSecretStore(),
    )
    cfg = manual()
    service.save(cfg)
    report = service.activate(cfg.id)

    assert report.ok
    assert absent.calls == []


def test_disable_clears_every_layer(service, desktop, env):
    cfg = manual()
    service.save(cfg)
    service.activate(cfg.id)
    service.disable()

    assert ("clear", None) in desktop.calls
    assert ("clear", None) in env.calls
    assert service.active is None
    assert not service.is_enabled


def test_active_id_survives_reload(service):
    cfg = manual()
    service.save(cfg)
    service.activate(cfg.id)
    service.reload()
    assert service.active is not None and service.active.id == cfg.id


def test_active_id_pointing_at_deleted_config_is_cleared(service, tmp_path):
    cfg = manual()
    service.save(cfg)
    service.activate(cfg.id)
    # Mô phỏng người dùng xoá tay khỏi file config
    service._store.save([], active_id=cfg.id)
    service.reload()
    assert service.active is None


# ── toggle ───────────────────────────────────────────────────────────────────


def test_toggle_enables_first_config_when_off(service):
    cfg = manual()
    service.save(cfg)
    service.toggle()
    assert service.is_enabled


def test_toggle_disables_when_on(service):
    cfg = manual()
    service.save(cfg)
    service.activate(cfg.id)
    service.toggle()
    assert not service.is_enabled


def test_toggle_remembers_last_used(service):
    a, b = manual("A"), manual("B")
    service.save(a)
    service.save(b)
    service.activate(b.id)
    service.toggle()                    # tắt
    service.toggle()                    # bật lại — phải là B, không phải A
    assert service.active.id == b.id


def test_toggle_without_configs_does_nothing(service):
    assert service.toggle() is None


# ── xoá cấu hình đang bật ────────────────────────────────────────────────────


def test_deleting_active_config_turns_proxy_off(service, desktop):
    """Không tắt trước khi xoá thì proxy chạy mồ côi, người dùng hết đường tắt."""
    cfg = manual()
    service.save(cfg)
    service.activate(cfg.id)
    service.delete(cfg.id)

    assert service.active is None
    assert desktop.calls[-1] == ("clear", None)


# ── mật khẩu ─────────────────────────────────────────────────────────────────


def test_password_saved_to_keyring_not_config(service):
    cfg = manual()
    cfg.auth_enabled = True
    cfg.username = "tam"
    service.save(cfg, password="bí-mật")

    assert service._secrets.load_password(cfg.id) == "bí-mật"
    assert "bí-mật" not in service._store.path.read_text()


def test_password_passed_to_layers_on_activate(service, desktop):
    cfg = manual()
    cfg.auth_enabled = True
    cfg.username = "tam"
    service.save(cfg, password="bí-mật")
    service.activate(cfg.id)

    assert ("apply", (cfg.name, "bí-mật")) in desktop.calls


def test_disabling_auth_removes_stored_password(service):
    cfg = manual()
    cfg.auth_enabled = True
    cfg.username = "tam"
    service.save(cfg, password="bí-mật")

    cfg.auth_enabled = False
    service.save(cfg)
    assert service._secrets.load_password(cfg.id) is None


def test_deleting_config_removes_password(service):
    cfg = manual()
    cfg.auth_enabled = True
    cfg.username = "tam"
    service.save(cfg, password="bí-mật")
    service.delete(cfg.id)
    assert service._secrets.load_password(cfg.id) is None


# ── nhập từ hệ thống ─────────────────────────────────────────────────────────


def test_import_current_preserves_ignore_hosts(service, desktop):
    """Không có tính năng này thì ignore-hosts người dùng đã gõ sẽ bị mất."""
    desktop.current = ProxyConfig(
        name="hệ thống",
        mode=ProxyMode.MANUAL,
        http=ProxyEndpoint("10.0.0.8", 3128),
        ignore_hosts=["localhost", "*.viettel.vn", "10.*.*.*"],
    )
    imported = service.import_current("Nhập về")

    assert imported is not None
    assert imported.ignore_hosts == ["localhost", "*.viettel.vn", "10.*.*.*"]
    assert imported.name == "Nhập về"
    assert imported in service.configs


def test_import_gets_fresh_id(service, desktop):
    desktop.current = ProxyConfig(id="id-cũ", name="hệ thống", mode=ProxyMode.MANUAL)
    assert service.import_current().id != "id-cũ"


def test_import_when_system_proxy_off_still_keeps_ignore_hosts(service, desktop):
    desktop.current = ProxyConfig(
        name="hệ thống", mode=ProxyMode.NONE, ignore_hosts=["*.viettel.vn"]
    )
    imported = service.import_current()
    assert imported.ignore_hosts == ["*.viettel.vn"]
    assert imported.mode is ProxyMode.MANUAL     # sẵn sàng để điền host/port


def test_import_without_desktop_layer_returns_none(tmp_path, env):
    service = ProxyService(
        layers=[env],
        store=ProxyStore(directory=tmp_path / "netmgr"),
        secrets=NullSecretStore(),
    )
    assert service.import_current() is None


# ── chẩn đoán ────────────────────────────────────────────────────────────────


def test_layer_statuses_cover_every_layer(service):
    statuses = service.layer_statuses()
    assert {s.layer for s in statuses} == {ProxyLayerId.DESKTOP, ProxyLayerId.ENVIRONMENT}
