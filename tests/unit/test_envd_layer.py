"""Test lớp proxy L2 (environment.d) — chỉ file IO, không cần gi."""

from __future__ import annotations

import pytest

from netmgr.domain.models import ProxyConfig, ProxyEndpoint, ProxyMode
from netmgr.infra.proxy.envd_layer import (
    FILENAME,
    EnvdLayer,
    build_env,
    build_no_proxy,
    ignore_host_to_no_proxy,
)


@pytest.fixture
def layer(tmp_path) -> EnvdLayer:
    return EnvdLayer(directory=tmp_path / "environment.d")


def manual(**kw) -> ProxyConfig:
    kw.setdefault("name", "Test")
    kw.setdefault("mode", ProxyMode.MANUAL)
    kw.setdefault("http", ProxyEndpoint("10.0.0.8", 3128))
    return ProxyConfig(**kw)


# ── chuyển ignore-hosts sang no_proxy ────────────────────────────────────────


@pytest.mark.parametrize(
    "entry,expected",
    [
        ("localhost", "localhost"),
        ("example.com", "example.com"),
        ("*.viettel.vn", ".viettel.vn"),
        ("127.0.0.0/8", "127.0.0.0/8"),
        ("192.168.1.1", "192.168.1.1"),
        ("10.*.*.*", "10.0.0.0/8"),
        ("192.168.*.*", "192.168.0.0/16"),
        ("10.1.2.*", "10.1.2.0/24"),
    ],
)
def test_ignore_host_conversion(entry, expected):
    """GNOME dùng cú pháp riêng mà curl/git không hiểu — không dịch thì proxy
    sẽ nuốt cả traffic nội bộ."""
    assert ignore_host_to_no_proxy(entry) == expected


@pytest.mark.parametrize("entry", ["", "   ", "*", "a*b.com"])
def test_untranslatable_entries_dropped(entry):
    assert ignore_host_to_no_proxy(entry) is None


def test_build_no_proxy_joins_and_dedupes():
    out = build_no_proxy(["localhost", "localhost", "*.viettel.vn", "*"])
    assert out == "localhost,.viettel.vn"


def test_build_no_proxy_on_real_gnome_value():
    """Giá trị thật lấy từ máy phát triển."""
    out = build_no_proxy(
        ["localhost", "127.0.0.0/8", "::1", "*.viettel.vn", "*.digital.vn", "10.*.*.*"]
    )
    assert out == "localhost,127.0.0.0/8,::1,.viettel.vn,.digital.vn,10.0.0.0/8"


# ── build_env ────────────────────────────────────────────────────────────────


def test_env_sets_both_cases():
    """Công cụ khác nhau đọc chữ thường hoặc chữ hoa — phải đặt cả hai."""
    env = build_env(manual())
    assert env["http_proxy"] == "http://10.0.0.8:3128"
    assert env["HTTP_PROXY"] == "http://10.0.0.8:3128"


def test_use_same_for_all_propagates_to_https():
    env = build_env(manual(use_same_for_all=True))
    assert env["https_proxy"] == "http://10.0.0.8:3128"


def test_per_scheme_endpoints_respected():
    env = build_env(
        manual(
            use_same_for_all=False,
            https=ProxyEndpoint("10.0.0.9", 3129),
        )
    )
    assert env["http_proxy"] == "http://10.0.0.8:3128"
    assert env["https_proxy"] == "http://10.0.0.9:3129"


def test_socks_uses_socks5_scheme():
    env = build_env(manual(use_same_for_all=False, socks=ProxyEndpoint("10.0.0.9", 1080)))
    assert env["all_proxy"] == "socks5://10.0.0.9:1080"


def test_all_proxy_absent_when_only_http_proxy_configured():
    """Quảng cáo một HTTP proxy dưới dạng socks5:// sẽ làm hỏng mọi công cụ
    đọc all_proxy — bug thật gặp khi chạy thử với proxy 10.254.148.131:8800."""
    env = build_env(manual(use_same_for_all=True))
    assert "all_proxy" not in env
    assert "ALL_PROXY" not in env


def test_none_mode_produces_no_env():
    assert build_env(ProxyConfig(mode=ProxyMode.NONE)) == {}


def test_pac_mode_produces_no_env():
    """Không lớp nào ngoài trình duyệt hiểu file PAC."""
    assert build_env(ProxyConfig(mode=ProxyMode.AUTO, pac_url="http://x/p.pac")) == {}


def test_credentials_never_embedded_in_url():
    """user:pass trong URL sẽ lộ ra trong `ps` và biến môi trường tiến trình con."""
    env = build_env(manual(auth_enabled=True, username="tam"))
    assert all("tam" not in v and "@" not in v for v in env.values())


def test_no_proxy_included():
    env = build_env(manual(ignore_hosts=["localhost", "*.viettel.vn"]))
    assert env["no_proxy"] == "localhost,.viettel.vn"


# ── apply / read / clear ─────────────────────────────────────────────────────


def test_apply_creates_file_with_restrictive_mode(layer):
    layer.apply(manual())
    assert layer.path.exists()
    assert layer.path.name == FILENAME
    assert oct(layer.path.stat().st_mode)[-3:] == "600"


def test_apply_creates_parent_directory(layer):
    assert not layer.directory.exists()
    assert layer.apply(manual()).ok
    assert layer.directory.exists()


def test_apply_warns_about_new_terminal(layer):
    result = layer.apply(manual())
    assert result.ok
    assert "terminal mới" in result.message


def test_file_marked_as_generated(layer):
    layer.apply(manual())
    assert "SỬA TAY SẼ BỊ GHI ĐÈ" in layer.path.read_text()


def test_apply_none_mode_removes_file(layer):
    layer.apply(manual())
    layer.apply(ProxyConfig(mode=ProxyMode.NONE))
    assert not layer.path.exists()


def test_clear_removes_file(layer):
    layer.apply(manual())
    assert layer.clear().ok
    assert not layer.path.exists()


def test_clear_is_idempotent(layer):
    assert layer.clear().ok
    assert layer.clear().ok


def test_read_without_file_reports_disabled(layer):
    config = layer.read()
    assert config is not None and config.mode is ProxyMode.NONE


def test_read_after_apply_roundtrips_endpoint(layer):
    layer.apply(manual())
    config = layer.read()
    assert config.mode is ProxyMode.MANUAL
    assert config.http == ProxyEndpoint("10.0.0.8", 3128)


def test_read_after_apply_roundtrips_no_proxy(layer):
    layer.apply(manual(ignore_hosts=["localhost", "*.viettel.vn"]))
    assert layer.read().ignore_hosts == ["localhost", ".viettel.vn"]


def test_apply_overwrites_previous_content(layer):
    layer.apply(manual())
    layer.apply(manual(http=ProxyEndpoint("10.0.0.99", 8080)))
    assert layer.read().http.port == 8080
    assert "3128" not in layer.path.read_text()


def test_status_reflects_active_state(layer):
    assert layer.status().active is False
    layer.apply(manual())
    assert layer.status().active is True


def test_status_notes_delayed_effect(layer):
    assert "terminal" in layer.status().note


# ── export snippet ───────────────────────────────────────────────────────────


def test_export_snippet_has_export_lines(layer):
    snippet = layer.export_snippet(manual())
    assert "export http_proxy=http://10.0.0.8:3128" in snippet


def test_export_snippet_unsets_when_disabled(layer):
    snippet = layer.export_snippet(ProxyConfig(mode=ProxyMode.NONE))
    assert snippet.startswith("unset ")
    assert "http_proxy" in snippet and "NO_PROXY" in snippet
