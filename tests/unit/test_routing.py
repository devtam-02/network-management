"""Test mô phỏng chọn route của kernel — nền tảng của mạng nhiều đường."""

from __future__ import annotations

import pytest

from netmgr.domain.models import Ipv4Route, SystemRoute
from netmgr.domain.routing import (
    IssueLevel,
    analyze,
    default_route_owner,
    group_by_interface,
    lookup,
    sorted_table,
)


def R(iface, dest, prefix, *, via=None, metric=None, table=None) -> SystemRoute:
    return SystemRoute(
        iface, Ipv4Route(dest, prefix, next_hop=via, metric=metric, table=table)
    )


@pytest.fixture
def real_table():
    """Bảng route thật lấy từ máy phát triển — ethernet công ty + Wi-Fi."""
    return [
        R("enp1s0", "10.207.152.0", 22, metric=100),
        R("enp1s0", "10.0.0.0", 8, via="10.207.154.254", metric=100),
        R("tailscale0", "100.89.48.74", 32, metric=0, table=52),
        R("tailscale0", "100.100.100.100", 32, metric=0, table=52),
        R("br-1d594c364a72", "172.18.0.0", 16, metric=0),
        R("docker0", "172.17.0.0", 16, metric=0),
        R("wlx", "172.46.0.0", 16, metric=600),
        R("wlx", "0.0.0.0", 0, via="172.46.0.1", metric=600),
    ]


# ── tra cứu ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "dest,iface",
    [
        ("10.60.101.189", "enp1s0"),      # trong 10/8 → LAN công ty
        ("10.207.155.1", "enp1s0"),       # trong subnet trực tiếp
        ("8.8.8.8", "wlx"),               # không khớp gì → default
        ("1.1.1.1", "wlx"),
        ("172.46.0.5", "wlx"),            # subnet Wi-Fi
        ("172.17.0.2", "docker0"),        # container
        ("172.18.0.9", "br-1d594c364a72"),
    ],
)
def test_lookup_matches_real_machine(real_table, dest, iface):
    """Kết quả phải khớp với `ip route get` trên máy thật."""
    assert lookup(real_table, dest).interface == iface


def test_longest_prefix_wins(real_table):
    """10.207.155.1 nằm trong cả 10/8 lẫn 10.207.152.0/22 — cái /22 phải thắng."""
    result = lookup(real_table, "10.207.155.1")
    assert result.route.route.prefix == 22
    assert any(r.route.prefix == 8 for r in result.also_matched)


def test_lower_metric_wins_on_tie():
    routes = [
        R("eth0", "10.0.0.0", 8, via="192.168.1.1", metric=200),
        R("eth1", "10.0.0.0", 8, via="192.168.2.1", metric=50),
    ]
    assert lookup(routes, "10.5.5.5").interface == "eth1"


def test_other_routing_tables_ignored(real_table):
    """Route tailscale nằm ở table 52 — không được ảnh hưởng traffic thường."""
    result = lookup(real_table, "100.89.48.74")
    assert result.interface != "tailscale0"
    assert result.interface == "wlx"          # rơi về default


def test_no_default_route_means_unreachable():
    routes = [R("eth0", "10.0.0.0", 8, metric=100)]
    result = lookup(routes, "8.8.8.8")
    assert not result.found
    assert "bị loại bỏ" in result.explain()


def test_empty_table():
    assert not lookup([], "8.8.8.8").found


def test_invalid_address():
    assert not lookup([R("eth0", "0.0.0.0", 0)], "không-phải-ip").found


def test_explain_mentions_gateway(real_table):
    assert "qua 10.207.154.254" in lookup(real_table, "10.60.101.189").explain()


def test_explain_mentions_on_link(real_table):
    assert "trực tiếp" in lookup(real_table, "10.207.155.1").explain()


def test_default_route_owner(real_table):
    assert default_route_owner(real_table).interface == "wlx"


def test_default_route_owner_none_when_absent():
    assert default_route_owner([R("eth0", "10.0.0.0", 8)]) is None


# ── phân tích ────────────────────────────────────────────────────────────────


def titles(issues) -> str:
    return " | ".join(i.title for i in issues)


def test_reports_default_route_owner(real_table):
    assert "wlx" in titles(analyze(real_table))


