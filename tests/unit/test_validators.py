"""Test cho bảng luật validate ở §3.3 — mỗi dòng trong bảng có ít nhất một test."""

from __future__ import annotations

import pytest

from netmgr.domain.models import Ipv4Address, Ipv4Route, ProxyConfig, ProxyEndpoint, ProxyMode
from netmgr.domain.validators import (
    format_route_line,
    normalize_route,
    parse_ipv4,
    parse_route_block,
    parse_route_line,
    validate_proxy,
    validate_route,
    validate_route_table,
)


def R(dest="10.20.0.0", prefix=16, **kw) -> Ipv4Route:
    return Ipv4Route(dest=dest, prefix=prefix, **kw)


def messages(result) -> str:
    return " | ".join(i.message for i in result.issues)


# ── parse_ipv4 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["10.0.0.1", "0.0.0.0", "255.255.255.255", " 192.168.1.1 "])
def test_parse_ipv4_accepts_valid(value):
    assert parse_ipv4(value) is not None


@pytest.mark.parametrize("value", ["10", "10.0.0", "10.0.0.256", "abc", "", "10.0.0.1.2", None])
def test_parse_ipv4_rejects_invalid(value):
    assert parse_ipv4(value) is None


def test_parse_ipv4_rejects_integer_shorthand():
    """`ipaddress` chấp nhận '10' -> 0.0.0.10; với input người dùng đó là gõ nhầm."""
    assert parse_ipv4("10") is None


# ── dest / prefix ────────────────────────────────────────────────────────────


def test_valid_route_passes():
    assert validate_route(R(next_hop="192.168.1.1", metric=100)).ok


def test_invalid_dest_rejected():
    res = validate_route(R(dest="10.20.0.999"))
    assert not res.ok
    assert "không phải địa chỉ IPv4 hợp lệ" in messages(res)


def test_empty_dest_rejected():
    assert not validate_route(R(dest="")).ok


@pytest.mark.parametrize("prefix", [-1, 33, 99])
def test_prefix_out_of_range_rejected(prefix):
    res = validate_route(R(prefix=prefix))
    assert not res.ok
    assert "0–32" in messages(res)


def test_host_bits_set_rejected_with_suggestion():
    """10.20.1.5/16 không phải network address — phải gợi ý 10.20.0.0."""
    res = validate_route(R(dest="10.20.1.5", prefix=16))
    assert not res.ok
    issue = next(i for i in res.issues if i.field == "dest")
    assert issue.suggestion == "10.20.0.0"


def test_host_bits_ok_for_prefix_32():
    assert validate_route(R(dest="10.20.1.5", prefix=32, next_hop="192.168.1.1")).ok


# ── next_hop ─────────────────────────────────────────────────────────────────


def test_invalid_next_hop_rejected():
    assert not validate_route(R(next_hop="nope")).ok


def test_next_hop_zero_rejected():
    res = validate_route(R(next_hop="0.0.0.0"))
    assert not res.ok
    assert "on-link" in messages(res)


def test_next_hop_inside_dest_subnet_and_unreachable_is_loop():
    res = validate_route(
        R(dest="192.168.1.0", prefix=24, next_hop="192.168.1.1"),
        local_subnets=[Ipv4Address("10.0.0.5", 24)],
    )
    assert not res.ok
    assert "vòng lặp" in messages(res)


def test_next_hop_inside_dest_subnet_but_on_link_is_valid():
    """Cấu hình doanh nghiệp rất phổ biến, gặp thật trên máy phát triển:

        interface  10.207.153.128/22
        route      10.0.0.0/8 via 10.207.154.254

    Gateway nằm trong 10.0.0.0/8 nhưng cũng nằm trong subnet của interface nên
    tới được trực tiếp. Chặn cấu hình này là sai.
    """
    res = validate_route(
        R(dest="10.0.0.0", prefix=8, next_hop="10.207.154.254", metric=100),
        local_subnets=[Ipv4Address("10.207.153.128", 22)],
    )
    assert res.ok, messages(res)
    assert not res.warnings


