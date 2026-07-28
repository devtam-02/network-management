"""Test view-model — nơi chứa mọi quyết định hiển thị của cửa sổ chính."""

from __future__ import annotations

import pytest

from netmgr.domain.models import (
    ConnType,
    DeviceState,
    Ipv4Address,
    Ipv4Method,
    Ipv4Route,
    Permissions,
    PermissionState,
    ProxyEndpoint,
    ProxyMode,
    RouteSource,
)
from netmgr.infra.proxy_store import new_config
from netmgr.ui.view_models import (
    ignore_auto_hint,
    connection_detail,
    connection_rows,
    empty_wifi_hint,
    layer_rows,
    method_text,
    proxy_rows,
    state_text,
)

from .factories import eth_conn, eth_device, snapshot, wifi_conn, wifi_device


def labels(rows) -> list[str]:
    return [r.label for r in rows]


def value_of(rows, label: str) -> str | None:
    return next((r.value for r in rows if r.label == label), None)


# ── nhãn ─────────────────────────────────────────────────────────────────────


def test_state_text_covers_every_state():
    for state in DeviceState:
        assert state_text(state) and state_text(state) != state.value


def test_method_text_covers_every_method():
    for method in Ipv4Method:
        assert method_text(method)


def test_automatic_method_is_spelled_out():
    assert "DHCP" in method_text(Ipv4Method.AUTO)


# ── danh sách kết nối ────────────────────────────────────────────────────────


def test_rows_filtered_by_type():
    snap = snapshot(connections=[wifi_conn("W"), eth_conn("E")])
    assert [r.title for r in connection_rows(snap, ConnType.WIFI)] == ["W"]
    assert [r.title for r in connection_rows(snap, ConnType.ETHERNET)] == ["E"]


def test_active_connection_sorted_first():
    snap = snapshot(
        devices=[wifi_device()],
        connections=[wifi_conn("Zulu"), wifi_conn("Alpha", active=True)],
    )
    assert [r.title for r in connection_rows(snap, ConnType.WIFI)] == ["Alpha", "Zulu"]


def test_inactive_connections_sorted_by_name():
    snap = snapshot(connections=[wifi_conn("Zulu"), wifi_conn("alpha")])
    assert [r.title for r in connection_rows(snap, ConnType.WIFI)] == ["alpha", "Zulu"]


def test_active_row_shows_state_and_ip():
    snap = snapshot(
        devices=[wifi_device(ip="192.168.1.42")],
        connections=[wifi_conn("W", active=True)],
    )
    row = connection_rows(snap, ConnType.WIFI)[0]
    assert "Đã kết nối" in row.subtitle
    assert "192.168.1.42/24" in row.subtitle


def test_inactive_row_shows_ipv4_method():
    snap = snapshot(connections=[wifi_conn("W", method=Ipv4Method.MANUAL)])
    assert "Thủ công" in connection_rows(snap, ConnType.WIFI)[0].subtitle


def test_row_mentions_static_route_count():
    conn = eth_conn("E")
    conn.ipv4.routes = [Ipv4Route("10.0.0.0", 8), Ipv4Route("172.16.0.0", 12)]
    snap = snapshot(connections=[conn])
    assert "2 route tĩnh" in connection_rows(snap, ConnType.ETHERNET)[0].subtitle


def test_action_label_flips_with_state():
    snap = snapshot(
        devices=[wifi_device()],
        connections=[wifi_conn("A", active=True), wifi_conn("B")],
    )
    rows = {r.title: r.action_label for r in connection_rows(snap, ConnType.WIFI)}
    assert rows == {"A": "Ngắt", "B": "Kết nối"}


def test_busy_device_disables_action():
    snap = snapshot(
        devices=[wifi_device(state=DeviceState.CONNECTING)],
        connections=[wifi_conn("A")],
    )
    row = connection_rows(snap, ConnType.WIFI)[0]
    assert row.busy is True and row.can_act is False


def test_active_connection_cannot_be_deleted():
    """Xoá cấu hình đang chạy sẽ làm mất mạng ngay mà người dùng không hiểu vì sao."""
    snap = snapshot(devices=[wifi_device()], connections=[wifi_conn("A", active=True)])
    assert connection_rows(snap, ConnType.WIFI)[0].can_delete is False


def test_inactive_connection_can_be_deleted():
    snap = snapshot(connections=[wifi_conn("A")])
    assert connection_rows(snap, ConnType.WIFI)[0].can_delete is True


