"""Biến trạng thái mạng thành dữ liệu để hiển thị — thuần Python.

Toàn bộ quyết định "hiện chữ gì, bật/tắt nút nào" nằm ở đây chứ không nằm trong
code widget. Nhờ vậy phần khó kiểm chứng nhất của UI lại test được, và code GTK
chỉ còn việc dựng widget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import (
    ConnectionProfile,
    ConnType,
    DeviceInfo,
    DeviceState,
    Ipv4Method,
    NetworkSnapshot,
    RouteSource,
)

_STATE_TEXT = {
    DeviceState.CONNECTED: "Đã kết nối",
    DeviceState.CONNECTING: "Đang kết nối…",
    DeviceState.DEACTIVATING: "Đang ngắt…",
    DeviceState.DISCONNECTED: "Chưa kết nối",
    DeviceState.UNAVAILABLE: "Không khả dụng",
    DeviceState.UNMANAGED: "Không do NetworkManager quản lý",
    DeviceState.FAILED: "Kết nối thất bại",
    DeviceState.UNKNOWN: "Không rõ",
}

_METHOD_TEXT = {
    Ipv4Method.AUTO: "Tự động (DHCP)",
    Ipv4Method.MANUAL: "Thủ công",
    Ipv4Method.LINK_LOCAL: "Link-Local",
    Ipv4Method.DISABLED: "Tắt",
    Ipv4Method.SHARED: "Chia sẻ",
}


def state_text(state: DeviceState) -> str:
    return _STATE_TEXT.get(state, "Không rõ")


def method_text(method: Ipv4Method) -> str:
    return _METHOD_TEXT.get(method, method.value)


# ─────────────────────────────────────────────────────────────────────────────
# Hàng danh sách kết nối
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ConnectionRow:
    uuid: str
    title: str
    subtitle: str
    icon_name: str
    is_active: bool
    busy: bool
    #: Nút hành động chính hiện chữ gì; None nghĩa là không có hành động.
    action_label: str | None
    can_act: bool
    can_delete: bool


def _device_for(conn: ConnectionProfile, snapshot: NetworkSnapshot) -> DeviceInfo | None:
    iface = conn.device_interface or conn.interface_name
    return snapshot.device_by_interface(iface) if iface else None


def _connection_subtitle(conn: ConnectionProfile, device: DeviceInfo | None) -> str:
    parts: list[str] = []
    if conn.is_active and device is not None:
        parts.append(state_text(device.state))
        if device.ip4_addresses:
            parts.append(str(device.ip4_addresses[0]))
    else:
        parts.append(method_text(conn.ipv4.method))

    if conn.ipv4.routes:
        parts.append(f"{len(conn.ipv4.routes)} route tĩnh")
    if conn.interface_name:
        parts.append(conn.interface_name)
    return " · ".join(parts)


def connection_rows(snapshot: NetworkSnapshot, conn_type: ConnType) -> list[ConnectionRow]:
    """Danh sách connection đã lưu của một loại, đã sắp xếp."""
    conns = [c for c in snapshot.connections if c.type is conn_type]
    conns.sort(key=lambda c: (not c.is_active, c.display_name.lower()))

    can_control = snapshot.permissions.can_control_network
    can_edit = snapshot.permissions.can_edit_connections

    rows: list[ConnectionRow] = []
    for conn in conns:
        device = _device_for(conn, snapshot)
        busy = device is not None and device.state.is_busy
        rows.append(
            ConnectionRow(
                uuid=conn.uuid,
                title=conn.display_name,
                subtitle=_connection_subtitle(conn, device),
                icon_name=(
                    "network-wireless-symbolic"
                    if conn.type is ConnType.WIFI
                    else "network-wired-symbolic"
                ),
                is_active=conn.is_active,
                busy=busy,
                action_label="Ngắt" if conn.is_active else "Kết nối",
                # Đang chuyển trạng thái thì khoá lại, tránh gửi hai lệnh chồng nhau.
                can_act=can_control and not busy,
                # Không cho xoá cấu hình đang chạy: người dùng sẽ mất mạng ngay
                # mà không hiểu vì sao.
                can_delete=can_edit and not conn.is_active and not busy,
            )
        )
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Chi tiết
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class DetailRow:
    label: str
    value: str
    #: True khi giá trị đến từ runtime chứ không phải cấu hình đã lưu.
    runtime: bool = False


@dataclass(slots=True)
class RouteRow:
    text: str
    source_label: str
    editable: bool


@dataclass(slots=True)
class ConnectionDetail:
    uuid: str
    title: str
    subtitle: str
    status: list[DetailRow] = field(default_factory=list)
    ipv4: list[DetailRow] = field(default_factory=list)
    routes: list[RouteRow] = field(default_factory=list)
    is_active: bool = False
    can_act: bool = False
    can_delete: bool = False
    action_label: str = "Kết nối"


def _status_rows(device: DeviceInfo | None) -> list[DetailRow]:
    if device is None:
        return [DetailRow("Trạng thái", "Thiết bị không có mặt")]

    rows = [DetailRow("Trạng thái", state_text(device.state))]
    if device.mac:
        rows.append(DetailRow("Địa chỉ MAC", device.mac))

    if device.type is ConnType.WIFI and device.wifi:
        w = device.wifi
        rows.append(DetailRow("SSID", w.ssid or "—", runtime=True))
        rows.append(DetailRow("Cường độ", f"{w.strength}% ({w.signal_bars}/4)", runtime=True))
        if w.band:
            rows.append(DetailRow("Băng tần", w.band, runtime=True))
        if w.security:
            rows.append(DetailRow("Bảo mật", w.security, runtime=True))
        if w.bitrate_kbps:
            rows.append(DetailRow("Tốc độ", f"{w.bitrate_kbps // 1000} Mb/s", runtime=True))
        if w.bssid:
            rows.append(DetailRow("BSSID", w.bssid, runtime=True))
    elif device.type is ConnType.ETHERNET:
        if not device.carrier:
            rows.append(DetailRow("Cáp mạng", "Chưa cắm"))
        if device.speed_mbps:
            rows.append(DetailRow("Tốc độ liên kết", f"{device.speed_mbps} Mb/s", runtime=True))

    if device.ip4_addresses:
        rows.append(
            DetailRow(
                "Địa chỉ IPv4",
                ", ".join(str(a) for a in device.ip4_addresses),
                runtime=True,
            )
        )
    if device.ip4_gateway:
        rows.append(DetailRow("Gateway", device.ip4_gateway, runtime=True))
    if device.ip4_dns:
        rows.append(DetailRow("DNS", ", ".join(device.ip4_dns), runtime=True))
    return rows


def _ipv4_rows(conn: ConnectionProfile) -> list[DetailRow]:
    ip4 = conn.ipv4
    rows = [DetailRow("Chế độ", method_text(ip4.method))]
    if ip4.addresses:
        rows.append(DetailRow("Địa chỉ", ", ".join(str(a) for a in ip4.addresses)))
    if ip4.gateway:
        rows.append(DetailRow("Gateway", ip4.gateway))
    if ip4.dns:
        rows.append(DetailRow("DNS", ", ".join(ip4.dns)))

    flags = [
        label
        for label, on in (
            ("Bỏ qua DNS tự động", ip4.ignore_auto_dns),
            ("Bỏ qua route tự động", ip4.ignore_auto_routes),
            ("Không dùng làm default route", ip4.never_default),
        )
        if on
    ]
    if flags:
        rows.append(DetailRow("Tuỳ chọn", ", ".join(flags)))
    return rows


def _route_rows(conn: ConnectionProfile, device: DeviceInfo | None) -> list[RouteRow]:
    """Gộp route tĩnh (sửa được) và route runtime (chỉ đọc) — FR-R1.

    Route runtime trùng với một route tĩnh thì bỏ, tránh hiện hai lần cùng một
    thứ với hai nhãn nguồn khác nhau.
    """
    rows = [
        RouteRow(text=str(r), source_label="Tĩnh", editable=True)
        for r in conn.ipv4.routes
    ]
    static_ids = {r.identity() for r in conn.ipv4.routes}

    if device is not None and conn.is_active:
        for route in device.ip4_routes:
            if route.identity() in static_ids:
                continue
            label = "DHCP" if route.source is RouteSource.DHCP else "Hệ thống"
            rows.append(RouteRow(text=str(route), source_label=label, editable=False))
    return rows


def connection_detail(snapshot: NetworkSnapshot, uuid: str) -> ConnectionDetail | None:
    conn = snapshot.connection_by_uuid(uuid)
    if conn is None:
        return None

    device = _device_for(conn, snapshot)
    busy = device is not None and device.state.is_busy

    return ConnectionDetail(
        uuid=conn.uuid,
        title=conn.display_name,
        subtitle=(
            "Wi-Fi" if conn.type is ConnType.WIFI else "Có dây"
        ) + (f" · {conn.interface_name}" if conn.interface_name else ""),
        status=_status_rows(device),
        ipv4=_ipv4_rows(conn),
        routes=_route_rows(conn, device),
        is_active=conn.is_active,
        can_act=snapshot.permissions.can_control_network and not busy,
        can_delete=(
            snapshot.permissions.can_edit_connections and not conn.is_active and not busy
        ),
        action_label="Ngắt kết nối" if conn.is_active else "Kết nối",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Proxy
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ProxyRow:
    config_id: str
    title: str
    subtitle: str
    is_active: bool


def proxy_rows(configs, active_id: str | None) -> list[ProxyRow]:
    return [
        ProxyRow(
            config_id=c.id,
            title=c.name,
            subtitle=c.summary()
            + (" · cần xác thực" if c.auth_enabled else "")
            + f" · {len(c.ignore_hosts)} mục bỏ qua",
            is_active=c.id == active_id,
        )
        for c in configs
    ]


def layer_rows(statuses) -> list[DetailRow]:
    """Trạng thái từng lớp proxy cho màn hình chẩn đoán (R4)."""
    rows = []
    for st in statuses:
        value = st.summary if st.available else "không khả dụng"
        if st.note:
            value += f" · {st.note}"
        rows.append(DetailRow(st.name, value, runtime=st.active))
    return rows


def empty_wifi_hint(snapshot: NetworkSnapshot) -> str | None:
    """Chữ hiện khi chưa có Wi-Fi nào được lưu (§3.2.1).

    App không kết nối mạng mới, nên danh sách trống mà không giải thích sẽ khiến
    người dùng tưởng app hỏng.
    """
    if not snapshot.has_wifi_hardware or snapshot.saved_wifi_connections():
        return None
    return (
        "Chưa có mạng Wi-Fi nào được lưu. Kết nối lần đầu bằng menu Wi-Fi của hệ "
        "thống, mạng sẽ tự xuất hiện ở đây."
    )
