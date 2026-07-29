"""Validator — hiện thực bảng luật ở §3.3 và các ràng buộc bảo mật ở §4.7.

Thuần Python. Đây là nơi tập trung mọi luật "route thế nào là hợp lệ", để UI chỉ
việc hiển thị `ValidationIssue` chứ không tự nghĩ ra luật riêng.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field, replace
from enum import Enum

from .models import Ipv4Address, Ipv4Route, ProxyConfig, ProxyMode

UINT32_MAX = 4294967295

# Ký tự có thể phá cú pháp khi sinh file environment.d / apt.conf (§4.7)
_UNSAFE_HOST_RE = re.compile(r"""[\s"'`$;&|<>\\()\[\]{}]""")


class Severity(str, Enum):
    ERROR = "error"       # chặn, không cho lưu
    WARNING = "warning"   # cho lưu nhưng phải cảnh báo


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    field: str
    message: str
    severity: Severity = Severity.ERROR
    #: Giá trị sửa sẵn để UI hiện nút "Sửa giúp tôi" (vd: snap về network address)
    suggestion: str | None = None


@dataclass(slots=True)
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Không có lỗi chặn. Warning vẫn coi là ok."""
        return not self.errors

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    def error(self, fld: str, msg: str, suggestion: str | None = None) -> None:
        self.issues.append(ValidationIssue(fld, msg, Severity.ERROR, suggestion))

    def warn(self, fld: str, msg: str, suggestion: str | None = None) -> None:
        self.issues.append(ValidationIssue(fld, msg, Severity.WARNING, suggestion))

    def extend(self, other: ValidationResult) -> None:
        self.issues.extend(other.issues)

    def first_message(self) -> str | None:
        return self.issues[0].message if self.issues else None


# ─────────────────────────────────────────────────────────────────────────────
# Nguyên thuỷ IPv4
# ─────────────────────────────────────────────────────────────────────────────


def parse_ipv4(value: str) -> ipaddress.IPv4Address | None:
    """Parse chặt: chỉ chấp nhận dotted-quad đủ 4 octet.

    `ipaddress` cho phép dạng nguyên ("10" -> 0.0.0.10) — với địa chỉ mạng do
    người dùng gõ thì đó gần như luôn là gõ nhầm, nên ta từ chối.
    """
    value = (value or "").strip()
    if value.count(".") != 3:
        return None
    try:
        return ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return None


def is_valid_ipv4(value: str) -> bool:
    return parse_ipv4(value) is not None


def network_of(dest: str, prefix: int) -> ipaddress.IPv4Network | None:
    """Network chứa `dest/prefix`, kể cả khi dest không phải network address."""
    addr = parse_ipv4(dest)
    if addr is None or not 0 <= prefix <= 32:
        return None
    return ipaddress.IPv4Network(f"{addr}/{prefix}", strict=False)


def validate_ipv4_address(value: str, fld: str = "address") -> ValidationResult:
    r = ValidationResult()
    if not (value or "").strip():
        r.error(fld, "Không được để trống")
    elif not is_valid_ipv4(value):
        r.error(fld, f"'{value}' không phải địa chỉ IPv4 hợp lệ")
    return r


def validate_prefix(prefix: int, fld: str = "prefix") -> ValidationResult:
    r = ValidationResult()
    if not isinstance(prefix, int) or isinstance(prefix, bool):
        r.error(fld, "Prefix phải là số nguyên")
    elif not 0 <= prefix <= 32:
        r.error(fld, "Prefix phải trong khoảng 0–32")
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Route — bảng luật §3.3
# ─────────────────────────────────────────────────────────────────────────────


def _is_directly_reachable(
    hop: ipaddress.IPv4Address, local_subnets: list[Ipv4Address] | None
) -> bool | None:
    """Gateway có nằm trong subnet nào của interface không.

    Trả về None khi chưa biết subnet của interface — khác hẳn với False, và
    người gọi phải phân biệt hai trường hợp đó.
    """
    if not local_subnets:
        return None
    for addr in local_subnets:
        if parse_ipv4(addr.address) is None or not 0 <= addr.prefix <= 32:
            continue
        if hop in ipaddress.IPv4Network(f"{addr.address}/{addr.prefix}", strict=False):
            return True
    return False


def validate_route(
    route: Ipv4Route,
    *,
    existing: list[Ipv4Route] | None = None,
    local_subnets: list[Ipv4Address] | None = None,
    editing_identity: tuple[str, int, int] | None = None,
) -> ValidationResult:
    """Validate một route.

    existing:         các route đã có, để bắt trùng (dest, prefix, table).
    local_subnets:    subnet mà interface đang có, để cảnh báo gateway không
                      reachable. Bỏ trống thì bỏ qua luật cảnh báo đó.
    editing_identity: khi đang *sửa* một route, truyền identity cũ vào để không
                      tự báo trùng với chính nó.
    """
    r = ValidationResult()

    # ── dest ────────────────────────────────────────────────────────────────
    dest_addr = parse_ipv4(route.dest)
    if not (route.dest or "").strip():
        r.error("dest", "Địa chỉ đích không được để trống")
    elif dest_addr is None:
        r.error("dest", f"'{route.dest}' không phải địa chỉ IPv4 hợp lệ")

    # ── prefix ──────────────────────────────────────────────────────────────
    r.extend(validate_prefix(route.prefix))

    # ── dest phải là network address ────────────────────────────────────────
    if dest_addr is not None and 0 <= route.prefix <= 32:
        net = ipaddress.IPv4Network(f"{dest_addr}/{route.prefix}", strict=False)
        if net.network_address != dest_addr:
            r.error(
                "dest",
                f"{dest_addr}/{route.prefix} không phải địa chỉ mạng — "
                f"ý bạn là {net.network_address}/{route.prefix}?",
                suggestion=str(net.network_address),
            )

    # ── next_hop ────────────────────────────────────────────────────────────
    hop_addr = None
    if not route.next_hop and not route.onlink:
        # CẢNH BÁO, không phải lỗi: route on-link là hợp lệ và `parse_route_line`
        # phải đọc được dạng một dòng "10.0.0.0/8" đang nằm trong file cấu hình.
        # Form thêm/sửa route mới là chỗ chặn cứng (xem `ui/route_editor.py`).
        r.warn(
            "next_hop",
            "Không có gateway → route on-link: chỉ đúng khi đích nằm trực tiếp "
            "trên liên kết.",
        )

    if route.next_hop:
        hop_addr = parse_ipv4(route.next_hop)
        if hop_addr is None:
            r.error("next_hop", f"'{route.next_hop}' không phải gateway hợp lệ")
        elif hop_addr == ipaddress.IPv4Address("0.0.0.0"):
            r.error("next_hop", "Gateway 0.0.0.0 không hợp lệ — để trống nếu là route on-link")

    # ── gateway có tới được không ───────────────────────────────────────────
    #
    # Luật "gateway nằm trong dải đích = vòng lặp" nghe hợp lý nhưng SAI nếu áp
    # dụng máy móc. Ví dụ có thật, rất phổ biến trong mạng doanh nghiệp:
    #
    #     interface: 10.207.153.128/22   (tức subnet 10.207.152.0/22)
    #     route:     10.0.0.0/8 via 10.207.154.254
    #
    # Gateway nằm trong 10.0.0.0/8, nhưng nó cũng nằm ngay trong subnet của
    # interface nên tới được trực tiếp — route này chạy hoàn toàn bình thường.
    # Vòng lặp thật chỉ xảy ra khi gateway nằm trong dải đích VÀ không tới được
    # bằng cách nào khác.
    #
    # Khi không biết subnet của interface thì ta không kết luận được, nên hạ
    # xuống cảnh báo: chặn nhầm một cấu hình đúng còn tệ hơn là cảnh báo thừa.
    if hop_addr is not None:
        reachable = _is_directly_reachable(hop_addr, local_subnets)
        in_dest = (
            dest_addr is not None
            and route.prefix > 0        # 0.0.0.0/0 chứa mọi địa chỉ, bỏ qua
            and hop_addr in ipaddress.IPv4Network(
                f"{dest_addr}/{route.prefix}", strict=False
            )
        )

        if in_dest and reachable is False:
            r.error(
                "next_hop",
                f"Gateway {hop_addr} nằm trong dải đích và không thuộc subnet nào "
                "của interface → route vòng lặp",
            )
        elif in_dest and reachable is None:
            r.warn(
                "next_hop",
                f"Gateway {hop_addr} nằm trong dải đích {dest_addr}/{route.prefix}. "
                "Chỉ hợp lệ nếu nó nằm cùng subnet với interface.",
            )
        elif reachable is False and not route.onlink:
            r.warn(
                "next_hop",
                f"Gateway {hop_addr} không nằm trong subnet nào của interface — "
                "route có thể không hoạt động (cần bật on-link?)",
            )

    # ── metric / table ──────────────────────────────────────────────────────
    if route.metric is not None and not 0 <= route.metric <= UINT32_MAX:
        r.error("metric", f"Metric phải trong khoảng 0–{UINT32_MAX}")
    if route.table is not None and not 0 <= route.table <= UINT32_MAX:
        r.error("table", f"Table ID phải trong khoảng 0–{UINT32_MAX}")

    # ── trùng lặp ───────────────────────────────────────────────────────────
    if existing:
        ident = route.identity()
        for other in existing:
            if other.identity() == ident and other.identity() != editing_identity:
                r.error("dest", f"Route {route.cidr} đã tồn tại trong bảng")
                break

    # ── default route ───────────────────────────────────────────────────────
    if route.prefix == 0 and dest_addr == ipaddress.IPv4Address("0.0.0.0"):
        r.warn(
            "dest",
            "Đây là default route — sẽ ảnh hưởng toàn bộ traffic không khớp route nào khác",
        )

    return r


def normalize_route(route: Ipv4Route) -> Ipv4Route:
    """Snap `dest` về địa chỉ mạng và dọn các trường rỗng.

    Dùng cho nút "Sửa giúp tôi" và cho luồng import hàng loạt.
    """
    net = network_of(route.dest, route.prefix)
    dest = str(net.network_address) if net else route.dest
    return replace(
        route,
        dest=dest,
        next_hop=(route.next_hop or None) or None,
        table=route.table or None,
    )


def validate_route_table(
    routes: list[Ipv4Route],
    *,
    local_subnets: list[Ipv4Address] | None = None,
) -> dict[int, ValidationResult]:
    """Validate cả bảng, trả về {chỉ số route: kết quả}. Chỉ giữ mục có vấn đề."""
    out: dict[int, ValidationResult] = {}
    for idx, route in enumerate(routes):
        others = routes[:idx] + routes[idx + 1:]
        res = validate_route(route, existing=others, local_subnets=local_subnets)
        if res.issues:
            out[idx] = res
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Nhập nhanh một dòng (FR-R2) / import hàng loạt (FR-R9)
# ─────────────────────────────────────────────────────────────────────────────

_ROUTE_LINE_RE = re.compile(
    r"""^\s*
        (?P<dest>[\d.]+)
        (?:/(?P<prefix>\d{1,2}))?
        (?:\s+(?:via|gw)\s+(?P<next_hop>[\d.]+))?
        (?:\s+metric\s+(?P<metric>\d+))?
        (?:\s+table\s+(?P<table>\d+))?
        \s*$""",
    re.VERBOSE | re.IGNORECASE,
)


def parse_route_line(line: str) -> tuple[Ipv4Route | None, str | None]:
    """Parse `10.20.0.0/16 via 192.168.1.1 metric 100`.

    Trả về (route, None) khi thành công, (None, thông báo lỗi) khi hỏng. Chấp
    nhận cả cú pháp `gw` thay cho `via`, và dấu phẩy/tab làm dấu phân cách
    (tiện khi dán từ bảng tính).
    """
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None, None                      # dòng trống / comment: bỏ qua
    normalized = re.sub(r"[,\t]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized)

    m = _ROUTE_LINE_RE.match(normalized)
    if not m:
        return None, f"Không hiểu cú pháp: '{raw}'"

    prefix = int(m["prefix"]) if m["prefix"] else 32
    route = Ipv4Route(
        dest=m["dest"],
        prefix=prefix,
        next_hop=m["next_hop"],
        metric=int(m["metric"]) if m["metric"] else None,
        table=int(m["table"]) if m["table"] else None,
    )
    res = validate_route(route)
    if not res.ok:
        return None, f"{raw} → {res.errors[0].message}"
    return route, None


def parse_route_block(text: str) -> tuple[list[Ipv4Route], list[str]]:
    """Parse nhiều dòng. Trả về (route hợp lệ, danh sách lỗi từng dòng)."""
    routes: list[Ipv4Route] = []
    errors: list[str] = []
    for line in (text or "").splitlines():
        route, err = parse_route_line(line)
        if err:
            errors.append(err)
        elif route is not None:
            routes.append(route)
    return routes, errors


def format_route_line(route: Ipv4Route) -> str:
    """Ngược của `parse_route_line` — dùng cho Export (FR-R9).

    Cố tình KHÔNG dùng `str(route)`: chuỗi đó là để hiển thị cho người đọc và có
    chứa "(on-link)", thứ mà parser không hiểu. Định dạng ở đây phải parse ngược
    lại được, nếu không thì export → import sẽ mất dữ liệu.
    """
    parts = [route.cidr]
    if route.next_hop:
        parts += ["via", route.next_hop]
    if route.metric is not None:
        parts += ["metric", str(route.metric)]
    if route.table:
        parts += ["table", str(route.table)]
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Proxy
# ─────────────────────────────────────────────────────────────────────────────


def validate_proxy_host(host: str, fld: str = "host") -> ValidationResult:
    r = ValidationResult()
    host = (host or "").strip()
    if not host:
        r.error(fld, "Host không được để trống")
        return r
    if _UNSAFE_HOST_RE.search(host):
        # Chặn ở đây thay vì escape lúc ghi file: giá trị này đi vào
        # environment.d và apt.conf, an toàn nhất là không cho vào từ đầu.
        r.error(fld, "Host chứa ký tự không hợp lệ")
    elif len(host) > 253:
        r.error(fld, "Host quá dài")
    return r


def validate_port(port: int, fld: str = "port") -> ValidationResult:
    r = ValidationResult()
    if not isinstance(port, int) or isinstance(port, bool):
        r.error(fld, "Port phải là số nguyên")
    elif not 1 <= port <= 65535:
        r.error(fld, "Port phải trong khoảng 1–65535")
    return r


def validate_proxy(cfg: ProxyConfig) -> ValidationResult:
    r = ValidationResult()

    if not (cfg.name or "").strip():
        r.error("name", "Tên cấu hình không được để trống")

    if cfg.mode is ProxyMode.MANUAL:
        schemes = ["http"] if cfg.use_same_for_all else ["http", "https", "ftp", "socks"]
        any_set = False
        for scheme in schemes:
            ep = getattr(cfg, scheme)
            if not ep.host and not ep.port:
                continue                       # bỏ trống là hợp lệ với scheme phụ
            any_set = True
            r.extend(validate_proxy_host(ep.host, f"{scheme}.host"))
            r.extend(validate_port(ep.port, f"{scheme}.port"))
        if not any_set:
            r.error("http", "Chế độ Manual cần ít nhất một proxy host:port")

        if cfg.auth_enabled and not (cfg.username or "").strip():
            r.error("username", "Đã bật xác thực nhưng chưa có tên đăng nhập")

    elif cfg.mode is ProxyMode.AUTO:
        url = (cfg.pac_url or "").strip()
        if not url:
            r.error("pac_url", "Chế độ Auto cần URL của file PAC")
        elif not re.match(r"^(https?|file)://\S+$", url):
            r.error("pac_url", "URL PAC phải bắt đầu bằng http://, https:// hoặc file://")

    for host in cfg.ignore_hosts:
        if _UNSAFE_HOST_RE.search(host):
            r.error("ignore_hosts", f"'{host}' chứa ký tự không hợp lệ")
            break

    return r


# ─────────────────────────────────────────────────────────────────────────────
# Netmask ↔ prefix
#
# Cài đặt của Ubuntu hiển thị route theo dạng Netmask, và người dùng mạng doanh
# nghiệp quen nghĩ theo netmask hơn là prefix. Nhưng NetworkManager và kernel chỉ
# nhận prefix, nên phải chuyển đổi hai chiều.
# ─────────────────────────────────────────────────────────────────────────────

#: Netmask mặc định khi thêm route mới.
DEFAULT_NETMASK = "255.0.0.0"


def prefix_to_netmask(prefix: int) -> str:
    if not 0 <= prefix <= 32:
        return ""
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return ".".join(str((bits >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def netmask_to_prefix(text: str) -> int | None:
    """Nhận cả "255.255.0.0" và "16". `None` nếu không hợp lệ.

    Netmask phải là dãy bit 1 liền nhau rồi toàn 0: 255.255.254.0 hợp lệ, còn
    255.0.255.0 thì không — kernel không có cách nào biểu diễn nó bằng prefix.
    """
    raw = text.strip()
    if not raw:
        return None

    if raw.isdigit():
        prefix = int(raw)
        return prefix if 0 <= prefix <= 32 else None

    parts = raw.split(".")
    if len(parts) != 4:
        return None
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return None
    if any(not 0 <= o <= 255 for o in octets):
        return None

    bits = 0
    for octet in octets:
        bits = (bits << 8) | octet
    if bits == 0:
        return 0
    inverted = ~bits & 0xFFFFFFFF
    if inverted & (inverted + 1):
        return None         # có bit 1 xen giữa các bit 0 → không phải netmask
    return 32 - inverted.bit_length()