def test_reports_split_off_prefixes(real_table):
    """Điểm mấu chốt của mạng nhiều đường: dải nào tách khỏi đường mặc định."""
    text = titles(analyze(real_table))
    assert "10.0.0.0/8 tách riêng qua enp1s0" in text


def test_reports_other_tables(real_table):
    """Không truyền `ip rule` thì bảng 52 đúng là không được tra cứu."""
    assert "Bảng 52 không được tra cứu" in titles(analyze(real_table))


def test_warns_when_no_default():
    issues = analyze([R("eth0", "10.0.0.0", 8)])
    assert any(i.level is IssueLevel.WARNING for i in issues)
    assert "Không có default route" in titles(issues)


def test_warns_on_equal_metric_defaults():
    routes = [
        R("eth0", "0.0.0.0", 0, via="192.168.1.1", metric=100),
        R("wlan0", "0.0.0.0", 0, via="192.168.2.1", metric=100),
    ]
    issues = analyze(routes)
    warning = next(i for i in issues if i.level is IssueLevel.WARNING)
    assert "cùng metric" in warning.title
    assert "eth0" in warning.detail and "wlan0" in warning.detail


def test_different_metric_defaults_is_normal():
    """Multi-homing đúng cách: metric khác nhau, có chính có dự phòng."""
    routes = [
        R("eth0", "0.0.0.0", 0, via="192.168.1.1", metric=100),
        R("wlan0", "0.0.0.0", 0, via="192.168.2.1", metric=600),
    ]
    issues = analyze(routes)
    assert all(i.level is IssueLevel.INFO for i in issues)
    primary = issues[0]
    assert "eth0" in primary.title
    assert "wlan0" in primary.detail and "600" in primary.detail


def test_warns_on_duplicate_prefix_across_interfaces():
    routes = [
        R("eth0", "0.0.0.0", 0, via="1.1.1.1", metric=100),
        R("eth1", "10.0.0.0", 8, via="192.168.1.1", metric=100),
        R("eth2", "10.0.0.0", 8, via="192.168.2.1", metric=100),
    ]
    warnings = [i for i in analyze(routes) if i.level is IssueLevel.WARNING]
    assert any("trùng trên nhiều interface" in i.title for i in warnings)


def test_same_prefix_different_metric_not_flagged():
    routes = [
        R("eth0", "0.0.0.0", 0, via="1.1.1.1", metric=100),
        R("eth1", "10.0.0.0", 8, via="192.168.1.1", metric=100),
        R("eth2", "10.0.0.0", 8, via="192.168.2.1", metric=200),
    ]
    assert not any("trùng" in i.title for i in analyze(routes))


def test_host_route_not_reported_as_split():
    """Route /32 là bình thường (VPN, peer), không đáng nêu."""
    routes = [
        R("eth0", "0.0.0.0", 0, via="1.1.1.1", metric=100),
        R("wg0", "10.1.2.3", 32, metric=0),
    ]
    assert not any("tách riêng" in i.title for i in analyze(routes))


# ── hiển thị ─────────────────────────────────────────────────────────────────


def test_group_by_interface(real_table):
    groups = {g.interface: g for g in group_by_interface(real_table)}
    assert set(groups) == {"enp1s0", "tailscale0", "br-1d594c364a72", "docker0", "wlx"}
    assert len(groups["enp1s0"].routes) == 2


def test_group_sorted_most_specific_first(real_table):
    enp = next(g for g in group_by_interface(real_table) if g.interface == "enp1s0")
    assert [r.route.prefix for r in enp.routes] == [22, 8]


def test_sorted_table_puts_main_table_first(real_table):
    ordered = sorted_table(real_table)
    main_count = sum(1 for e in real_table if e.route.table in (None, 0, 254))
    assert all(e.route.table in (None, 0, 254) for e in ordered[:main_count])


def test_sorted_table_default_route_last_in_main(real_table):
    ordered = [e for e in sorted_table(real_table) if e.route.table in (None, 0, 254)]
    assert ordered[-1].route.prefix == 0


def test_system_route_str():
    assert str(R("eth0", "10.0.0.0", 8, via="192.168.1.1", metric=100)) == (
        "10.0.0.0/8 via 192.168.1.1 metric 100 dev eth0"
    )
