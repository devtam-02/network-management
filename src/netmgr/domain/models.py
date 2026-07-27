"""Model nghiệp vụ — §4.4 tài liệu phân tích.

Các dataclass ở đây là biểu diễn *của app*, không phải object của libnm. Tầng
`infra.nm_facade` chịu trách nhiệm chuyển đổi hai chiều. Nhờ tách như vậy, mọi
thứ dưới đây test được mà không cần NetworkManager.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

# ─────────────────────────────────────────────────────────────────────────────
# Enum
# ─────────────────────────────────────────────────────────────────────────────


class ConnType(str, Enum):
    ETHERNET = "802-3-ethernet"
    WIFI = "802-11-wireless"
    OTHER = "other"


class Ipv4Method(str, Enum):
    """Ánh xạ 1-1 với `ipv4.method` của NetworkManager (§3.4)."""

    AUTO = "auto"
    MANUAL = "manual"
    LINK_LOCAL = "link-local"
    DISABLED = "disabled"
    SHARED = "shared"

    @property
    def is_automatic(self) -> bool:
        """"Automatic mode" theo cách người dùng hiểu."""
        return self is Ipv4Method.AUTO


class RouteSource(str, Enum):
    """Route tĩnh sửa được; route từ DHCP/kernel chỉ đọc (FR-R1)."""

    STATIC = "static"
    DHCP = "dhcp"
    KERNEL = "kernel"

    @property
    def editable(self) -> bool:
        return self is RouteSource.STATIC


class DeviceState(str, Enum):
    UNKNOWN = "unknown"
    UNMANAGED = "unmanaged"
    UNAVAILABLE = "unavailable"      # vd: cáp rút, radio tắt
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEACTIVATING = "deactivating"
    FAILED = "failed"

    @property
    def is_busy(self) -> bool:
        return self in (DeviceState.CONNECTING, DeviceState.DEACTIVATING)


class ProxyMode(str, Enum):
    NONE = "none"
    MANUAL = "manual"
    AUTO = "auto"       # PAC


class ProxyLayerId(str, Enum):
    """Các lớp proxy độc lập — §3.1."""

    DESKTOP = "desktop"     # L1: gsettings org.gnome.system.proxy
    ENVIRONMENT = "env"     # L2: ~/.config/environment.d
    APT = "apt"             # L3: cần root — v2
    DOCKER = "docker"       # L4: v2


class BindingAction(str, Enum):
    ACTIVATE = "activate"
    DISCONNECT = "disconnect"
    LEAVE_ALONE = "leave_alone"


class PermissionState(str, Enum):
    """Quyền polkit — BA trạng thái, không phải hai (§4.6).

    NetworkManager nạp permission bất đồng bộ: ngay sau khi tạo client, mọi
    permission đều là UNKNOWN và chỉ thành YES/NO sau khi main loop chạy vài
    trăm ms. Nếu coi UNKNOWN là "không có quyền" thì toàn bộ UI sẽ bị xám lúc
    khởi động rồi mới sáng lại — vừa xấu vừa gây hiểu nhầm.
    """

    YES = "yes"          # được phép
    AUTH = "auth"        # được phép, nhưng sẽ hiện hộp thoại xác thực
    NO = "no"            # bị từ chối
    UNKNOWN = "unknown"  # chưa biết — chưa nạp xong

    @property
    def allowed(self) -> bool:
        return self in (PermissionState.YES, PermissionState.AUTH)

    @property
    def needs_auth_prompt(self) -> bool:
        return self is PermissionState.AUTH

    @property
    def should_block_ui(self) -> bool:
        """Chỉ chặn UI khi CHẮC CHẮN bị từ chối, không chặn khi chưa biết."""
        return self is PermissionState.NO


# ─────────────────────────────────────────────────────────────────────────────
# IPv4
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Ipv4Address:
    address: str
    prefix: int

    def __str__(self) -> str:
        return f"{self.address}/{self.prefix}"


@dataclass(frozen=True, slots=True)
class Ipv4Route:
    """Một route tĩnh IPv4 (§3.3).

    `enabled` là metadata riêng của app (FR-R5) — NetworkManager không có khái
    niệm này, nên route bị tắt sẽ đơn giản là không được ghi xuống khi apply.
    """

    dest: str
    prefix: int
    next_hop: str | None = None
    metric: int | None = None
    table: int | None = None
    src: str | None = None
    onlink: bool = False
    enabled: bool = True
    source: RouteSource = RouteSource.STATIC

    @property
    def editable(self) -> bool:
        return self.source.editable

    @property
    def is_default(self) -> bool:
        return self.prefix == 0

    @property
    def cidr(self) -> str:
        return f"{self.dest}/{self.prefix}"

    def identity(self) -> tuple[str, int, int]:
        """Khoá định danh để phát hiện trùng — (dest, prefix, table)."""
        return (self.dest, self.prefix, self.table or 0)

    def __str__(self) -> str:
        parts = [self.cidr]
        if self.next_hop:
            parts += ["via", self.next_hop]
        else:
            parts.append("(on-link)")
        if self.metric is not None:
            parts += ["metric", str(self.metric)]
        if self.table:
            parts += ["table", str(self.table)]
        return " ".join(parts)


@dataclass(slots=True)
class Ipv4Config:
    method: Ipv4Method = Ipv4Method.AUTO
    addresses: list[Ipv4Address] = field(default_factory=list)
    gateway: str | None = None
    dns: list[str] = field(default_factory=list)
    dns_search: list[str] = field(default_factory=list)
    routes: list[Ipv4Route] = field(default_factory=list)
    ignore_auto_dns: bool = False
    ignore_auto_routes: bool = False
    never_default: bool = False
    route_metric: int | None = None
    may_fail: bool = True

    @property
    def static_routes(self) -> list[Ipv4Route]:
        return [r for r in self.routes if r.source is RouteSource.STATIC]

    def copy(self) -> Ipv4Config:
        """Bản sao sâu vừa đủ — Ipv4Route/Ipv4Address là frozen nên share được."""
        return replace(
            self,
            addresses=list(self.addresses),
            dns=list(self.dns),
            dns_search=list(self.dns_search),
            routes=list(self.routes),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Device & Connection
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class WifiStatus:
    """Trạng thái Wi-Fi chỉ-đọc (FR-F6).

    v1 không quét AP (§3.2.1) — dữ liệu ở đây lấy từ AP *đang kết nối*.
    """

    ssid: str | None = None
    bssid: str | None = None
    strength: int = 0            # 0..100
    frequency_mhz: int = 0
    bitrate_kbps: int = 0
    security: str = ""

    @property
    def band(self) -> str:
        if self.frequency_mhz >= 5925:
            return "6 GHz"
        if self.frequency_mhz >= 4900:
            return "5 GHz"
        if self.frequency_mhz >= 2400:
            return "2.4 GHz"
        return ""

    @property
    def signal_bars(self) -> int:
        """0..4 — dùng để chọn icon."""
        if self.strength >= 80:
            return 4
        if self.strength >= 55:
            return 3
        if self.strength >= 30:
            return 2
        if self.strength > 0:
            return 1
        return 0


@dataclass(slots=True)
class DeviceInfo:
    interface: str
    type: ConnType
    state: DeviceState = DeviceState.UNKNOWN
    mac: str | None = None
    # Runtime (đang áp dụng thực tế), khác với cấu hình đã lưu trong profile
    ip4_addresses: list[Ipv4Address] = field(default_factory=list)
    ip4_gateway: str | None = None
    ip4_dns: list[str] = field(default_factory=list)
    ip4_routes: list[Ipv4Route] = field(default_factory=list)
    speed_mbps: int = 0
    carrier: bool = True         # ethernet: có cáp hay không
    wifi: WifiStatus | None = None
    active_connection_uuid: str | None = None

    @property
    def is_connected(self) -> bool:
        return self.state is DeviceState.CONNECTED


@dataclass(frozen=True, slots=True)
class OpResult:
    """Kết quả một thao tác ghi (mạng hoặc proxy).

    Nằm ở domain để cả `infra.nm_facade` lẫn các `ProxyLayer` dùng chung, và để
    tầng trên không bao giờ phải bắt `GLib.Error` hay `OSError` (§4.3).
    """

    ok: bool
    message: str = ""

    @classmethod
    def success(cls, message: str = "") -> OpResult:
        return cls(True, message)

    @classmethod
    def failure(cls, exc: Exception, context: str) -> OpResult:
        detail = getattr(exc, "message", None) or str(exc)
        return cls(False, f"{context}: {detail}")


@dataclass(slots=True)
class Permissions:
    """Những gì polkit cho phép app làm (§4.6)."""

    modify_system: PermissionState = PermissionState.UNKNOWN
    modify_own: PermissionState = PermissionState.UNKNOWN
    network_control: PermissionState = PermissionState.UNKNOWN
    enable_disable_wifi: PermissionState = PermissionState.UNKNOWN
    checkpoint_rollback: PermissionState = PermissionState.UNKNOWN

    @property
    def loaded(self) -> bool:
        return self.modify_system is not PermissionState.UNKNOWN

    @property
    def can_edit_connections(self) -> bool:
        """Chưa nạp xong thì cứ cho thao tác — thất bại sẽ báo lỗi rõ ràng."""
        return not self.modify_system.should_block_ui

    @property
    def can_control_network(self) -> bool:
        return not self.network_control.should_block_ui

    @property
    def can_toggle_wifi(self) -> bool:
        return not self.enable_disable_wifi.should_block_ui


@dataclass(slots=True)
class ConnectionProfile:
    """Ánh xạ 1-1 với `NM.RemoteConnection` — xem thuật ngữ §1.1.1."""

    uuid: str
    id: str
    type: ConnType
    interface_name: str | None = None
    autoconnect: bool = True
    autoconnect_priority: int = 0
    ipv4: Ipv4Config = field(default_factory=Ipv4Config)
    ssid: str | None = None                 # chỉ với Wi-Fi
    is_active: bool = False
    device_interface: str | None = None     # device đang chạy profile này
    managed_by_app: bool = False            # app tạo cho một Bộ cấu hình (Mô hình C)

    @property
    def display_name(self) -> str:
        return self.id or self.ssid or self.uuid[:8]


# ─────────────────────────────────────────────────────────────────────────────
# Proxy
# ─────────────────────────────────────────────────────────────────────────────


DEFAULT_IGNORE_HOSTS = ["localhost", "127.0.0.0/8", "::1"]


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    host: str = ""
    port: int = 0

    @property
    def is_set(self) -> bool:
        return bool(self.host) and self.port > 0

    def __str__(self) -> str:
        return f"{self.host}:{self.port}" if self.is_set else "—"


@dataclass(slots=True)
class ProxyConfig:
    """Cấu hình proxy (§3.1). Mật khẩu KHÔNG nằm ở đây — xem `secrets`."""

    id: str = ""
    name: str = ""
    mode: ProxyMode = ProxyMode.NONE
    http: ProxyEndpoint = field(default_factory=ProxyEndpoint)
    https: ProxyEndpoint = field(default_factory=ProxyEndpoint)
    ftp: ProxyEndpoint = field(default_factory=ProxyEndpoint)
    socks: ProxyEndpoint = field(default_factory=ProxyEndpoint)
    use_same_for_all: bool = True
    pac_url: str = ""
    ignore_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_HOSTS))
    auth_enabled: bool = False
    username: str = ""
    layers: set[ProxyLayerId] = field(
        default_factory=lambda: {ProxyLayerId.DESKTOP, ProxyLayerId.ENVIRONMENT}
    )

    @property
    def is_enabled(self) -> bool:
        return self.mode is not ProxyMode.NONE

    #: `use_same_for_all` chỉ áp cho các scheme cùng nói giao thức HTTP.
    #: SOCKS là giao thức KHÁC HẲN: gửi traffic SOCKS5 tới cổng của một HTTP
    #: proxy chỉ dẫn tới lỗi kết nối, nên không bao giờ suy ra socks từ http.
    _SAME_PROXY_SCHEMES = ("http", "https", "ftp")

    def effective(self, scheme: str) -> ProxyEndpoint:
        """Endpoint thực tế cho một scheme, có tính `use_same_for_all`."""
        if self.use_same_for_all and scheme in self._SAME_PROXY_SCHEMES:
            return self.http
        return {"http": self.http, "https": self.https,
                "ftp": self.ftp, "socks": self.socks}.get(scheme, ProxyEndpoint())

    def summary(self) -> str:
        if self.mode is ProxyMode.NONE:
            return "Tắt"
        if self.mode is ProxyMode.AUTO:
            return f"PAC: {self.pac_url}" if self.pac_url else "PAC (chưa đặt URL)"
        return str(self.http)


# ─────────────────────────────────────────────────────────────────────────────
# Bộ cấu hình (F6)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Binding:
    """Một device được Bộ cấu hình xử lý thế nào (§3.5.2)."""

    device_match: str                       # "enp3s0" | "any-ethernet" | "any-wifi"
    action: BindingAction = BindingAction.LEAVE_ALONE
    connection_uuid: str | None = None
    owned: bool = False                     # app sở hữu → xoá cùng Bộ cấu hình
    required: bool = False                  # True → thiếu thiết bị là lỗi

    def matches(self, device: DeviceInfo) -> bool:
        if self.device_match == "any-ethernet":
            return device.type is ConnType.ETHERNET
        if self.device_match == "any-wifi":
            return device.type is ConnType.WIFI
        return self.device_match == device.interface


@dataclass(slots=True)
class NetworkSnapshot:
    """Ảnh chụp trạng thái mạng tại một thời điểm — thứ UI đọc.

    Nằm ở domain chứ không phải infra vì đây là dữ liệu thuần: nhờ vậy test cho
    menu builder và tray status chạy được mà không cần typelib NM.
    """

    devices: list[DeviceInfo] = field(default_factory=list)
    connections: list[ConnectionProfile] = field(default_factory=list)
    networking_enabled: bool = True
    wifi_enabled: bool = True
    wifi_hardware_enabled: bool = True
    permissions: Permissions = field(default_factory=Permissions)

    @property
    def ethernet_devices(self) -> list[DeviceInfo]:
        return [d for d in self.devices if d.type is ConnType.ETHERNET]

    @property
    def wifi_devices(self) -> list[DeviceInfo]:
        return [d for d in self.devices if d.type is ConnType.WIFI]

    @property
    def has_wifi_hardware(self) -> bool:
        return bool(self.wifi_devices)

    def saved_wifi_connections(self) -> list[ConnectionProfile]:
        """FR-F2 — v1 chỉ làm việc với Wi-Fi ĐÃ LƯU, không quét AP (§3.2.1)."""
        return [c for c in self.connections if c.type is ConnType.WIFI]

    def ethernet_connections(self) -> list[ConnectionProfile]:
        return [c for c in self.connections if c.type is ConnType.ETHERNET]

    def connection_by_uuid(self, uuid: str) -> ConnectionProfile | None:
        return next((c for c in self.connections if c.uuid == uuid), None)

    def device_by_interface(self, iface: str) -> DeviceInfo | None:
        return next((d for d in self.devices if d.interface == iface), None)

    def primary_device(self) -> DeviceInfo | None:
        """Device 'đáng hiển thị nhất' trên tray: ưu tiên ethernet đang nối."""
        connected = [d for d in self.devices if d.is_connected]
        if not connected:
            busy = [d for d in self.devices if d.state.is_busy]
            return busy[0] if busy else None
        connected.sort(key=lambda d: 0 if d.type is ConnType.ETHERNET else 1)
        return connected[0]


@dataclass(slots=True)
class Profile:
    """"Bộ cấu hình" — một bối cảnh mạng + proxy trọn vẹn (F6)."""

    id: str
    name: str
    icon: str = "🌐"
    description: str = ""
    order: int = 0
    bindings: list[Binding] = field(default_factory=list)
    wifi_enabled: bool | None = None        # None = không đụng tới
    #: id của cấu hình proxy; "" = tắt proxy; None = không đụng tới proxy.
    proxy_id: str | None = None
    broken_reason: str | None = None        # tính runtime, không lưu xuống file

    @property
    def is_broken(self) -> bool:
        return self.broken_reason is not None

    @property
    def label(self) -> str:
        return f"{self.icon} {self.name}".strip()

    @property
    def touches_proxy(self) -> bool:
        return self.proxy_id is not None

    @property
    def disables_proxy(self) -> bool:
        return self.proxy_id == ""

    def binding_for(self, interface: str) -> Binding | None:
        return next((b for b in self.bindings if b.device_match == interface), None)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


@dataclass(slots=True)
class ApplyStep:
    """Một bước trong luồng áp dụng Bộ cấu hình (§3.5.4).

    UI hiển thị tiến trình theo từng bước chứ không phải spinner vô định: bước
    kích hoạt connection có thể mất 10–30 giây và người dùng cần biết đang chờ gì.
    """

    key: str
    label: str
    status: StepStatus = StepStatus.PENDING
    detail: str = ""

    @property
    def finished(self) -> bool:
        return self.status not in (StepStatus.PENDING, StepStatus.RUNNING)


@dataclass(slots=True)
class ProfileApplyReport:
    profile_name: str
    steps: list[ApplyStep] = field(default_factory=list)
    rolled_back: bool = False

    @property
    def ok(self) -> bool:
        return not self.rolled_back and not self.failed_steps

    @property
    def failed_steps(self) -> list[ApplyStep]:
        return [s for s in self.steps if s.status is StepStatus.FAILED]

    def step(self, key: str) -> ApplyStep | None:
        return next((s for s in self.steps if s.key == key), None)

    def summary(self) -> str:
        if self.ok:
            return f"Đã áp dụng '{self.profile_name}'"
        failed = self.failed_steps
        reason = failed[0].detail or failed[0].label if failed else "không rõ nguyên nhân"
        if self.rolled_back:
            return f"'{self.profile_name}' thất bại, đã khôi phục: {reason}"
        return f"'{self.profile_name}' áp dụng chưa trọn vẹn: {reason}"
