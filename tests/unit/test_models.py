"""Test cho model — chủ yếu là các property dẫn xuất mà UI phụ thuộc vào."""

from __future__ import annotations

import pytest

from netmgr.domain.models import (
    Binding,
    BindingAction,
    ConnType,
    DeviceInfo,
    DeviceState,
    Ipv4Config,
    Ipv4Method,
    Ipv4Route,
    PermissionState,
    Permissions,
    Profile,
    ProxyConfig,
    ProxyEndpoint,
    ProxyMode,
    RouteSource,
    WifiStatus,
)


# ── Ipv4Route ────────────────────────────────────────────────────────────────


def test_route_str_with_gateway():
    r = Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1", metric=100)
    assert str(r) == "10.20.0.0/16 via 192.168.1.1 metric 100"


def test_route_str_onlink():
    assert str(Ipv4Route("10.20.0.0", 16)) == "10.20.0.0/16 (on-link)"


def test_route_str_includes_table():
    r = Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1", table=100)
    assert "table 100" in str(r)


def test_route_identity_treats_missing_table_as_zero():
    assert Ipv4Route("10.0.0.0", 8).identity() == Ipv4Route("10.0.0.0", 8, table=0).identity()


def test_route_is_default_only_for_prefix_zero():
    assert Ipv4Route("0.0.0.0", 0).is_default
    assert not Ipv4Route("10.0.0.0", 8).is_default


@pytest.mark.parametrize(
    "source,editable",
    [(RouteSource.STATIC, True), (RouteSource.DHCP, False), (RouteSource.KERNEL, False)],
)
def test_only_static_routes_are_editable(source, editable):
    assert Ipv4Route("10.0.0.0", 8, source=source).editable is editable


def test_route_is_hashable_and_frozen():
    r = Ipv4Route("10.0.0.0", 8)
    assert {r, r} == {r}
    with pytest.raises(Exception):
        r.dest = "x"      # type: ignore[misc]


# ── Ipv4Config ───────────────────────────────────────────────────────────────


def test_static_routes_filters_out_dhcp():
    cfg = Ipv4Config(
        routes=[
            Ipv4Route("10.0.0.0", 8),
            Ipv4Route("0.0.0.0", 0, source=RouteSource.DHCP),
        ]
    )
    assert len(cfg.static_routes) == 1


def test_config_copy_is_independent():
    cfg = Ipv4Config(routes=[Ipv4Route("10.0.0.0", 8)], dns=["1.1.1.1"])
    clone = cfg.copy()
    clone.routes.append(Ipv4Route("172.16.0.0", 12))
    clone.dns.append("8.8.8.8")
    assert len(cfg.routes) == 1 and len(cfg.dns) == 1


def test_automatic_mode_flag():
    assert Ipv4Method.AUTO.is_automatic
    assert not Ipv4Method.MANUAL.is_automatic


# ── WifiStatus ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "freq,band",
    [(2412, "2.4 GHz"), (5180, "5 GHz"), (6115, "6 GHz"), (0, "")],
)
def test_wifi_band_from_frequency(freq, band):
    assert WifiStatus(frequency_mhz=freq).band == band


@pytest.mark.parametrize(
    "strength,bars", [(0, 0), (10, 1), (40, 2), (60, 3), (95, 4), (100, 4)]
)
def test_wifi_signal_bars(strength, bars):
    assert WifiStatus(strength=strength).signal_bars == bars


# ── DeviceInfo ───────────────────────────────────────────────────────────────


def test_device_is_connected():
    assert DeviceInfo("enp3s0", ConnType.ETHERNET, DeviceState.CONNECTED).is_connected
    assert not DeviceInfo("enp3s0", ConnType.ETHERNET, DeviceState.DISCONNECTED).is_connected


def test_device_state_busy():
    assert DeviceState.CONNECTING.is_busy
    assert DeviceState.DEACTIVATING.is_busy
    assert not DeviceState.CONNECTED.is_busy


# ── Binding (F6) ─────────────────────────────────────────────────────────────


def test_binding_matches_exact_interface():
    dev = DeviceInfo("enp3s0", ConnType.ETHERNET)
    assert Binding("enp3s0").matches(dev)
    assert not Binding("enp4s0").matches(dev)


def test_binding_matches_wildcards_by_type():
    eth = DeviceInfo("enp3s0", ConnType.ETHERNET)
    wifi = DeviceInfo("wlp2s0", ConnType.WIFI)
    assert Binding("any-ethernet").matches(eth)
    assert not Binding("any-ethernet").matches(wifi)
    assert Binding("any-wifi").matches(wifi)