def test_denied_permission_blocks_actions():
    perms = Permissions(
        modify_system=PermissionState.NO, network_control=PermissionState.NO
    )
    snap = snapshot(
        devices=[wifi_device()], connections=[wifi_conn("A")], permissions=perms
    )
    row = connection_rows(snap, ConnType.WIFI)[0]
    assert row.can_act is False and row.can_delete is False


def test_unknown_permission_does_not_block():
    """Thiết bị có mặt, chỉ permission là chưa biết → vẫn phải cho thao tác."""
    snap = snapshot(
        devices=[wifi_device()], connections=[wifi_conn("A")], permissions=Permissions()
    )
    row = connection_rows(snap, ConnType.WIFI)[0]
    assert row.can_act is True and row.can_delete is True
    assert row.blocked_reason == ""


def test_missing_device_explains_why():
    """Nút bị tắt phải nói được lý do khi người dùng bấm vào."""
    snap = snapshot(connections=[wifi_conn("A", interface="wlp-đã-rút")])
    row = connection_rows(snap, ConnType.WIFI)[0]
    assert row.can_act is False
    assert "không có mặt" in row.blocked_reason


def test_denied_permission_explains_why():
    perms = Permissions(network_control=PermissionState.NO)
    snap = snapshot(
        devices=[wifi_device()], connections=[wifi_conn("A")], permissions=perms
    )
    assert "polkit" in connection_rows(snap, ConnType.WIFI)[0].blocked_reason


def test_wifi_off_explains_why():
    snap = snapshot(
        devices=[wifi_device()], connections=[wifi_conn("A")], wifi_enabled=False
    )
    assert "Wi-Fi đang tắt" in connection_rows(snap, ConnType.WIFI)[0].blocked_reason


def test_busy_device_explains_why():
    snap = snapshot(
        devices=[wifi_device(state=DeviceState.CONNECTING)],
        connections=[wifi_conn("A")],
    )
    assert "chờ" in connection_rows(snap, ConnType.WIFI)[0].blocked_reason


def test_icons_differ_by_type():
    snap = snapshot(connections=[wifi_conn("W"), eth_conn("E")])
    assert "wireless" in connection_rows(snap, ConnType.WIFI)[0].icon_name
    assert "wired" in connection_rows(snap, ConnType.ETHERNET)[0].icon_name


# ── chi tiết ─────────────────────────────────────────────────────────────────


def test_detail_of_unknown_uuid_is_none():
    assert connection_detail(snapshot(), "không-có") is None


def test_detail_title_and_subtitle():
    conn = wifi_conn("Nhà")
    snap = snapshot(connections=[conn])
    detail = connection_detail(snap, conn.uuid)
    assert detail.title == "Nhà"
    assert "Wi-Fi" in detail.subtitle and "wlp2s0" in detail.subtitle


def test_detail_status_includes_wifi_fields():
    conn = wifi_conn("Nhà", active=True)
    snap = snapshot(devices=[wifi_device(ssid="Nhà", strength=85)], connections=[conn])
    rows = connection_detail(snap, conn.uuid).status
    assert value_of(rows, "SSID") == "Nhà"
    assert "85%" in value_of(rows, "Cường độ")
    assert value_of(rows, "Băng tần") == "5 GHz"


def test_detail_status_includes_ethernet_fields():
    conn = eth_conn("LAN", active=True)
    snap = snapshot(devices=[eth_device()], connections=[conn])
    rows = connection_detail(snap, conn.uuid).status
    assert value_of(rows, "Tốc độ liên kết") == "1000 Mb/s"


def test_unplugged_cable_reported_in_detail():
    conn = eth_conn("LAN")
    snap = snapshot(
        devices=[eth_device(state=DeviceState.UNAVAILABLE, carrier=False, ip=None)],
        connections=[conn],
    )
    assert value_of(connection_detail(snap, conn.uuid).status, "Cáp mạng") == "Chưa cắm"


def test_detail_without_device_says_so():
    conn = wifi_conn("Nhà", interface="wlp9s0")
    snap = snapshot(connections=[conn])
    rows = connection_detail(snap, conn.uuid).status
    assert value_of(rows, "Trạng thái") == "Thiết bị không có mặt"


