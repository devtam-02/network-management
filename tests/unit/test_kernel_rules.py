"""Test parse `ip rule` và tra cứu có tính tới policy routing."""

from __future__ import annotations

import pytest

from netmgr.domain.models import Ipv4Route, RoutingRule, SystemRoute
from netmgr.domain.routing import IssueLevel, analyze, lookup
from netmgr.infra.kernel_rules import parse_rules

#: Output thật của `ip rule show` trên máy phát triển (có Tailscale).
REAL_OUTPUT = """\
0:	from all lookup local
5210:	from all fwmark 0x80000/0xff0000 lookup main
5230:	from all fwmark 0x80000/0xff0000 lookup default
5250:	from all fwmark 0x80000/0xff0000 unreachable
5270:	from all lookup 52
32766:	from all lookup main
32767:	from all lookup default
"""


def R(iface, dest, prefix, *, via=None, metric=None, table=None) -> SystemRoute:
    return SystemRoute(
        iface, Ipv4Route(dest, prefix, next_hop=via, metric=metric, table=table)
    )


# ── parse ────────────────────────────────────────────────────────────────────


def test_parses_all_lines():
    assert len(parse_rules(REAL_OUTPUT)) == 7


def test_priorities_and_tables():
    rules = {r.priority: r for r in parse_rules(REAL_OUTPUT)}
    assert rules[0].table == 255            # local
    assert rules[5270].table == 52
    assert rules[32766].table == 254        # main
    assert rules[32767].table == 253        # default


def test_fwmark_parsed_as_int():
    rule = next(r for r in parse_rules(REAL_OUTPUT) if r.priority == 5210)
    assert rule.fwmark == 0x80000
    assert rule.fwmask == 0xFF0000


def test_action_parsed():
    rule = next(r for r in parse_rules(REAL_OUTPUT) if r.priority == 5250)
    assert rule.action == "unreachable"


def test_rules_sorted_by_priority():
    priorities = [r.priority for r in parse_rules(REAL_OUTPUT)]
    assert priorities == sorted(priorities)


def test_fwmark_rules_do_not_match_plain_traffic():
    """Traffic thông thường không mang mark, nên rule theo mark không áp."""
    rule = next(r for r in parse_rules(REAL_OUTPUT) if r.priority == 5210)
    assert rule.matches_plain_traffic is False


def test_plain_rules_match():
    rule = next(r for r in parse_rules(REAL_OUTPUT) if r.priority == 5270)
    assert rule.matches_plain_traffic is True


def test_source_constrained_rule_does_not_match():
    rules = parse_rules("100:\tfrom 10.0.0.0/8 lookup 100\n")
    assert rules[0].from_prefix == "10.0.0.0/8"
    assert rules[0].matches_plain_traffic is False


def test_iif_and_not_parsed():
    rules = parse_rules("200:\tnot from all iif eth0 lookup 5\n")
    assert rules[0].invert is True
    assert rules[0].iif == "eth0"
    assert rules[0].matches_plain_traffic is False


def test_table_keyword_alias():
    assert parse_rules("300:\tfrom all table 7\n")[0].table == 7


def test_garbage_lines_ignored():
    assert parse_rules("không phải rule\n\n") == []


def test_empty_input():
    assert parse_rules("") == []


def test_rule_str_roundtrip_readable():
    rule = next(r for r in parse_rules(REAL_OUTPUT) if r.priority == 5270)
    assert str(rule) == "5270: from all lookup 52"


# ── tra cứu có ip rule ───────────────────────────────────────────────────────


@pytest.fixture
def routes():
    return [
        R("enp1s0", "10.207.152.0", 22, metric=100),
        R("enp1s0", "10.0.0.0", 8, via="10.207.154.254", metric=100),
        R("tailscale0", "100.89.48.74", 32, metric=0, table=52),
        R("wlx", "172.46.0.0", 16, metric=600),
        R("wlx", "0.0.0.0", 0, via="172.46.0.1", metric=600),
    ]


@pytest.fixture
def rules():
    return parse_rules(REAL_OUTPUT)


def test_lookup_respects_rule_priority(routes, rules):
    """Bảng 52 được tra trước main — đây là lỗi thật đã phát hiện khi đối chiếu
    với `ip route get`, trước đó app trả về wlx."""
    assert lookup(routes, "100.89.48.74", rules).interface == "tailscale0"


def test_lookup_falls_through_when_table_misses(routes, rules):
    """Bảng 52 không có route tới 8.8.8.8 → đi tiếp xuống bảng main."""
    assert lookup(routes, "8.8.8.8", rules).interface == "wlx"


def test_lookup_reports_which_table_won(routes, rules):
    assert lookup(routes, "100.89.48.74", rules).table == 52
    assert lookup(routes, "8.8.8.8", rules).table == 254


def test_explain_mentions_non_main_table(routes, rules):
    assert "bảng 52" in lookup(routes, "100.89.48.74", rules).explain()


def test_without_rules_extra_tables_are_invisible(routes):
    """Không có thông tin rule thì chỉ dùng thứ tự mặc định của kernel."""
    assert lookup(routes, "100.89.48.74").interface == "wlx"


def test_rule_pointing_nowhere_is_skipped(routes):
    custom = parse_rules("100:\tfrom all lookup 99\n32766:\tfrom all lookup main\n")
    assert lookup(routes, "8.8.8.8", custom).interface == "wlx"


def test_lower_priority_table_used_only_as_fallback():
    routes = [
        R("eth0", "0.0.0.0", 0, via="1.1.1.1", metric=100),
        R("vpn0", "0.0.0.0", 0, via="10.0.0.1", metric=0, table=100),
    ]
    after = parse_rules("32766:\tfrom all lookup main\n32800:\tfrom all lookup 100\n")
    before = parse_rules("100:\tfrom all lookup 100\n32766:\tfrom all lookup main\n")
    assert lookup(routes, "8.8.8.8", after).interface == "eth0"
    assert lookup(routes, "8.8.8.8", before).interface == "vpn0"


# ── phân tích có ip rule ─────────────────────────────────────────────────────


def test_analyze_warns_table_consulted_before_main(routes, rules):
    issues = analyze(routes, rules)
    warning = next(
        i for i in issues if i.level is IssueLevel.WARNING and "Bảng 52" in i.title
    )
    assert "TRƯỚC bảng chính" in warning.title
    assert "VPN" in warning.detail


def test_analyze_reports_unused_table(routes):
    only_main = parse_rules("32766:\tfrom all lookup main\n")
    issues = analyze(routes, only_main)
    assert any("không được tra cứu" in i.title for i in issues)


def test_analyze_reports_fallback_table():
    routes = [
        R("eth0", "0.0.0.0", 0, via="1.1.1.1", metric=100),
        R("vpn0", "10.8.0.0", 24, metric=0, table=100),
    ]
    rules = parse_rules("32766:\tfrom all lookup main\n32800:\tfrom all lookup 100\n")
    assert any("tra sau bảng chính" in i.title for i in analyze(routes, rules))
