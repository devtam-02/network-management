"""Phân tích bảng định tuyến hợp nhất — thuần Python (§3.6).

Trả lời câu hỏi quan trọng nhất của mạng nhiều đường: *"truy cập địa chỉ này thì
đi ra interface nào?"* — bằng cách mô phỏng đúng cách kernel chọn route.

Không dùng `ip route get` vì hai lý do: app không nên phải spawn tiến trình để
hiển thị một bảng, và mô phỏng thuần Python thì test được mọi tình huống mà máy
thật không dựng ra được.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum

from .models import RoutingRule, SystemRoute

#: Bảng định tuyến chính. Route không ghi rõ bảng thì thuộc về bảng này.
#:
#: Bảng khác CÓ thể ảnh hưởng traffic thông thường — điều này đã kiểm chứng
#: trên máy thật: Tailscale cài `ip rule` priority 5270 trỏ tới bảng 52, đứng
#: trước `lookup main` (32766), nên bảng 52 được tra TRƯỚC. Vì vậy `lookup()`
#: bắt buộc phải đọc `ip rule` chứ không được chỉ nhìn bảng main.
MAIN_TABLES = (None, 0, 254)


def is_main_table(route: SystemRoute) -> bool:
    return route.route.table in MAIN_TABLES


@dataclass(frozen=True, slots=True)
class LookupResult:
    """Kết quả tra cứu một địa chỉ đích."""

    dest: str
    route: SystemRoute | None
    #: Các route cũng khớp nhưng thua vì kém cụ thể hơn hoặc metric cao hơn.
    also_matched: tuple[SystemRoute, ...] = ()
    #: Bảng định tuyến đã cho ra kết quả.
    table: int | None = None

    @property
    def found(self) -> bool:
        return self.route is not None

    @property
    def interface(self) -> str | None:
        return self.route.interface if self.route else None

    def explain(self) -> str:
        if self.route is None:
            return f"Không có route nào tới {self.dest} — traffic sẽ bị loại bỏ"

        r = self.route.route
        via = f"qua {r.next_hop}" if r.next_hop else "trực tiếp trên liên kết"
        reason = f"khớp {r.cidr}"
        if self.table not in (None, 254):
            reason += f" ở bảng {self.table}"
        if self.also_matched:
            reason += f", cụ thể hơn {len(self.also_matched)} route khác"
        return f"{self.dest} → {self.route.interface} {via} ({reason})"


def lookup(
    routes: list[SystemRoute],
    dest: str,
    rules: list[RoutingRule] | None = None,
) -> LookupResult:
    """Chọn route như kernel.

    Kernel duyệt `ip rule` theo priority tăng dần; rule đầu tiên khớp sẽ chỉ ra
    bảng cần tra. Trong bảng đó, prefix dài nhất thắng, hoà thì metric nhỏ hơn
    thắng. Nếu bảng đó không có route nào khớp thì đi tiếp rule sau.

    Bỏ qua `ip rule` là sai thực tế, không phải chuyện lý thuyết: Tailscale cài
    rule `from all lookup 52` ở priority 5270, tức TRƯỚC `lookup main` (32766).
    """
    try:
        target = ipaddress.IPv4Address(dest.strip())
    except ValueError:
        return LookupResult(dest, None)

    for table in _tables_in_order(rules):
        matches = [
            entry
            for entry in routes
            if _table_of(entry) == table and _contains(entry, target)
        ]
        if not matches:
            continue
        # Prefix dài nhất trước; cùng độ dài thì metric nhỏ hơn thắng.
        matches.sort(key=lambda e: (-e.route.prefix, e.metric))
        return LookupResult(dest, matches[0], tuple(matches[1:]), table=table)

    return LookupResult(dest, None)


def _tables_in_order(rules: list[RoutingRule] | None) -> list[int]:
    """Thứ tự các bảng sẽ được tra, theo priority của `ip rule`.

    Không có thông tin rule thì dùng thứ tự mặc định của kernel: local → main →
    default.
    """
    if not rules:
        return [255, 254, 253]

    order: list[int] = []
    for rule in sorted(rules, key=lambda r: r.priority):
        if rule.action != "lookup" or rule.table is None:
            continue
        if not rule.matches_plain_traffic:
            continue
        if rule.table not in order:
            order.append(rule.table)
    return order or [255, 254, 253]


def _table_of(entry: SystemRoute) -> int:
    """Bảng của route; None/0 nghĩa là bảng main."""
    table = entry.route.table
    return 254 if table in (None, 0) else table


def _contains(entry: SystemRoute, target: ipaddress.IPv4Address) -> bool:
    network = _network_of(entry)
    return network is not None and target in network


def _network_of(entry: SystemRoute) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.IPv4Network(
            f"{entry.route.dest}/{entry.route.prefix}", strict=False
        )
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Cảnh báo
# ─────────────────────────────────────────────────────────────────────────────


class IssueLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class RoutingIssue:
    level: IssueLevel
    title: str
    detail: str


def default_route_owner(routes: list[SystemRoute]) -> SystemRoute | None:
    """Interface nhận traffic không khớp route nào khác."""
    defaults = [
        e for e in routes if is_main_table(e) and e.route.prefix == 0
    ]
    if not defaults:
        return None
    defaults.sort(key=lambda e: e.metric)
    return defaults[0]


def analyze(
    routes: list[SystemRoute], rules: list[RoutingRule] | None = None
) -> list[RoutingIssue]:
    """Những điều đáng nói về bảng định tuyến hiện tại."""
    issues: list[RoutingIssue] = []
    main = [e for e in routes if is_main_table(e)]

    # ── default route ──
    defaults = sorted([e for e in main if e.route.prefix == 0], key=lambda e: e.metric)
    if not defaults:
        issues.append(
            RoutingIssue(
                IssueLevel.WARNING,
                "Không có default route",
                "Traffic ra Internet sẽ không đi được.",
            )
        )
    elif len(defaults) == 1:
        issues.append(
            RoutingIssue(
                IssueLevel.INFO,
                f"Traffic mặc định đi qua {defaults[0].interface}",
                str(defaults[0]),
            )
        )
    elif defaults[0].metric == defaults[1].metric:
        # Cùng metric → kernel chọn thế nào là không xác định trước.
        names = ", ".join(e.interface for e in defaults if e.metric == defaults[0].metric)
        issues.append(
            RoutingIssue(
                IssueLevel.WARNING,
                "Nhiều default route cùng metric",
                f"{names} — không đoán trước được đường nào thắng. "
                f"Đặt route-metric khác nhau để chỉ định rõ.",
            )
        )
    else:
        others = ", ".join(f"{e.interface} (metric {e.metric})" for e in defaults[1:])
        issues.append(
            RoutingIssue(
                IssueLevel.INFO,
                f"Traffic mặc định đi qua {defaults[0].interface} "
                f"(metric {defaults[0].metric})",
                f"Dự phòng: {others}",
            )
        )

    # ── trùng đích trên nhiều interface ──
    by_key: dict[tuple, list[SystemRoute]] = {}
    for entry in main:
        by_key.setdefault(
            (entry.route.dest, entry.route.prefix, entry.metric), []
        ).append(entry)
    for (dest, prefix, metric), group in by_key.items():
        if len(group) > 1 and len({e.interface for e in group}) > 1:
            issues.append(
                RoutingIssue(
                    IssueLevel.WARNING,
                    f"{dest}/{prefix} trùng trên nhiều interface",
                    f"{', '.join(e.interface for e in group)} — cùng metric {metric}, "
                    "kết quả không xác định.",
                )
            )

    # ── route cụ thể tách khỏi default ──
    default_iface = defaults[0].interface if defaults else None
    for entry in main:
        if entry.route.prefix == 0 or entry.interface == default_iface:
            continue
        if entry.route.prefix < 32:
            issues.append(
                RoutingIssue(
                    IssueLevel.INFO,
                    f"{entry.route.cidr} tách riêng qua {entry.interface}",
                    "Traffic tới dải này không đi theo đường mặc định.",
                )
            )

    # ── bảng định tuyến khác ──
    #
    # Phải đối chiếu với `ip rule` thật: bảng phụ có ảnh hưởng hay không phụ
    # thuộc hoàn toàn vào việc có rule nào trỏ tới nó và ở priority bao nhiêu.
    consulted = _tables_in_order(rules)
    main_position = consulted.index(254) if 254 in consulted else len(consulted)

    extra_tables = sorted(
        {e.route.table for e in routes if not is_main_table(e) and e.route.table}
    )
    for table in extra_tables:
        count = sum(1 for e in routes if e.route.table == table)

        if table not in consulted:
            issues.append(
                RoutingIssue(
                    IssueLevel.INFO,
                    f"Bảng {table} không được tra cứu",
                    f"{count} route, nhưng không có `ip rule` nào trỏ tới — "
                    "không ảnh hưởng traffic.",
                )
            )
        elif consulted.index(table) < main_position:
            issues.append(
                RoutingIssue(
                    IssueLevel.WARNING,
                    f"Bảng {table} được tra TRƯỚC bảng chính",
                    f"{count} route ở bảng này chiếm quyền trước mọi route thông "
                    "thường. Thường do VPN (Tailscale, WireGuard) cài đặt.",
                )
            )
        else:
            issues.append(
                RoutingIssue(
                    IssueLevel.INFO,
                    f"Bảng {table} được tra sau bảng chính",
                    f"{count} route, chỉ dùng khi bảng chính không khớp.",
                )
            )
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Hiển thị
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class RouteGroup:
    """Route gộp theo interface, đã sắp xếp để đọc."""

    interface: str
    routes: list[SystemRoute] = field(default_factory=list)


def group_by_interface(routes: list[SystemRoute]) -> list[RouteGroup]:
    groups: dict[str, RouteGroup] = {}
    for entry in routes:
        groups.setdefault(entry.interface, RouteGroup(entry.interface)).routes.append(entry)
    for group in groups.values():
        # Cụ thể nhất lên trước, giống thứ tự kernel xét.
        group.routes.sort(key=lambda e: (-e.route.prefix, e.metric))
    return sorted(groups.values(), key=lambda g: g.interface)


def sorted_table(routes: list[SystemRoute]) -> list[SystemRoute]:
    """Toàn bộ bảng theo đúng thứ tự ưu tiên của kernel."""
    return sorted(
        routes,
        key=lambda e: (0 if is_main_table(e) else 1, -e.route.prefix, e.metric),
    )
