"""Đọc `ip rule` của kernel.

NetworkManager chỉ biết những rule do chính nó cấu hình; rule do phần mềm khác
cài (Tailscale, WireGuard, VPN doanh nghiệp) không xuất hiện trong libnm. Mà
chính những rule đó lại quyết định bảng nào được tra trước — bỏ qua chúng thì
việc mô phỏng định tuyến sẽ ra kết quả sai.

Không có cách nào lấy được qua libnm, nên module này gọi `ip rule show` và parse.
Hàm parse là hàm thuần, tách riêng để test được mà không cần chạy `ip`.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

from ..domain.models import ROUTE_TABLE_IDS, RoutingRule

log = logging.getLogger(__name__)

_LINE = re.compile(r"^(\d+):\s+(.*)$")
_ACTIONS = ("unreachable", "blackhole", "prohibit", "throw")


def parse_rules(text: str) -> list[RoutingRule]:
    """Parse output của `ip rule show`.

    Ví dụ dòng thật gặp trên máy có Tailscale:

        5210:   from all fwmark 0x80000/0xff0000 lookup main
        5270:   from all lookup 52
        32766:  from all lookup main
    """
    rules: list[RoutingRule] = []
    for line in (text or "").splitlines():
        match = _LINE.match(line.strip())
        if not match:
            continue
        priority = int(match.group(1))
        tokens = match.group(2).split()

        rule = {
            "from_prefix": "all",
            "to_prefix": "all",
            "fwmark": 0,
            "fwmask": 0,
            "iif": None,
            "oif": None,
            "invert": False,
            "action": "lookup",
            "table": None,
        }

        index = 0
        while index < len(tokens):
            token = tokens[index]
            nxt = tokens[index + 1] if index + 1 < len(tokens) else None

            if token == "not":
                rule["invert"] = True
            elif token == "from" and nxt:
                rule["from_prefix"] = nxt; index += 1
            elif token == "to" and nxt:
                rule["to_prefix"] = nxt; index += 1
            elif token == "iif" and nxt:
                rule["iif"] = nxt; index += 1
            elif token == "oif" and nxt:
                rule["oif"] = nxt; index += 1
            elif token == "fwmark" and nxt:
                mark, _, mask = nxt.partition("/")
                rule["fwmark"] = _to_int(mark)
                rule["fwmask"] = _to_int(mask) if mask else 0
                index += 1
            elif token in ("lookup", "table") and nxt:
                rule["table"] = _table_id(nxt); index += 1
            elif token in _ACTIONS:
                rule["action"] = token
            index += 1

        rules.append(RoutingRule(priority=priority, **rule))

    rules.sort(key=lambda r: r.priority)
    return rules


def _to_int(value: str) -> int:
    try:
        return int(value, 16) if value.lower().startswith("0x") else int(value)
    except ValueError:
        return 0


def _table_id(value: str) -> int | None:
    if value in ROUTE_TABLE_IDS:
        return ROUTE_TABLE_IDS[value]
    try:
        return int(value)
    except ValueError:
        return None


def read_rules() -> list[RoutingRule]:
    """Đọc từ kernel. Trả về [] nếu không chạy được — không được làm sập app."""
    if shutil.which("ip") is None:
        log.debug("Không có lệnh `ip`, bỏ qua ip rule")
        return []
    try:
        result = subprocess.run(
            ["ip", "-4", "rule", "show"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Không đọc được ip rule: %s", exc)
        return []
    if result.returncode != 0:
        log.warning("`ip rule show` trả về %s", result.returncode)
        return []
    return parse_rules(result.stdout)
