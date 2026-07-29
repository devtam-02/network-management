"""Logic ghi/xoá của lớp proxy desktop, với Gio.Settings giả.

Không đụng vào dconf thật: chỉ kiểm CHÍNH SÁCH — khoá nào bị đổi khi bật, khi
tắt, khi chuyển chế độ. Việc nói chuyện với dconf thật đã có
`tests/integration/test_gsettings_layer.py` (chỉ đọc).
"""

from __future__ import annotations

import pytest

from netmgr.domain.models import ProxyConfig, ProxyEndpoint, ProxyMode


class FakeSettings:
    """Đủ giống Gio.Settings cho phần lớp này dùng."""

    def __init__(self, **initial) -> None:
        self.data = dict(initial)

    def set_string(self, key, value): self.data[key] = value
    def set_int(self, key, value): self.data[key] = value
    def set_boolean(self, key, value): self.data[key] = value
    def set_strv(self, key, value): self.data[key] = list(value)
    def get_string(self, key): return self.data.get(key, "")


@pytest.fixture
def layer(monkeypatch):
    gi = pytest.importorskip("gi")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from netmgr.infra.proxy.gsettings_layer import GSettingsLayer

    monkeypatch.setattr(Gio.Settings, "sync", staticmethod(lambda: None))

    obj = GSettingsLayer()
    obj._root = FakeSettings(mode="none", autoconfig_url="")
    obj._children = {s: FakeSettings() for s in ("http", "https", "ftp", "socks")}
    # `_settings()` chỉ tạo mới khi _root là None, nên bản giả được giữ nguyên.
    return obj


def manual(host="10.0.0.1", port=3128) -> ProxyConfig:
    return ProxyConfig(id="m", name="M", mode=ProxyMode.MANUAL,
                       http=ProxyEndpoint(host, port))


def auto(url="file:///tmp/x.pac") -> ProxyConfig:
    return ProxyConfig(id="a", name="A", mode=ProxyMode.AUTO, pac_url=url)


# ── tắt proxy ────────────────────────────────────────────────────────────────


def test_tat_chi_doi_mode_giu_nguyen_host_port(layer):
    """Xoá sạch các trường khi tắt làm Cài đặt Ubuntu hiện ô trống trơn, trông
    như cấu hình đã mất."""
    layer.apply(manual())
    assert layer._children["http"].data["host"] == "10.0.0.1"

    assert layer.clear().ok
    assert layer._root.data["mode"] == "none"
    assert layer._children["http"].data["host"] == "10.0.0.1"
    assert layer._children["http"].data["port"] == 3128


def test_tat_giu_nguyen_autoconfig_url(layer):
    layer.apply(auto())
    layer.clear()
    assert layer._root.data["mode"] == "none"
    assert layer._root.data["autoconfig-url"] == "file:///tmp/x.pac"


def test_tat_van_xoa_mat_khau(layer):
    """Mật khẩu là bí mật: không có lý do nằm trong dconf khi proxy đang tắt."""
    config = manual()
    config.auth_enabled = True
    config.username = "tam"
    layer.apply(config, password="bimat")
    assert layer._children["http"].data["authentication-password"] == "bimat"

    layer.clear()
    assert layer._children["http"].data["authentication-password"] == ""


# ── chuyển chế độ ────────────────────────────────────────────────────────────


def test_chuyen_sang_auto_thi_don_host_port_cu(layer):
    """Để lại host/port của lần MANUAL trước làm cấu hình trông như lẫn hai chế độ."""
    layer.apply(manual())
    layer.apply(auto())
    assert layer._root.data["autoconfig-url"] == "file:///tmp/x.pac"
    assert layer._children["http"].data["host"] == ""


def test_chuyen_sang_manual_thi_don_autoconfig_url(layer):
    layer.apply(auto())
    layer.apply(manual("10.0.0.9", 8080))
    assert layer._root.data["autoconfig-url"] == ""
    assert layer._children["http"].data["host"] == "10.0.0.9"


def test_mode_duoc_dat_sau_cung(layer):
    """Đặt mode trước sẽ có khoảnh khắc proxy bật với host/port của lần trước."""
    order = []
    root = layer._root
    original = root.set_string

    def spy(key, value):
        order.append(key)
        original(key, value)

    root.set_string = spy
    layer.apply(manual())
    assert order[-1] == "mode"
