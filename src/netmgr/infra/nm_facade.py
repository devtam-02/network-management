"""NMFacade — nơi DUY NHẤT trong app biết tới libnm (§4.3).

Mọi thứ ra khỏi module này đều là dataclass thuần ở `domain.models`. Nhờ vậy
tầng service/UI test được bằng `FakeNMFacade` mà không cần D-Bus, và nếu sau
này đổi backend thì chỉ sửa ở đây.

Đọc dữ liệu là đồng bộ (rẻ, chỉ là truy cập cache trong tiến trình của libnm).
Mọi thao tác GHI sẽ là async — xem `services/` ở các giai đoạn sau.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib, NM  # noqa: E402

from .kernel_rules import read_rules
from ..domain.models import (
    ConnectionProfile,
    ConnType,
    DeviceInfo,
    DeviceState,
    Ipv4Address,
    Ipv4Config,
    Ipv4Method,
    Ipv4Route,
    NetworkSnapshot,
    OpResult,
    PermissionState,
    Permissions,
    RouteSource,
    RuntimeIpv4,
    SystemRoute,
    WifiStatus,
)

__all__ = ["NMFacade", "NMUnavailableError", "NetworkSnapshot", "OpResult"]

log = logging.getLogger(__name__)

#: Gom các signal dồn dập của NM lại trước khi báo lên UI (§4.8).
CHANGE_DEBOUNCE_MS = 200


#: Callback nhận kết quả thao tác ghi.
OpCallback = Callable[[OpResult], None]


# ─────────────────────────────────────────────────────────────────────────────
# Chuyển đổi enum
# ─────────────────────────────────────────────────────────────────────────────

_DEVICE_STATE_MAP = {
    NM.DeviceState.UNKNOWN: DeviceState.UNKNOWN,
    NM.DeviceState.UNMANAGED: DeviceState.UNMANAGED,
    NM.DeviceState.UNAVAILABLE: DeviceState.UNAVAILABLE,
    NM.DeviceState.DISCONNECTED: DeviceState.DISCONNECTED,
    NM.DeviceState.PREPARE: DeviceState.CONNECTING,
    NM.DeviceState.CONFIG: DeviceState.CONNECTING,
    NM.DeviceState.NEED_AUTH: DeviceState.CONNECTING,
    NM.DeviceState.IP_CONFIG: DeviceState.CONNECTING,
    NM.DeviceState.IP_CHECK: DeviceState.CONNECTING,
    NM.DeviceState.SECONDARIES: DeviceState.CONNECTING,
    NM.DeviceState.ACTIVATED: DeviceState.CONNECTED,
    NM.DeviceState.DEACTIVATING: DeviceState.DEACTIVATING,
    NM.DeviceState.FAILED: DeviceState.FAILED,
}

_IPV4_METHOD_MAP = {
    "auto": Ipv4Method.AUTO,
    "manual": Ipv4Method.MANUAL,
    "link-local": Ipv4Method.LINK_LOCAL,
    "disabled": Ipv4Method.DISABLED,
    "shared": Ipv4Method.SHARED,
}


def _conn_type(raw: str | None) -> ConnType:
    try:
        return ConnType(raw)
    except ValueError:
        return ConnType.OTHER


def _ssid_to_str(ssid) -> str | None:
    """SSID trong libnm là GLib.Bytes, có thể không phải UTF-8 hợp lệ."""
    if ssid is None:
        return None
    try:
        return NM.utils_ssid_to_utf8(ssid.get_data())
    except Exception:  # noqa: BLE001 — SSID rác không được làm sập app
        return None


def _ap_security(ap) -> str:
    """Suy ra chuẩn bảo mật từ flag của AP (chỉ để hiển thị — FR-F6)."""
    rsn = ap.get_rsn_flags()
    wpa = ap.get_wpa_flags()
    # Tên enum bắt đầu bằng chữ số nên không viết `NM.80211...` trực tiếp được.
    sec_flags = getattr(NM, "80211ApSecurityFlags")
    ap_flags = getattr(NM, "80211ApFlags")

    if rsn & sec_flags.KEY_MGMT_SAE:
        return "WPA3"
    if rsn & sec_flags.KEY_MGMT_802_1X or wpa & sec_flags.KEY_MGMT_802_1X:
        return "WPA2 Enterprise"
    if rsn:
        return "WPA2"
    if wpa:
        return "WPA"
    if ap.get_flags() & ap_flags.PRIVACY:
        return "WEP"
    return "Mở"


# ─────────────────────────────────────────────────────────────────────────────
# Chuyển đổi IPv4
# ─────────────────────────────────────────────────────────────────────────────


def _route_from_nm(nm_route, source: RouteSource) -> Ipv4Route:
    metric = nm_route.get_metric()
    attrs: dict[str, object] = {}
    for name in nm_route.get_attribute_names():
        variant = nm_route.get_attribute(name)
        if variant is not None:
            attrs[name] = variant.unpack()

    table = attrs.get("table")
    return Ipv4Route(
        dest=nm_route.get_dest(),
        prefix=nm_route.get_prefix(),
        next_hop=nm_route.get_next_hop() or None,
        metric=None if metric is None or metric < 0 else int(metric),
        table=int(table) if table else None,
        src=str(attrs["src"]) if attrs.get("src") else None,
        onlink=bool(attrs.get("onlink", False)),
        source=source,
    )


def _to_nm_route(route: Ipv4Route) -> NM.IPRoute:
    """Ngược lại — dùng khi ghi cấu hình (§4.5)."""
    nm_route = NM.IPRoute.new(
        socket.AF_INET,
        route.dest,
        route.prefix,
        route.next_hop,
        -1 if route.metric is None else route.metric,
    )
    if route.table:
        nm_route.set_attribute("table", GLib.Variant("u", route.table))
    if route.src:
        nm_route.set_attribute("src", GLib.Variant("s", route.src))
    if route.onlink:
        nm_route.set_attribute("onlink", GLib.Variant("b", True))
    return nm_route


def _apply_ipv4_to_setting(setting, config: Ipv4Config) -> None:
    """Ghi `Ipv4Config` của app vào `NM.SettingIPConfig`.

    Chỉ ghi route đang bật: `enabled=False` là metadata riêng của app (FR-R5),
    NetworkManager không có khái niệm đó nên route tắt đơn giản là không xuống.
    """
    setting.set_property("method", config.method.value)

    setting.clear_addresses()
    for addr in config.addresses:
        setting.add_address(NM.IPAddress.new(socket.AF_INET, addr.address, addr.prefix))

    # Gateway chỉ có nghĩa ở chế độ manual; để lại ở chế độ auto sẽ khiến
    # `verify()` từ chối cả connection.
    setting.set_property(
        "gateway", config.gateway if config.method is Ipv4Method.MANUAL else None
    )

    setting.clear_dns()
    for server in config.dns:
        setting.add_dns(server)
    setting.clear_dns_searches()
    for domain in config.dns_search:
        setting.add_dns_search(domain)

    setting.clear_routes()
    for route in config.routes:
        if route.enabled:
            setting.add_route(_to_nm_route(route))

    setting.clear_routing_rules()
    for text in config.routing_rules:
        rule = parse_routing_rule(text)
        if rule is not None:
            setting.add_routing_rule(rule)

    setting.set_property("ignore-auto-dns", config.ignore_auto_dns)
    setting.set_property("ignore-auto-routes", config.ignore_auto_routes)
    setting.set_property("never-default", config.never_default)
    setting.set_property("may-fail", config.may_fail)
    setting.set_property(
        "route-metric", -1 if config.route_metric is None else config.route_metric
    )
    # 0 = để NM tự chọn theo loại connection. Chỉ ghi khi người dùng đặt tường minh.
    setting.set_property(
        "dns-priority", 0 if config.dns_priority is None else config.dns_priority
    )


def parse_routing_rule(text: str):
    """Chuỗi `ip rule` → `NM.IPRoutingRule`, hoặc None nếu không parse được."""
    try:
        return NM.IPRoutingRule.from_string(
            text.strip(), NM.IPRoutingRuleAsStringFlags.AF_INET, None
        )
    except Exception:  # noqa: BLE001
        return None


def validate_routing_rule(text: str) -> str | None:
    """Trả về thông báo lỗi, hoặc None nếu hợp lệ.

    Dùng chính bộ parse của libnm thay vì tự viết: cú pháp `ip rule` nhiều biến
    thể, tự parse chắc chắn sẽ lệch với thứ NetworkManager thực sự chấp nhận.
    """
    if not text.strip():
        return "Luật không được để trống"
    try:
        NM.IPRoutingRule.from_string(
            text.strip(), NM.IPRoutingRuleAsStringFlags.AF_INET, None
        )
    except GLib.Error as exc:
        return exc.message
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def format_routing_rule(rule) -> str:
    try:
        return rule.to_string(NM.IPRoutingRuleAsStringFlags.AF_INET, None)
    except Exception:  # noqa: BLE001
        return ""


def _ipv4_from_setting(setting) -> Ipv4Config:
    """Cấu hình IPv4 ĐÃ LƯU trong connection profile."""
    if setting is None:
        return Ipv4Config()

    addresses = [
        Ipv4Address(a.get_address(), a.get_prefix())
        for a in (setting.get_address(i) for i in range(setting.get_num_addresses()))
    ]
    dns = [setting.get_dns(i) for i in range(setting.get_num_dns())]
    searches = [setting.get_dns_search(i) for i in range(setting.get_num_dns_searches())]
    routes = [
        _route_from_nm(setting.get_route(i), RouteSource.STATIC)
        for i in range(setting.get_num_routes())
    ]

    rules = [
        format_routing_rule(setting.get_routing_rule(i))
        for i in range(setting.get_num_routing_rules())
    ]

    metric = setting.get_route_metric()
    priority = setting.get_dns_priority()
    return Ipv4Config(
        method=_IPV4_METHOD_MAP.get(setting.get_method() or "auto", Ipv4Method.AUTO),
        addresses=addresses,
        gateway=setting.get_gateway(),
        dns=dns,
        dns_search=searches,
        dns_priority=None if not priority else int(priority),
        routes=routes,
        routing_rules=[r for r in rules if r],
        ignore_auto_dns=setting.get_ignore_auto_dns(),
        ignore_auto_routes=setting.get_ignore_auto_routes(),
        never_default=setting.get_never_default(),
        route_metric=None if metric is None or metric < 0 else int(metric),
        may_fail=setting.get_may_fail(),
    )


def _runtime_ip4(device) -> tuple[list[Ipv4Address], str | None, list[str], list[Ipv4Route]]:
    """Cấu hình IPv4 ĐANG ÁP DỤNG THỰC TẾ trên device.

    Khác với cấu hình đã lưu: chỗ này gồm cả thứ DHCP đẩy về. Route lấy ở đây
    được đánh dấu DHCP → UI hiển thị chỉ-đọc (FR-R1).
    """
    cfg = device.get_ip4_config()
    if cfg is None:
        return [], None, [], []

    addresses = [Ipv4Address(a.get_address(), a.get_prefix()) for a in cfg.get_addresses()]
    routes = [_route_from_nm(r, RouteSource.DHCP) for r in cfg.get_routes()]
    return addresses, cfg.get_gateway(), list(cfg.get_nameservers()), routes


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Facade
# ─────────────────────────────────────────────────────────────────────────────


class NMUnavailableError(RuntimeError):
    """Không nói chuyện được với NetworkManager."""


class NMFacade:
    def __init__(self, client: NM.Client | None = None) -> None:
        try:
            self._client = client or NM.Client.new(None)
        except GLib.Error as exc:  # pragma: no cover — cần môi trường hỏng
            raise NMUnavailableError(f"Không kết nối được NetworkManager: {exc}") from exc
        if self._client is None:  # pragma: no cover
            raise NMUnavailableError("NM.Client.new() trả về None")

        self._subscribers: list[Callable[[], None]] = []
        self._debounce_source: int | None = None
        self._signal_ids: list[tuple[object, int]] = []
        #: Tách riêng để nối lại được khi danh sách device thay đổi.
        self._device_signal_ids: list[tuple[object, int]] = []

    # ── đọc ─────────────────────────────────────────────────────────────────

    @property
    def nm_version(self) -> str:
        return self._client.get_version() or "?"

    def snapshot(self) -> NetworkSnapshot:
        return NetworkSnapshot(
            devices=[self._device_info(d) for d in self._managed_devices()],
            connections=[self._connection_profile(c) for c in self._client.get_connections()],
            system_routes=self._system_routes(),
            routing_rules=read_rules(),
            networking_enabled=self._client.networking_get_enabled(),
            wifi_enabled=self._client.wireless_get_enabled(),
            wifi_hardware_enabled=self._client.wireless_hardware_get_enabled(),
            permissions=self._permissions(),
        )

    def _managed_devices(self) -> list[NM.Device]:
        """Chỉ lấy ethernet + wifi thật, bỏ loopback/veth/docker0 (§1.4)."""
        wanted = (NM.DeviceType.ETHERNET, NM.DeviceType.WIFI)
        out = []
        for dev in self._client.get_devices():
            if dev.get_device_type() not in wanted:
                continue
            if dev.get_state() == NM.DeviceState.UNMANAGED:
                continue
            out.append(dev)
        return out

    def _system_routes(self) -> list[SystemRoute]:
        """Route của MỌI device, không chỉ ethernet/wifi.

        Bỏ qua docker0/bridge/VPN sẽ khiến việc tra cứu "địa chỉ này đi đường
        nào" trả lời sai — chúng vẫn nằm trong bảng định tuyến của kernel.
        """
        out: list[SystemRoute] = []
        for dev in self._client.get_devices():
            cfg = dev.get_ip4_config()
            if cfg is None:
                continue
            iface = dev.get_iface() or "?"
            for nm_route in cfg.get_routes():
                out.append(SystemRoute(iface, _route_from_nm(nm_route, RouteSource.KERNEL)))
        return out

    def _device_info(self, dev: NM.Device) -> DeviceInfo:
        dev_type = (
            ConnType.ETHERNET
            if dev.get_device_type() == NM.DeviceType.ETHERNET
            else ConnType.WIFI
        )
        addresses, gateway, dns, routes = _runtime_ip4(dev)

        active = dev.get_active_connection()
        info = DeviceInfo(
            interface=dev.get_iface(),
            type=dev_type,
            state=_DEVICE_STATE_MAP.get(dev.get_state(), DeviceState.UNKNOWN),
            mac=dev.get_hw_address(),
            hw_path=dev.get_path() or "",
            ip4_addresses=addresses,
            ip4_gateway=gateway,
            ip4_dns=dns,
            ip4_routes=routes,
            active_connection_uuid=active.get_uuid() if active else None,
        )

        if dev_type is ConnType.ETHERNET:
            info.speed_mbps = dev.get_speed() if hasattr(dev, "get_speed") else 0
            info.carrier = dev.get_carrier() if hasattr(dev, "get_carrier") else True
        else:
            info.wifi = self._wifi_status(dev)
        return info

    def _wifi_status(self, dev) -> WifiStatus | None:
        """Chỉ đọc AP ĐANG KẾT NỐI — v1 không duyệt danh sách AP (§3.2.1)."""
        ap = dev.get_active_access_point() if hasattr(dev, "get_active_access_point") else None
        if ap is None:
            return None
        return WifiStatus(
            ssid=_ssid_to_str(ap.get_ssid()),
            bssid=ap.get_bssid(),
            strength=int(ap.get_strength()),
            frequency_mhz=int(ap.get_frequency()),
            bitrate_kbps=int(dev.get_bitrate()) if hasattr(dev, "get_bitrate") else 0,
            security=_ap_security(ap),
        )

    def _connection_profile(self, conn: NM.RemoteConnection) -> ConnectionProfile:
        s_con = conn.get_setting_connection()
        ctype = _conn_type(conn.get_connection_type())

        ssid = None
        if ctype is ConnType.WIFI:
            s_wifi = conn.get_setting_wireless()
            if s_wifi is not None:
                ssid = _ssid_to_str(s_wifi.get_ssid())

        active_uuid_to_iface = {
            ac.get_uuid(): (ac.get_devices()[0].get_iface() if ac.get_devices() else None)
            for ac in self._client.get_active_connections()
        }
        uuid = conn.get_uuid()

        return ConnectionProfile(
            uuid=uuid,
            id=conn.get_id(),
            type=ctype,
            interface_name=s_con.get_interface_name() if s_con else None,
            autoconnect=s_con.get_autoconnect() if s_con else True,
            autoconnect_priority=s_con.get_autoconnect_priority() if s_con else 0,
            ipv4=_ipv4_from_setting(conn.get_setting_ip4_config()),
            ssid=ssid,
            is_active=uuid in active_uuid_to_iface,
            device_interface=active_uuid_to_iface.get(uuid),
            managed_by_app=(conn.get_id() or "").startswith("netmgr:"),
        )

    _PERMISSION_RESULT_MAP = {
        NM.ClientPermissionResult.YES: PermissionState.YES,
        NM.ClientPermissionResult.AUTH: PermissionState.AUTH,
        NM.ClientPermissionResult.NO: PermissionState.NO,
        NM.ClientPermissionResult.UNKNOWN: PermissionState.UNKNOWN,
    }

    def _permission(self, perm) -> PermissionState:
        result = self._client.get_permission_result(perm)
        return self._PERMISSION_RESULT_MAP.get(result, PermissionState.UNKNOWN)

    def _permissions(self) -> Permissions:
        p = NM.ClientPermission
        return Permissions(
            modify_system=self._permission(p.SETTINGS_MODIFY_SYSTEM),
            modify_own=self._permission(p.SETTINGS_MODIFY_OWN),
            network_control=self._permission(p.NETWORK_CONTROL),
            enable_disable_wifi=self._permission(p.ENABLE_DISABLE_WIFI),
            checkpoint_rollback=self._permission(p.CHECKPOINT_ROLLBACK),
        )

    # ── ghi ─────────────────────────────────────────────────────────────────

    def set_wifi_enabled(self, enabled: bool, callback: OpCallback | None = None) -> None:
        """Bật/tắt radio Wi-Fi (FR-F1).

        `NM.Client.wireless_set_enabled()` đã deprecated; cách thay thế là ghi
        thẳng property `WirelessEnabled` trên D-Bus, và cách này còn bất đồng bộ
        thật sự nên không chặn main loop.
        """
        try:
            self._client.dbus_set_property(
                NM.DBUS_PATH,
                NM.DBUS_INTERFACE,
                "WirelessEnabled",
                GLib.Variant("b", enabled),
                -1,
                None,
                self._on_set_property_done,
                (callback, "Bật/tắt Wi-Fi"),
            )
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, "Bật/tắt Wi-Fi"))

    def _on_set_property_done(self, client, result, user_data) -> None:
        callback, context = user_data
        try:
            client.dbus_set_property_finish(result)
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, context))
        else:
            self._report(callback, OpResult.success())

    def activate_connection(
        self,
        uuid: str | None,
        interface: str | None = None,
        callback: OpCallback | None = None,
    ) -> None:
        """Kích hoạt một connection đã lưu (FR-W3, FR-F3).

        `interface` là thiết bị BẮT BUỘC phải dùng. Bỏ trống thì để NM tự chọn —
        đúng khi người dùng bấm thẳng vào một connection trong menu.

        Nhưng khi Bộ cấu hình nói "enp1s0 → connection X" thì phải chỉ định rõ.
        Nếu để NM tự chọn, một profile không ghim `interface-name` đang chạy trên
        cổng khác sẽ được giữ nguyên chỗ cũ — người dùng bấm áp dụng mà không
        thấy gì thay đổi.
        """
        # uuid=None nghĩa là "bật thiết bị này lên bằng cấu hình phù hợp nhất",
        # đúng như khi người dùng cắm cáp. Nhờ vậy Bộ cấu hình chỉ cần biết
        # thiết bị nào bật/tắt, không phải chọn connection.
        conn = self._client.get_connection_by_uuid(uuid) if uuid else None
        if uuid and conn is None:
            self._report(
                callback, OpResult(False, f"Không tìm thấy connection {uuid[:8]}")
            )
            return

        device = None
        if interface:
            device = self._device_by_iface(interface)
            if device is None:
                self._report(
                    callback, OpResult(False, f"Không tìm thấy thiết bị {interface}")
                )
                return

        name = (conn.get_id() if conn else interface or "thiết bị") + (
            f" trên {interface}" if conn and interface else ""
        )
        self._client.activate_connection_async(
            conn, device, None, None, self._on_activate_done, (callback, name)
        )

    def _on_activate_done(self, client, result, user_data) -> None:
        callback, name = user_data
        try:
            client.activate_connection_finish(result)
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, f"Kết nối '{name}'"))
        else:
            self._report(callback, OpResult.success())

    def deactivate_connection(
        self, uuid: str, callback: OpCallback | None = None
    ) -> None:
        """Ngắt một connection đang active (FR-W3, FR-F3)."""
        active = next(
            (ac for ac in self._client.get_active_connections() if ac.get_uuid() == uuid),
            None,
        )
        if active is None:
            # Không coi là lỗi: đích đến (không kết nối) đã đạt được rồi.
            self._report(callback, OpResult.success())
            return

        self._client.deactivate_connection_async(
            active, None, self._on_deactivate_done, (callback, active.get_id())
        )

    def _on_deactivate_done(self, client, result, user_data) -> None:
        callback, name = user_data
        try:
            client.deactivate_connection_finish(result)
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, f"Ngắt '{name}'"))
        else:
            self._report(callback, OpResult.success())

    # ── áp cấu hình Ở RUNTIME (không bao giờ ghi đĩa) ───────────────────────
    #
    # App CỐ TÌNH không có đường ghi xuống đĩa. Lý do là một sự cố có thật:
    # `update2(TO_DISK)` trên Ubuntu khiến NetworkManager ghi connection ra
    # /etc/netplan/90-NM-<uuid>.yaml, và vòng chuyển đổi đó LÀM MẤT
    # `connection.interface-name`. Profile của cổng LAN biến thành profile
    # ethernet chung rồi tự bám sang cổng USB vừa cắm — hỏng cấu hình mạng của
    # người dùng theo cách rất khó lần ra.
    #
    # `reapply()` áp thẳng vào thiết bị đang chạy, không đụng gì tới đĩa. Ngắt
    # kết nối hoặc gọi `restore_runtime()` là mọi thứ trở về đúng cấu hình gốc
    # của máy.

    def apply_runtime_routes(
        self,
        interface: str,
        routes: list[Ipv4Route],
        ignore_auto: bool = True,
        callback: OpCallback | None = None,
    ) -> None:
        """Áp danh sách route tĩnh lên thiết bị đang chạy, chỉ ở runtime.

        `ignore_auto` bỏ luôn route do DHCP đẩy về. Cần thiết vì nếu không, route
        của DHCP vẫn nằm đó và tranh với route vừa đặt theo metric.
        """

        def mutate(setting) -> None:
            setting.clear_routes()
            for route in routes:
                if route.enabled:
                    setting.add_route(_to_nm_route(route))
            setting.set_property("ignore-auto-routes", ignore_auto)

        self._reapply_with(interface, mutate, callback, f"Áp route cho {interface}")

    def set_stored_automatic_routes(
        self, uuid: str, automatic: bool, callback: OpCallback | None = None
    ) -> None:
        """Ghi chế độ Automatic của route xuống cấu hình ĐÃ LƯU (tức xuống đĩa).

        Đây là ngoại lệ duy nhất của nguyên tắc "không ghi đĩa", và có lý do:
        chỉ đổi runtime thì Cài đặt của Ubuntu không thấy, và cấu hình mất mỗi
        lần thiết bị dựng lại. Chỉ ghi ĐÚNG MỘT thuộc tính; route tĩnh vẫn chỉ
        sống ở runtime.

        Trên Ubuntu, ghi xuống đĩa khiến NetworkManager viết lại
        /etc/netplan/90-NM-<uuid>.yaml — chính đường đã từng làm mất
        `connection.interface-name` và hỏng cấu hình mạng của người dùng. Nên
        sau khi ghi, hàm này kiểm lại trường đó và báo lỗi nếu nó thay đổi.
        """
        connection = self._client.get_connection_by_uuid(uuid) if self._client else None
        if connection is None:
            self._report(callback, OpResult(False, f"Không tìm thấy cấu hình {uuid}"))
            return

        setting = connection.get_setting_ip4_config()
        if setting is None:
            self._report(callback, OpResult(False, "Cấu hình không có IPv4"))
            return
        if setting.get_ignore_auto_routes() == (not automatic):
            self._report(callback, OpResult.success())     # đã đúng, đừng ghi đĩa
            return

        setting.set_property("ignore-auto-routes", not automatic)
        before = connection.get_setting_connection().get_interface_name()
        connection.commit_changes_async(
            True, None, self._on_stored_committed,
            (connection, uuid, before, callback),
        )

    def _on_stored_committed(self, connection, result, user_data) -> None:
        _conn, uuid, before, callback = user_data
        try:
            connection.commit_changes_finish(result)
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, "Lưu chế độ route"))
            return

        after = connection.get_setting_connection().get_interface_name()
        if before and after != before:
            # Đúng dấu hiệu của sự cố netplan cũ. Báo to chứ không im lặng.
            log.error(
                "GHI ĐĨA LÀM MẤT interface-name của %s: %r -> %r", uuid, before, after
            )
            self._report(callback, OpResult(
                False,
                f"Lưu xong nhưng NetworkManager làm mất interface-name "
                f"({before!r} -> {after!r}) — hãy kiểm tra /etc/netplan",
            ))
            return
        self._report(callback, OpResult.success())

    def read_runtime_ipv4(self, interface: str, callback) -> None:
        """Đọc cấu hình IPv4 ĐANG CHẠY trên thiết bị; `None` nếu không đọc được.

        Cần thiết vì `snapshot()` chỉ thấy cấu hình đã lưu. Không có hàm này thì
        app không có cách nào cho người dùng thấy Bộ cấu hình đã đổi được gì —
        và Cài đặt của Ubuntu thì đọc đĩa nên cũng không thấy.
        """
        device = self._device_by_iface(interface)
        if device is None:
            callback(None)
            return
        device.get_applied_connection_async(
            0, None, self._on_runtime_ipv4, (interface, callback)
        )

    def _on_runtime_ipv4(self, device, result, user_data) -> None:
        interface, callback = user_data
        try:
            connection, _version = device.get_applied_connection_finish(result)
        except Exception as exc:  # noqa: BLE001
            log.debug("không đọc được cấu hình đang chạy của %s: %s", interface, exc)
            callback(None)
            return

        setting = connection.get_setting_ip4_config()
        if setting is None:
            callback(None)
            return
        callback(
            RuntimeIpv4(
                interface=interface,
                automatic_routes=not setting.get_ignore_auto_routes(),
                routes=[
                    _route_from_nm(setting.get_route(i), RouteSource.STATIC)
                    for i in range(setting.get_num_routes())
                ],
            )
        )

    def restore_runtime(self, interface: str, callback: OpCallback | None = None) -> None:
        """Trả thiết bị về đúng cấu hình đã lưu của máy.

        Reapply chính `NM.RemoteConnection` trên đĩa, nên mọi thay đổi runtime
        do app tạo ra đều biến mất.
        """
        device = self._device_by_iface(interface)
        if device is None:
            self._report(callback, OpResult.success())   # không có gì để trả
            return

        active = device.get_active_connection()
        stored = active.get_connection() if active else None
        if stored is None:
            self._report(callback, OpResult.success())
            return

        device.reapply_async(
            stored, 0, 0, None, self._on_reapply_done,
            (callback, f"Khôi phục {interface}"),
        )

    def _reapply_with(self, interface, mutate, callback, context) -> None:
        device = self._device_by_iface(interface)
        if device is None:
            self._report(callback, OpResult(False, f"Không tìm thấy thiết bị {interface}"))
            return
        device.get_applied_connection_async(
            0, None, self._on_applied_fetched, (device, mutate, callback, context)
        )

    def _on_applied_fetched(self, device, result, user_data) -> None:
        _device, mutate, callback, context = user_data
        try:
            connection, version_id = device.get_applied_connection_finish(result)
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, context))
            return

        setting = connection.get_setting_ip4_config()
        if setting is None:
            self._report(callback, OpResult(False, f"{context}: thiết bị không có IPv4"))
            return

        try:
            mutate(setting)
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, context))
            return

        # version_id đảm bảo không ghi đè lên thay đổi vừa xảy ra ở nơi khác.
        device.reapply_async(
            connection, version_id, 0, None, self._on_reapply_done, (callback, context)
        )

    def _on_reapply_done(self, device, result, user_data) -> None:
        callback, context = user_data
        try:
            device.reapply_finish(result)
        except Exception as exc:  # noqa: BLE001
            self._report(callback, OpResult.failure(exc, context))
        else:
            self._report(callback, OpResult.success())

    def _device_by_iface(self, interface: str):
        return next(
            (d for d in self._client.get_devices() if d.get_iface() == interface), None
        )

    @staticmethod
    def _report(callback: OpCallback | None, result: OpResult) -> None:
        # Log ở đây thay vì trong OpResult.failure: đây là điểm hội tụ duy nhất
        # của mọi thao tác ghi, nên không thể có lỗi nào lọt mà không được ghi.
        if not result.ok:
            log.warning("%s", result.message)
        if callback is None:
            return
        try:
            callback(result)
        except Exception:  # noqa: BLE001
            log.exception("Callback của thao tác ghi ném exception")

    # ── tiện ích ────────────────────────────────────────────────────────────

    def wait_for_permissions(self, timeout_ms: int = 2000) -> bool:
        """Chạy main loop cho tới khi NM trả lời xong về permission.

        Chỉ dùng cho CLI/test — app dạng tray không cần vì nó đã có main loop
        và nhận `permission-changed` qua `subscribe()`.
        """
        if self._permissions().loaded:
            return True

        loop = GLib.MainLoop()
        timed_out = False

        def on_timeout() -> bool:
            nonlocal timed_out
            timed_out = True
            loop.quit()
            return GLib.SOURCE_REMOVE

        def on_permission_changed(*_args) -> None:
            if self._permissions().loaded:
                loop.quit()

        handler = self._client.connect("permission-changed", on_permission_changed)
        timeout_id = GLib.timeout_add(timeout_ms, on_timeout)
        loop.run()

        self._client.disconnect(handler)
        if not timed_out:
            GLib.source_remove(timeout_id)
        return self._permissions().loaded

    # ── theo dõi thay đổi ───────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Đăng ký nhận thông báo khi trạng thái mạng đổi.

        Callback được gọi trên GLib main loop, đã debounce. Không truyền dữ liệu
        kèm theo — người nghe tự gọi `snapshot()` để lấy trạng thái mới nhất.
        """
        self._subscribers.append(callback)
        if len(self._subscribers) == 1:
            self._wire_signals()

    def _wire_signals(self) -> None:
        c = self._client
        watched = [
            (c, "device-added"), (c, "device-removed"),
            (c, "connection-added"), (c, "connection-removed"),
            (c, "active-connection-added"), (c, "active-connection-removed"),
            (c, "notify::wireless-enabled"),
            (c, "notify::networking-enabled"),
            (c, "notify::primary-connection"),
            (c, "notify::state"),
            # NM nạp permission bất đồng bộ sau khi client được tạo; không nghe
            # signal này thì UI sẽ kẹt ở trạng thái "chưa biết quyền" mãi.
            (c, "permission-changed"),
        ]
        for obj, signal in watched:
            handler = obj.connect(signal, lambda *_a: self._schedule_notify())
            self._signal_ids.append((obj, handler))

        c.connect("device-added", lambda *_a: self._rewire_devices())
        c.connect("device-removed", lambda *_a: self._rewire_devices())
        self._rewire_devices()

    #: Tín hiệu của từng device cần nghe riêng — client không chuyển tiếp chúng.
    _DEVICE_SIGNALS = ("state-changed", "notify::ip4-config", "notify::active-connection")

    def _rewire_devices(self) -> None:
        """Nối lại tín hiệu cho TẤT CẢ device hiện có.

        Phải gọi lại mỗi khi danh sách device đổi. Nếu chỉ nối một lần lúc khởi
        động thì thiết bị cắm sau (USB ethernet, điện thoại chia sẻ mạng, dock)
        sẽ không bao giờ được theo dõi: `device-added` chỉ bắn đúng một lần, còn
        quá trình thiết bị đó lấy IP thì app không hay biết — nhìn như app không
        nhận ra thiết bị.
        """
        for dev, handler in self._device_signal_ids:
            try:
                dev.disconnect(handler)
            except Exception:  # noqa: BLE001 — device đã biến mất
                pass
        self._device_signal_ids.clear()

        for dev in self._managed_devices():
            for signal in self._DEVICE_SIGNALS:
                handler = dev.connect(signal, lambda *_a: self._schedule_notify())
                self._device_signal_ids.append((dev, handler))

    def _schedule_notify(self) -> None:
        """Gom signal dồn dập thành một lần thông báo (§4.8).

        Lúc chuyển mạng NM bắn hàng chục signal trong vài trăm ms; nếu rebuild
        menu theo từng cái thì tray sẽ giật.
        """
        if self._debounce_source is not None:
            GLib.source_remove(self._debounce_source)
        self._debounce_source = GLib.timeout_add(CHANGE_DEBOUNCE_MS, self._emit_notify)

    def _emit_notify(self) -> bool:
        self._debounce_source = None
        for callback in list(self._subscribers):
            try:
                callback()
            except Exception:  # noqa: BLE001 — một subscriber hỏng không được kéo sập app
                log.exception("Subscriber của NMFacade ném exception")
        return GLib.SOURCE_REMOVE

    def close(self) -> None:
        """Ngắt mọi signal handler — tránh rò rỉ khi app chạy 24/7 (R8)."""
        if self._debounce_source is not None:
            GLib.source_remove(self._debounce_source)
            self._debounce_source = None
        for obj, handler in [*self._signal_ids, *self._device_signal_ids]:
            try:
                obj.disconnect(handler)
            except Exception:  # noqa: BLE001
                pass
        self._signal_ids.clear()
        self._device_signal_ids.clear()
        self._subscribers.clear()