def test_runtime_values_are_flagged():
    """UI cần phân biệt giá trị đang chạy với giá trị đã lưu."""
    conn = wifi_conn("Nhà", active=True)
    snap = snapshot(devices=[wifi_device(ssid="Nhà")], connections=[conn])
    rows = connection_detail(snap, conn.uuid).status
    assert next(r for r in rows if r.label == "Địa chỉ IPv4").runtime is True
    assert next(r for r in rows if r.label == "Trạng thái").runtime is False


def test_ipv4_section_shows_method():
    conn = eth_conn("LAN", method=Ipv4Method.MANUAL)
    conn.ipv4.addresses = [Ipv4Address("10.0.5.20", 24)]
    conn.ipv4.gateway = "10.0.5.1"
    conn.ipv4.dns = ["1.1.1.1"]
    snap = snapshot(connections=[conn])
    rows = connection_detail(snap, conn.uuid).ipv4
    assert value_of(rows, "Chế độ") == "Thủ công"
    assert value_of(rows, "Địa chỉ") == "10.0.5.20/24"
    assert value_of(rows, "DNS") == "1.1.1.1"


def test_ipv4_flags_listed():
    conn = eth_conn("LAN")
    conn.ipv4.ignore_auto_routes = True
    conn.ipv4.never_default = True
    snap = snapshot(connections=[conn])
    value = value_of(connection_detail(snap, conn.uuid).ipv4, "Tuỳ chọn")
    assert "Bỏ qua route tự động" in value and "default route" in value


def test_no_flags_row_when_all_default():
    conn = eth_conn("LAN")
    snap = snapshot(connections=[conn])
    assert value_of(connection_detail(snap, conn.uuid).ipv4, "Tuỳ chọn") is None


# ── route ────────────────────────────────────────────────────────────────────


