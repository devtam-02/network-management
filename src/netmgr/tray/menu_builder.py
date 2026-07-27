"""Dựng cây menu tray từ `NetworkSnapshot` (§5.1).

Thuần Python: nhận snapshot + một bộ callback, trả về `list[MenuItem]`. Không
đụng D-Bus nên test được toàn bộ cấu trúc menu.

Vì GNOME không hỗ trợ tooltip (xem docstring `tray/status.py`), các dòng trạng
thái phải nằm ngay trong menu dưới dạng item disabled — đó là chỗ DUY NHẤT người
dùng đọc được IP, cường độ sóng và trạng thái proxy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.models import ConnectionProfile, ConnType, DeviceInfo
from .menu_model import MenuItem, checkbox, info, item, radio, separator, submenu
from .status import TrayStatus

#: Nhãn dùng cho các mục chưa làm ở giai đoạn hiện tại. Hiện ra nhưng disabled,
#: để người dùng biết app định làm gì chứ không phải bấm vào rồi không có gì xảy ra.
TODO_SUFFIX = " (chưa có ở bản này)"


@dataclass(slots=True)
class MenuActions:
    """Các callback menu gọi tới. Gom vào một chỗ để test dễ thay bằng fake."""

    set_wifi_enabled: Callable[[bool], None]
    activate_connection: Callable[[str], None]
    deactivate_connection: Callable[[str], None]
    open_wifi_settings: Callable[[], None]
    quit: Callable[[], None]
    open_main_window: Callable[[], None] | None = None
    # ── proxy (P2) ──
    activate_proxy: Callable[[str], None] | None = None
    disable_proxy: Callable[[], None] | None = None
    import_current_proxy: Callable[[], None] | None = None
    copy_proxy_snippet: Callable[[], None] | None = None
    # ── Bộ cấu hình (P5) ──
    apply_profile: Callable[[str], None] | None = None
    clear_profile: Callable[[], None] | None = None
    capture_profile: Callable[[], None] | None = None
    manage_profiles: Callable[[], None] | None = None


def _device_for(conn: ConnectionProfile, devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Device mà connection này đang chạy trên đó, hoặc sẽ chạy nếu kích hoạt."""
    iface = conn.device_interface or conn.interface_name
    if iface is None:
        return None
    return next((d for d in devices if d.interface == iface), None)


def _connection_items(
    connections: list[ConnectionProfile],
    devices: list[DeviceInfo],
    actions: MenuActions,
    *,
    enabled: bool = True,
) -> list[MenuItem]:
    """Radio group cho một nhóm connection. Bấm lại cái đang chọn = ngắt kết nối."""
    items: list[MenuItem] = []
    for conn in sorted(connections, key=lambda c: c.display_name.lower()):
        device = _device_for(conn, devices)
        busy = device is not None and device.state.is_busy

        label = conn.display_name
        if busy:
            label += " — đang xử lý…"

        items.append(
            radio(
                label,
                selected=conn.is_active,
                # Device đang chuyển trạng thái thì khoá lại, nếu không hai lần
                # bấm liên tiếp sẽ gửi hai lệnh chồng nhau.
                enabled=enabled and not busy,
                action=_toggle_action(conn, actions),
            )
        )
    return items


def _toggle_action(conn: ConnectionProfile, actions: MenuActions) -> Callable[[], None]:
    uuid = conn.uuid
    if conn.is_active:
        return lambda: actions.deactivate_connection(uuid)
    return lambda: actions.activate_connection(uuid)


def _wifi_submenu(snapshot, actions: MenuActions) -> MenuItem:
    children: list[MenuItem] = [
        checkbox(
            "Bật Wi-Fi",
            checked=snapshot.wifi_enabled,
            enabled=snapshot.wifi_hardware_enabled and snapshot.permissions.can_toggle_wifi,
            action=lambda: actions.set_wifi_enabled(not snapshot.wifi_enabled),
        )
    ]
    if not snapshot.wifi_hardware_enabled:
        children.append(info("Wi-Fi bị tắt bằng công tắc phần cứng"))
        return submenu("Wi-Fi", children)

    saved = snapshot.saved_wifi_connections()
    children.append(separator())

    if not saved:
        # §3.2.1 — app không kết nối mạng mới, nên phải chỉ đường rõ ràng thay vì
        # để một danh sách trống khó hiểu.
        children.append(info("Chưa có mạng Wi-Fi nào được lưu"))
        children.append(
            item("Kết nối lần đầu bằng cài đặt hệ thống…", action=actions.open_wifi_settings)
        )
        return submenu("Wi-Fi", children)

    children.extend(
        _connection_items(
            saved, snapshot.devices, actions,
            enabled=snapshot.wifi_enabled and snapshot.permissions.can_control_network,
        )
    )
    children.append(separator())
    children.append(item("Quản lý mạng đã lưu…" + TODO_SUFFIX, enabled=False))
    return submenu("Wi-Fi", children)


def _wired_submenu(snapshot, actions: MenuActions) -> MenuItem | None:
    devices = snapshot.ethernet_devices
    if not devices:
        return None

    children: list[MenuItem] = []
    unplugged = [d for d in devices if not d.carrier]
    for dev in unplugged:
        children.append(info(f"{dev.interface}: chưa cắm cáp"))
    if unplugged:
        children.append(separator())

    connections = snapshot.ethernet_connections()
    if connections:
        children.extend(
            _connection_items(
                connections, snapshot.devices, actions,
                enabled=snapshot.permissions.can_control_network,
            )
        )
    else:
        children.append(info("Chưa có cấu hình có dây nào"))

    return submenu("Có dây", children)