def test_binding_defaults_are_safe():
    """Mặc định phải là 'không đụng tới' và 'không bắt buộc' — §3.5.2."""
    b = Binding("enp3s0")
    assert b.action is BindingAction.LEAVE_ALONE
    assert b.required is False
    assert b.owned is False


# ── Profile (F6) ─────────────────────────────────────────────────────────────


def test_profile_label_combines_icon_and_name():
    assert Profile(id="1", name="Công ty", icon="🏢").label == "🏢 Công ty"


def test_profile_broken_flag_follows_reason():
    p = Profile(id="1", name="X")
    assert not p.is_broken
    p.broken_reason = "connection không còn tồn tại"
    assert p.is_broken


# ── Permissions (§4.6) ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,allowed",
    [
        (PermissionState.YES, True),
        (PermissionState.AUTH, True),
        (PermissionState.NO, False),
        (PermissionState.UNKNOWN, False),
    ],
)
def test_permission_allowed(state, allowed):
    assert state.allowed is allowed


@pytest.mark.parametrize(
    "state,blocks",
    [
        (PermissionState.YES, False),
        (PermissionState.AUTH, False),
        (PermissionState.NO, True),
        (PermissionState.UNKNOWN, False),
    ],
)
def test_only_explicit_no_blocks_ui(state, blocks):
    """UNKNOWN không được chặn UI — NM nạp permission bất đồng bộ."""
    assert state.should_block_ui is blocks


def test_fresh_permissions_do_not_disable_ui():
    """Ngay sau khi tạo client mọi permission là UNKNOWN; UI vẫn phải dùng được."""
    fresh = Permissions()
    assert not fresh.loaded
    assert fresh.can_edit_connections
    assert fresh.can_control_network
    assert fresh.can_toggle_wifi


def test_denied_permission_disables_ui():
    p = Permissions(modify_system=PermissionState.NO)
    assert p.loaded
    assert not p.can_edit_connections


def test_auth_permission_flags_prompt():
    assert PermissionState.AUTH.needs_auth_prompt
    assert not PermissionState.YES.needs_auth_prompt


# ── ProxyConfig ──────────────────────────────────────────────────────────────


def test_proxy_enabled_flag():
    assert not ProxyConfig(mode=ProxyMode.NONE).is_enabled
    assert ProxyConfig(mode=ProxyMode.MANUAL).is_enabled


@pytest.mark.parametrize("scheme", ["http", "https", "ftp"])
def test_use_same_for_all_covers_http_family(scheme):
    cfg = ProxyConfig(
        mode=ProxyMode.MANUAL,
        http=ProxyEndpoint("10.0.0.8", 3128),
        use_same_for_all=True,
    )
    assert cfg.effective(scheme).port == 3128


def test_use_same_for_all_never_derives_socks_from_http():
    """SOCKS là giao thức khác — gửi traffic SOCKS5 tới cổng HTTP proxy sẽ hỏng."""
    cfg = ProxyConfig(
        mode=ProxyMode.MANUAL,
        http=ProxyEndpoint("10.0.0.8", 3128),
        use_same_for_all=True,
    )
    assert not cfg.effective("socks").is_set


def test_explicit_socks_used_even_with_use_same_for_all():
    cfg = ProxyConfig(
        mode=ProxyMode.MANUAL,
        http=ProxyEndpoint("10.0.0.8", 3128),
        socks=ProxyEndpoint("10.0.0.9", 1080),
        use_same_for_all=True,
    )
    assert cfg.effective("socks").port == 1080


def test_proxy_per_scheme_when_not_shared():
    cfg = ProxyConfig(
        mode=ProxyMode.MANUAL,
        http=ProxyEndpoint("10.0.0.8", 3128),
        socks=ProxyEndpoint("10.0.0.9", 1080),
        use_same_for_all=False,
    )
    assert cfg.effective("socks").port == 1080


def test_proxy_summary_by_mode():
    assert ProxyConfig(mode=ProxyMode.NONE).summary() == "Tắt"
    assert "3128" in ProxyConfig(
        mode=ProxyMode.MANUAL, http=ProxyEndpoint("10.0.0.8", 3128)
    ).summary()
    assert "p.pac" in ProxyConfig(mode=ProxyMode.AUTO, pac_url="http://x/p.pac").summary()


def test_proxy_default_ignore_hosts_not_shared_between_instances():
    a, b = ProxyConfig(), ProxyConfig()
    a.ignore_hosts.append("10.0.0.0/8")
    assert "10.0.0.0/8" not in b.ignore_hosts