def test_next_hop_inside_dest_subnet_without_context_only_warns():
    """Không biết subnet của interface thì không kết luận được — chặn nhầm một
    cấu hình đúng còn tệ hơn cảnh báo thừa."""
    res = validate_route(R(dest="192.168.1.0", prefix=24, next_hop="192.168.1.1"))
    assert res.ok
    assert res.warnings
    assert "cùng subnet với interface" in messages(res)


def test_next_hop_inside_default_route_is_allowed():
    """0.0.0.0/0 chứa mọi địa chỉ — không được coi đó là vòng lặp."""
    res = validate_route(R(dest="0.0.0.0", prefix=0, next_hop="192.168.1.1"))
    assert res.ok


def test_unreachable_next_hop_is_warning_not_error():
    res = validate_route(
        R(next_hop="10.99.99.1"),
        local_subnets=[Ipv4Address("192.168.1.42", 24)],
    )
    assert res.ok                       # cảnh báo không chặn lưu
    assert res.warnings
    assert "on-link" in messages(res)


def test_reachable_next_hop_has_no_warning():
    res = validate_route(
        R(next_hop="192.168.1.1"),
        local_subnets=[Ipv4Address("192.168.1.42", 24)],
    )
    assert res.ok and not res.warnings


def test_onlink_route_skips_reachability_warning():
    res = validate_route(
        R(next_hop="10.99.99.1", onlink=True),
        local_subnets=[Ipv4Address("192.168.1.42", 24)],
    )
    assert not res.warnings


# ── metric / table ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("metric", [-1, 4294967296])
def test_metric_out_of_range_rejected(metric):
    assert not validate_route(R(metric=metric)).ok


def test_metric_bounds_accepted():
    assert validate_route(R(metric=0)).ok
    assert validate_route(R(metric=4294967295)).ok


def test_table_out_of_range_rejected():
    assert not validate_route(R(table=4294967296)).ok


# ── trùng lặp ────────────────────────────────────────────────────────────────


def test_duplicate_route_rejected():
    existing = [R(next_hop="192.168.1.1")]
    res = validate_route(R(next_hop="192.168.1.254"), existing=existing)
    assert not res.ok
    assert "đã tồn tại" in messages(res)


def test_same_dest_different_table_is_not_duplicate():
    existing = [R(table=100)]
    assert validate_route(R(table=200), existing=existing).ok


def test_editing_route_does_not_conflict_with_itself():
    original = R(next_hop="192.168.1.1")
    edited = R(next_hop="192.168.1.254")      # cùng identity, đổi gateway
    res = validate_route(
        edited, existing=[original], editing_identity=original.identity()
    )
    assert res.ok


# ── default route ────────────────────────────────────────────────────────────


def test_default_route_warns():
    res = validate_route(R(dest="0.0.0.0", prefix=0, next_hop="192.168.1.1"))
    assert res.ok
    assert "default route" in messages(res)


# ── bảng route ───────────────────────────────────────────────────────────────


def test_validate_route_table_flags_only_bad_rows():
    routes = [
        R(dest="10.20.0.0", prefix=16, next_hop="192.168.1.1"),
        R(dest="10.20.1.5", prefix=16),                    # host bits
        R(dest="172.16.0.0", prefix=12, next_hop="192.168.1.1"),
    ]
    problems = validate_route_table(routes)
    assert set(problems) == {1}


def test_validate_route_table_detects_duplicates_across_rows():
    routes = [R(), R()]
    problems = validate_route_table(routes)
    assert 0 in problems and 1 in problems


# ── normalize ────────────────────────────────────────────────────────────────


def test_normalize_snaps_to_network_address():
    assert normalize_route(R(dest="10.20.1.5", prefix=16)).dest == "10.20.0.0"


def test_normalize_clears_empty_fields():
    out = normalize_route(R(next_hop="", table=0))
    assert out.next_hop is None and out.table is None


# ── nhập nhanh một dòng ──────────────────────────────────────────────────────


def test_parse_route_line_full():
    route, err = parse_route_line("10.20.0.0/16 via 192.168.1.1 metric 100 table 200")
    assert err is None
    assert (route.dest, route.prefix) == ("10.20.0.0", 16)
    assert route.next_hop == "192.168.1.1"
    assert route.metric == 100 and route.table == 200


def test_parse_route_line_minimal_defaults_to_32():
    route, err = parse_route_line("10.20.0.5")
    assert err is None and route.prefix == 32


