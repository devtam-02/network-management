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
from netmgr.infra.proxy_store import BUILTIN_ID
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


def user_configs(service):
    """Cấu hình do NGƯỜI DÙNG tạo — bỏ "Cấu hình mặc định" app luôn tạo sẵn."""
    return [c for c in service.configs if not c.builtin]


def test_starts_with_only_builtin(service):
    """App luôn có sẵn một cấu hình proxy dùng được, nhưng KHÔNG tự bật."""
    assert user_configs(service) == []
    assert [c.id for c in service.configs] == [BUILTIN_ID]
    assert service.is_enabled is False
    assert service.active is None
    assert service.summary() is None


def test_save_adds_config(service):
    assert service.save(manual()).ok
    assert len(user_configs(service)) == 1


def test_save_rejects_invalid_config(service):
    invalid = new_config("", mode=ProxyMode.MANUAL)      # thiếu tên và endpoint
    assert not service.save(invalid).ok
    assert user_configs(service) == []


def test_save_updates_existing_by_id(service):
    cfg = manual()
    service.save(cfg)
    cfg.name = "Đổi tên"
    service.save(cfg)
    assert len(user_configs(service)) == 1
    assert user_configs(service)[0].name == "Đổi tên"


def test_config_persists_across_reload(service):
    service.save(manual())
    service.reload()
    assert len(user_configs(service)) == 1


def test_delete_removes_config(service):
    cfg = manual()
    service.save(cfg)
    assert service.delete(cfg.id).ok
    assert user_configs(service) == []


def test_delete_unknown_id_fails(service):
    assert not service.delete("không-có").ok


def test_duplicate_creates_new_id(service):
    cfg = manual()
    service.save(cfg)
    clone = service.duplicate(cfg.id, "Bản sao")
    assert clone.id != cfg.id
    assert clone.name == "Bản sao"
    assert len(user_configs(service)) == 2


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


def test_toggle_khi_chua_tao_gi_thi_bat_cau_hinh_mac_dinh(service, desktop):
    """Người dùng chưa tạo cấu hình nào vẫn bật được proxy — đó là lý do có
    "Cấu hình mặc định"."""
    report = service.toggle()
    assert report is not None and report.ok
    assert service.active.id == BUILTIN_ID


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


# ── "Cấu hình mặc định" có sẵn của app ───────────────────────────────────────


def test_cau_hinh_mac_dinh_la_pac_tro_vao_file_trong_du_an(service):
    from netmgr.domain.models import ProxyMode

    config = service.get(BUILTIN_ID)
    assert config.mode is ProxyMode.AUTO
    assert config.pac_url.startswith("file://")
    assert config.pac_path.endswith("data/pac/default.pac")


def test_khong_xoa_duoc_cau_hinh_mac_dinh(service):
    result = service.delete(BUILTIN_ID)
    assert not result.ok
    assert "không xoá được" in result.message
    assert service.get(BUILTIN_ID) is not None


def test_khong_tao_lai_khi_da_co(service):
    """Nạp lại nhiều lần không được sinh ra nhiều bản."""
    service.reload()
    service.reload()
    assert len([c for c in service.configs if c.builtin]) == 1


def test_doc_va_ghi_duoc_noi_dung_pac(service, tmp_path, monkeypatch):
    pac = tmp_path / "x.pac"
    config = service.get(BUILTIN_ID)
    config.pac_file = str(pac)

    body = "function FindProxyForURL(url, host) { return \"DIRECT\"; }"
    assert service.save_pac_content(BUILTIN_ID, body).ok
    assert service.pac_content(BUILTIN_ID) == body


def test_tu_choi_pac_khong_co_ham_bat_buoc(service, tmp_path):
    """PAC thiếu FindProxyForURL sẽ bị mọi ứng dụng lặng lẽ bỏ qua."""
    config = service.get(BUILTIN_ID)
    config.pac_file = str(tmp_path / "x.pac")

    result = service.save_pac_content(BUILTIN_ID, "var a = 1;")
    assert not result.ok
    assert "FindProxyForURL" in result.message


def test_tu_choi_pac_rong(service, tmp_path):
    config = service.get(BUILTIN_ID)
    config.pac_file = str(tmp_path / "x.pac")
    assert not service.save_pac_content(BUILTIN_ID, "   ").ok


# ── bắt lỗi PAC thất bại im lặng ─────────────────────────────────────────────


def test_tu_choi_isinnet_voi_ten_mien(service, tmp_path):
    """Đo trên máy thật: isInNet(host, "*.viettel.com.vn", "255.255.255.0") làm
    google.com đi qua proxy còn vas.viettel.com.vn đi thẳng — ngược hoàn toàn, và
    không có thông báo lỗi ở bất kỳ đâu."""
    service.get(BUILTIN_ID).pac_file = str(tmp_path / "x.pac")
    body = (
        'function FindProxyForURL(url, host) {\n'
        '    if (isInNet(host, "*.viettel.com.vn", "255.255.255.0")) {\n'
        '        return "PROXY 10.254.148.131:8800";\n'
        '    }\n'
        '    return "DIRECT";\n'
        '}\n'
    )
    result = service.save_pac_content(BUILTIN_ID, body)
    assert not result.ok
    assert "dnsDomainIs" in result.message
    assert "Dòng 2" in result.message


def test_chap_nhan_isinnet_voi_dai_ip(service, tmp_path):
    service.get(BUILTIN_ID).pac_file = str(tmp_path / "x.pac")
    body = (
        'function FindProxyForURL(url, host) {\n'
        '    if (isInNet(dnsResolve(host), "10.0.0.0", "255.0.0.0")) {\n'
        '        return "DIRECT";\n'
        '    }\n'
        '    return "DIRECT";\n'
        '}\n'
    )
    assert service.save_pac_content(BUILTIN_ID, body).ok


def test_bo_qua_isinnet_trong_comment(service, tmp_path):
    """Chú thích hướng dẫn có nêu ví dụ sai — không được coi là lỗi."""
    service.get(BUILTIN_ID).pac_file = str(tmp_path / "x.pac")
    body = (
        '// sai: isInNet(host, "*.example.com", "255.255.255.0")\n'
        'function FindProxyForURL(url, host) { return "DIRECT"; }\n'
    )
    assert service.save_pac_content(BUILTIN_ID, body).ok


def test_khong_bao_gio_ghi_vao_file_pac_that_khi_test(service, tmp_path):
    """Chốt lại một lỗi đã thực sự xảy ra: `pac_path` ưu tiên `pac_file`, nên
    test chỉ đổi `pac_url` sang tmp vẫn ghi thẳng vào file PAC của dự án và xoá
    mất nội dung người dùng đang dùng."""
    from netmgr.infra.proxy_store import project_pac_path

    config = service.get(BUILTIN_ID)
    config.pac_url = (tmp_path / "khong-dung.pac").as_uri()
    # pac_file vẫn trỏ vào dự án -> pac_path phải theo pac_file, không theo url
    assert config.pac_path == str(project_pac_path())