def test_static_routes_are_editable():
    conn = eth_conn("LAN")
    conn.ipv4.routes = [Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1")]
    snap = snapshot(connections=[conn])
    rows = connection_detail(snap, conn.uuid).routes
    assert len(rows) == 1
    assert rows[0].editable is True and rows[0].source_label == "Tĩnh"


def test_runtime_routes_are_read_only():
    conn = eth_conn("LAN", active=True)
    device = eth_device()
    device.ip4_routes = [Ipv4Route("0.0.0.0", 0, next_hop="10.0.5.1", source=RouteSource.DHCP)]
    snap = snapshot(devices=[device], connections=[conn])
    rows = connection_detail(snap, conn.uuid).routes
    assert rows[0].editable is False and rows[0].source_label == "DHCP"


def test_route_present_in_both_shown_once_as_static():
    """Trùng lặp sẽ hiện hai lần cùng một route với hai nhãn nguồn khác nhau."""
    route = Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1")
    conn = eth_conn("LAN", active=True)
    conn.ipv4.routes = [route]
    device = eth_device()
    device.ip4_routes = [
        Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1", source=RouteSource.DHCP)
    ]
    snap = snapshot(devices=[device], connections=[conn])
    rows = connection_detail(snap, conn.uuid).routes
    assert len(rows) == 1 and rows[0].source_label == "Tĩnh"


def test_runtime_routes_hidden_when_connection_inactive():
    conn = eth_conn("LAN")
    device = eth_device()
    device.ip4_routes = [Ipv4Route("0.0.0.0", 0, source=RouteSource.DHCP)]
    snap = snapshot(devices=[device], connections=[conn])
    assert connection_detail(snap, conn.uuid).routes == []


# ── proxy ────────────────────────────────────────────────────────────────────


def test_proxy_rows_mark_active():
    a = new_config("A", mode=ProxyMode.MANUAL)
    a.http = ProxyEndpoint("10.0.0.8", 3128)
    b = new_config("B")
    rows = proxy_rows([a, b], active_id=a.id)
    assert rows[0].is_active is True and rows[1].is_active is False


def test_proxy_row_subtitle_has_endpoint_and_counts():
    cfg = new_config("A", mode=ProxyMode.MANUAL)
    cfg.http = ProxyEndpoint("10.0.0.8", 3128)
    cfg.ignore_hosts = ["localhost", "*.viettel.vn"]
    row = proxy_rows([cfg], None)[0]
    assert "10.0.0.8:3128" in row.subtitle
    assert "2 mục bỏ qua" in row.subtitle


def test_proxy_row_flags_authentication():
    cfg = new_config("A", mode=ProxyMode.MANUAL)
    cfg.http = ProxyEndpoint("10.0.0.8", 3128)
    cfg.auth_enabled = True
    assert "cần xác thực" in proxy_rows([cfg], None)[0].subtitle


def test_layer_rows_include_note():
    from netmgr.domain.models import ProxyLayerId
    from netmgr.infra.proxy.base import LayerStatus

    rows = layer_rows(
        [LayerStatus(ProxyLayerId.ENVIRONMENT, "Môi trường", True, True, "10.0.0.8:3128",
                     note="cần terminal mới")]
    )
    assert rows[0].label == "Môi trường"
    assert "cần terminal mới" in rows[0].value
    assert rows[0].runtime is True


def test_layer_rows_report_unavailable():
    from netmgr.domain.models import ProxyLayerId
    from netmgr.infra.proxy.base import LayerStatus

    rows = layer_rows([LayerStatus(ProxyLayerId.APT, "APT", False, False, "")])
    assert rows[0].value == "không khả dụng"


# ── trạng thái rỗng ──────────────────────────────────────────────────────────


def test_empty_wifi_hint_when_nothing_saved():
    hint = empty_wifi_hint(snapshot(devices=[wifi_device()]))
    assert hint and "menu Wi-Fi của hệ thống" in hint


def test_no_hint_when_wifi_saved():
    snap = snapshot(devices=[wifi_device()], connections=[wifi_conn("A")])
    assert empty_wifi_hint(snap) is None


def test_no_hint_without_wifi_hardware():
    assert empty_wifi_hint(snapshot(devices=[eth_device()])) is None


# ── tên và trạng thái thiết bị (dùng cho Bộ cấu hình) ────────────────────────


def test_device_label_distinguishes_pci_and_usb():
    """enx52578a6f8d22 không nói lên điều gì; "USB Ethernet" thì có."""
    from netmgr.ui.view_models import device_label

    pci = eth_device("enp1s0")
    pci.hw_path = "pci-0000:01:00.0"
    usb = eth_device("enx52578a6f8d22")
    usb.hw_path = "pci-0000:00:14.0-usb-0:1:4.2"

    assert device_label(pci) == "PCI Ethernet"
    assert device_label(usb) == "USB Ethernet"


def test_device_label_for_wifi():
    from netmgr.ui.view_models import device_label

    dev = wifi_device()
    dev.hw_path = "pci-0000:00:14.0-usb-0:8:1.0"
    assert device_label(dev) == "USB Wi-Fi"


def test_device_label_without_path_defaults_to_pci():
    from netmgr.ui.view_models import device_label

    assert device_label(eth_device("eth0")) == "PCI Ethernet"


def test_device_status_shows_connection_and_ip():
    from netmgr.ui.view_models import device_status

    conn = eth_conn("LAN enp1s0", active=True, interface="enp1s0")
    dev = eth_device("enp1s0", ip="10.207.153.128")
    dev.active_connection_uuid = conn.uuid
    snap = snapshot(devices=[dev], connections=[conn])

    text = device_status(dev, snap)
    assert "Đã kết nối" in text
    assert "LAN enp1s0" in text
    assert "10.207.153.128/24" in text


def test_device_status_when_unavailable():
    from netmgr.ui.view_models import device_status

    dev = wifi_device(state=DeviceState.UNAVAILABLE, ip=None)
    assert device_status(dev, snapshot(devices=[dev])) == "Không khả dụng"


def test_device_status_when_idle():
    from netmgr.ui.view_models import device_status

    dev = eth_device("enp1s0", state=DeviceState.DISCONNECTED, ip=None)
    assert device_status(dev, snapshot(devices=[dev])) == "Chưa kết nối"


# ── cảnh báo khi bỏ route tự động ────────────────────────────────────────────


def test_ignore_auto_hint_canh_bao_khi_la_duong_ra_internet():
    from netmgr.domain.models import Ipv4Route, SystemRoute

    snap = snapshot(system_routes=[
        SystemRoute("enx1234",
                    Ipv4Route("0.0.0.0", 0, next_hop="172.20.10.1", metric=100)),
    ])
    hint = ignore_auto_hint("enx1234", snap)
    assert "đường ra Internet" in hint


def test_ignore_auto_hint_binh_thuong_khi_khong_giu_default_route():
    from netmgr.domain.models import Ipv4Route, SystemRoute

    snap = snapshot(system_routes=[
        SystemRoute("enx1234",
                    Ipv4Route("0.0.0.0", 0, next_hop="172.20.10.1", metric=100)),
    ])
    hint = ignore_auto_hint("enp1s0", snap)
    assert "đường ra Internet" not in hint
    assert "DHCP" in hint