def test_parse_route_line_accepts_gw_alias_and_tabs():
    route, err = parse_route_line("10.20.0.0/16\tgw\t192.168.1.1")
    assert err is None and route.next_hop == "192.168.1.1"


def test_parse_route_line_rejects_garbage():
    route, err = parse_route_line("chịu thôi")
    assert route is None and err


def test_parse_route_line_propagates_validation_error():
    route, err = parse_route_line("10.20.1.5/16")
    assert route is None
    assert "địa chỉ mạng" in err


def test_parse_route_line_skips_blank_and_comment():
    for line in ["", "   ", "# ghi chú"]:
        route, err = parse_route_line(line)
        assert route is None and err is None


def test_parse_route_block_separates_good_and_bad():
    routes, errors = parse_route_block(
        "10.20.0.0/16 via 192.168.1.1\n"
        "# comment\n"
        "hỏng\n"
        "172.16.0.0/12 via 192.168.1.254 metric 50\n"
    )
    assert len(routes) == 2
    assert len(errors) == 1


def test_route_line_roundtrip():
    original = R(next_hop="192.168.1.1", metric=100)
    reparsed, err = parse_route_line(format_route_line(original))
    assert err is None
    assert (reparsed.dest, reparsed.prefix, reparsed.next_hop, reparsed.metric) == (
        original.dest, original.prefix, original.next_hop, original.metric,
    )


def test_route_line_roundtrip_onlink():
    reparsed, err = parse_route_line(format_route_line(R(metric=10)))
    assert err is None and reparsed.next_hop is None


# ── proxy ────────────────────────────────────────────────────────────────────


def P(**kw) -> ProxyConfig:
    kw.setdefault("name", "Test")
    return ProxyConfig(**kw)


def test_proxy_none_mode_is_valid():
    assert validate_proxy(P(mode=ProxyMode.NONE)).ok


def test_proxy_manual_requires_endpoint():
    res = validate_proxy(P(mode=ProxyMode.MANUAL))
    assert not res.ok
    assert "ít nhất một proxy" in messages(res)


def test_proxy_manual_valid():
    cfg = P(mode=ProxyMode.MANUAL, http=ProxyEndpoint("10.0.0.8", 3128))
    assert validate_proxy(cfg).ok


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_proxy_bad_port_rejected(port):
    cfg = P(mode=ProxyMode.MANUAL, http=ProxyEndpoint("10.0.0.8", port))
    assert not validate_proxy(cfg).ok


@pytest.mark.parametrize(
    "host",
    ["10.0.0.8; rm -rf /", "$(whoami)", "host`id`", "a b", 'x"y', "a\nb"],
)
def test_proxy_host_injection_rejected(host):
    """§4.7 — giá trị này đi vào environment.d/apt.conf, chặn ngay từ đầu vào."""
    cfg = P(mode=ProxyMode.MANUAL, http=ProxyEndpoint(host, 3128))
    assert not validate_proxy(cfg).ok


def test_proxy_auth_requires_username():
    cfg = P(mode=ProxyMode.MANUAL, http=ProxyEndpoint("10.0.0.8", 3128), auth_enabled=True)
    assert not validate_proxy(cfg).ok


def test_proxy_auto_requires_pac_url():
    assert not validate_proxy(P(mode=ProxyMode.AUTO)).ok


@pytest.mark.parametrize("url", ["http://x/p.pac", "https://x/p.pac", "file:///tmp/p.pac"])
def test_proxy_auto_accepts_valid_pac(url):
    assert validate_proxy(P(mode=ProxyMode.AUTO, pac_url=url)).ok


@pytest.mark.parametrize("url", ["ftp://x/p.pac", "x/p.pac", "javascript:alert(1)"])
def test_proxy_auto_rejects_bad_pac(url):
    assert not validate_proxy(P(mode=ProxyMode.AUTO, pac_url=url)).ok


def test_proxy_name_required():
    assert not validate_proxy(ProxyConfig(name="", mode=ProxyMode.NONE)).ok


def test_proxy_ignore_hosts_injection_rejected():
    cfg = P(mode=ProxyMode.NONE, ignore_hosts=["localhost", "evil`id`"])
    assert not validate_proxy(cfg).ok