def _proxy_submenu(proxy, actions: MenuActions) -> MenuItem:
    """Submenu proxy (F1). `proxy` là ProxyService, None nếu chưa khởi tạo được."""
    if proxy is None or actions.activate_proxy is None:
        return item("Proxy" + TODO_SUFFIX, enabled=False)

    configs = proxy.configs
    active = proxy.active
    children: list[MenuItem] = [
        radio("Tắt", selected=active is None, action=actions.disable_proxy)
    ]

    for config in configs:
        cid = config.id
        children.append(
            radio(
                f"{config.name} — {config.summary()}",
                selected=active is not None and active.id == cid,
                action=lambda cid=cid: actions.activate_proxy(cid),
            )
        )

    if not configs:
        children.append(info("Chưa có cấu hình proxy nào"))

    children.append(separator())

    # Từng lớp đang ở trạng thái gì — trả lời câu "tắt proxy rồi mà apt vẫn
    # qua proxy?" (R4). Đây là thứ GNOME không cho thấy ở đâu cả.
    for st in proxy.layer_statuses():
        mark = "●" if st.active else "○"
        note = f" · {st.note}" if st.note else ""
        children.append(info(f"{mark} {st.name}: {st.summary}{note}"))

    children.append(separator())
    if actions.import_current_proxy is not None:
        children.append(
            item("Nhập cấu hình proxy đang có của hệ thống…",
                 action=actions.import_current_proxy)
        )
    if actions.copy_proxy_snippet is not None:
        children.append(
            item("Chép lệnh export cho terminal đang mở",
                 action=actions.copy_proxy_snippet)
        )
    children.append(item("Quản lý cấu hình proxy…" + TODO_SUFFIX, enabled=False))

    label = "Proxy: " + (active.name if active and active.is_enabled else "Tắt")
    return submenu(label, children)


def _profile_submenu(profiles, actions: MenuActions) -> MenuItem:
    """Radio group chuyển bối cảnh một click (FR-PR2)."""
    if profiles is None or actions.apply_profile is None:
        return item("Bộ cấu hình" + TODO_SUFFIX, enabled=False)

    active = profiles.active
    busy = profiles.busy
    children: list[MenuItem] = []

    for profile in profiles.profiles:
        pid = profile.id
        label = profile.label
        if profile.is_broken:
            label += " ⚠"
        children.append(
            radio(
                label,
                selected=active is not None and active.id == pid,
                # Đang áp dụng thì khoá hết: hai lệnh chồng nhau sẽ để mạng ở
                # trạng thái lai không ai đoán được.
                enabled=not busy and not profile.is_broken,
                action=lambda pid=pid: actions.apply_profile(pid),
                description=profile.description,
            )
        )

    if not profiles.profiles:
        children.append(info("Chưa có Bộ cấu hình nào"))

    children.append(
        radio(
            "✖ Không dùng Bộ cấu hình",
            selected=active is None,
            enabled=not busy,
            action=actions.clear_profile,
        )
    )

    children.append(separator())
    if actions.capture_profile is not None:
        children.append(
            item("Lưu trạng thái hiện tại thành Bộ cấu hình…",
                 enabled=not busy, action=actions.capture_profile)
        )
    if actions.manage_profiles is not None:
        children.append(item("Quản lý Bộ cấu hình…", action=actions.manage_profiles))

    label = "Bộ cấu hình: " + (
        "đang áp dụng…" if busy else (active.label if active else "Không dùng")
    )
    return submenu(label, children)


def build_menu(
    snapshot,
    status: TrayStatus,
    actions: MenuActions,
    proxy=None,
    profiles=None,
) -> list[MenuItem]:
    """Menu hoàn chỉnh cho tray."""
    items: list[MenuItem] = []

    # ── trạng thái ──────────────────────────────────────────────────────────
    items.append(info(status.title))
    items.extend(info(f"   {line}") for line in status.status_lines)
    if not snapshot.networking_enabled:
        items.append(info("Toàn bộ mạng đang tắt"))

    # ── Bộ cấu hình ─────────────────────────────────────────────────────────
    # Đặt ngay dưới trạng thái vì đây là thao tác chính: chuyển cả bối cảnh
    # thay vì chỉnh từng thứ một.
    items.append(separator())
    items.append(_profile_submenu(profiles, actions))

    # ── kết nối ─────────────────────────────────────────────────────────────
    items.append(separator())
    if snapshot.has_wifi_hardware:
        items.append(_wifi_submenu(snapshot, actions))
    wired = _wired_submenu(snapshot, actions)
    if wired is not None:
        items.append(wired)

    # ── proxy ───────────────────────────────────────────────────────────────
    items.append(separator())
    items.append(_proxy_submenu(proxy, actions))

    items.append(separator())
    if actions.open_main_window is not None:
        items.append(item("Cài đặt…", action=actions.open_main_window))
    else:
        items.append(item("Cài đặt…" + TODO_SUFFIX, enabled=False))

    if not snapshot.permissions.can_edit_connections:
        items.append(info("Không đủ quyền để sửa cấu hình mạng"))

    items.append(item("Thoát", action=actions.quit))
    return items


def summarize_devices(devices: list[DeviceInfo]) -> list[str]:
    """Dòng mô tả ngắn cho từng device — dùng khi cần ngoài `TrayStatus`."""
    out = []
    for dev in devices:
        ip = str(dev.ip4_addresses[0]) if dev.ip4_addresses else "chưa có IP"
        name = dev.wifi.ssid if dev.type is ConnType.WIFI and dev.wifi else dev.interface
        out.append(f"{name}: {ip}")
    return out
