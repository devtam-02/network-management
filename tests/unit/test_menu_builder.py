"""Test cho việc dựng menu tray từ snapshot."""

from __future__ import annotations

import pytest

from netmgr.domain.models import DeviceState, Permissions, PermissionState
from netmgr.tray.menu_builder import MenuActions, build_menu
from netmgr.tray.menu_model import Menu, ToggleType
from netmgr.tray.status import compute_status

from .factories import ALL_ALLOWED, eth_conn, eth_device, snapshot, wifi_conn, wifi_device


class Recorder:
    """Ghi lại mọi callback menu gọi tới."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def actions(self) -> MenuActions:
        return MenuActions(
            set_wifi_enabled=lambda v: self.calls.append(("wifi", v)),
            activate_connection=lambda u: self.calls.append(("activate", u)),
            deactivate_connection=lambda u: self.calls.append(("deactivate", u)),
            open_wifi_settings=lambda: self.calls.append(("wifi_settings", None)),
            quit=lambda: self.calls.append(("quit", None)),
        )


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


def make_menu(snap, rec: Recorder) -> Menu:
    return Menu(build_menu(snap, compute_status(snap), rec.actions()))


def labels(menu: Menu) -> list[str]:
    return [n.label for n in menu]


def find(menu: Menu, text: str):
    return next((n for n in menu if text in n.label), None)


def find_exact(menu: Menu, label: str):
    """Khớp chính xác — cần khi nhãn ngắn có thể lọt vào nhãn khác
    (vd `find(menu, "B")` sẽ trúng cả "Bật Wi-Fi")."""
    return next((n for n in menu if n.label == label), None)


def find_all(menu: Menu, text: str):
    return [n for n in menu if text in n.label]


# ── cấu trúc chung ───────────────────────────────────────────────────────────


def test_menu_always_has_quit(rec):
    menu = make_menu(snapshot(), rec)
    assert find(menu, "Thoát") is not None


def test_quit_action_wired(rec):
    menu = make_menu(snapshot(), rec)
    menu.activate(find(menu, "Thoát").id)
    assert rec.calls == [("quit", None)]


def test_status_lines_appear_as_disabled_items(rec):
    """Tooltip không hoạt động trên GNOME → thông tin phải nằm trong menu."""
    snap = snapshot(devices=[wifi_device(ssid="X", strength=70)])
    menu = make_menu(snap, rec)
    line = find(menu, "70%")
    assert line is not None
    assert line.enabled is False


def test_title_is_first_item(rec):
    snap = snapshot(devices=[wifi_device(ssid="Viettel Digital")])
    menu = make_menu(snap, rec)
    assert labels(menu)[0] == "Viettel Digital"


def test_networking_disabled_is_announced(rec):
    menu = make_menu(snapshot(networking_enabled=False), rec)
    assert find(menu, "Toàn bộ mạng đang tắt") is not None


# ── submenu Wi-Fi ────────────────────────────────────────────────────────────


def test_no_wifi_hardware_means_no_wifi_submenu(rec):
    menu = make_menu(snapshot(devices=[eth_device()]), rec)
    assert find(menu, "Wi-Fi") is None


def test_wifi_submenu_present_when_hardware_exists(rec):
    menu = make_menu(snapshot(devices=[wifi_device()]), rec)
    assert find(menu, "Wi-Fi") is not None


def test_wifi_toggle_is_checkbox_reflecting_state(rec):
    menu = make_menu(snapshot(devices=[wifi_device()], wifi_enabled=True), rec)
    toggle = find(menu, "Bật Wi-Fi")
    assert toggle.toggle_type is ToggleType.CHECKMARK
    assert toggle.toggle_state is True


def test_wifi_toggle_sends_inverse_of_current_state(rec):
    snap = snapshot(devices=[wifi_device()], wifi_enabled=True)
    menu = make_menu(snap, rec)
    menu.activate(find(menu, "Bật Wi-Fi").id)
    assert rec.calls == [("wifi", False)]


def test_wifi_toggle_turns_on_when_off(rec):
    snap = snapshot(devices=[wifi_device(ip=None)], wifi_enabled=False)
    menu = make_menu(snap, rec)
    menu.activate(find(menu, "Bật Wi-Fi").id)
    assert rec.calls == [("wifi", True)]


def test_hardware_killswitch_disables_toggle(rec):
    snap = snapshot(devices=[wifi_device(ip=None)], wifi_hardware_enabled=False)
    menu = make_menu(snap, rec)
    assert find(menu, "Bật Wi-Fi").enabled is False
    assert find(menu, "công tắc phần cứng") is not None


def test_empty_wifi_list_shows_guidance(rec):
    """§3.2.1 — app không kết nối mạng mới nên phải chỉ đường."""
    menu = make_menu(snapshot(devices=[wifi_device()]), rec)
    assert find(menu, "Chưa có mạng Wi-Fi nào được lưu") is not None
    assert find(menu, "cài đặt hệ thống") is not None


def test_wifi_settings_action_wired(rec):
    menu = make_menu(snapshot(devices=[wifi_device()]), rec)
    menu.activate(find(menu, "cài đặt hệ thống").id)
    assert rec.calls == [("wifi_settings", None)]


def test_saved_wifi_connections_listed(rec):
    snap = snapshot(
        devices=[wifi_device()],
        connections=[wifi_conn("Mạng A"), wifi_conn("Mạng B")],
    )
    menu = make_menu(snap, rec)
    assert find(menu, "Mạng A") is not None
    assert find(menu, "Mạng B") is not None


def test_wifi_connections_sorted_by_name(rec):
    snap = snapshot(
        devices=[wifi_device()],
        connections=[wifi_conn("Zulu"), wifi_conn("Alpha")],
    )
    menu = make_menu(snap, rec)
    names = [n.label.split("  (")[0] for n in menu if n.label.startswith(("Alpha", "Zulu"))]
    assert names == ["Alpha", "Zulu"]


def test_wifi_radio_disabled_when_radio_off(rec):
    snap = snapshot(
        devices=[wifi_device(ip=None)],
        connections=[wifi_conn("Mạng A")],
        wifi_enabled=False,
    )
    menu = make_menu(snap, rec)
    assert find(menu, "Mạng A").enabled is False


# ── radio group connection ───────────────────────────────────────────────────


def test_active_connection_is_selected(rec):
    snap = snapshot(
        devices=[wifi_device()],
        connections=[wifi_conn("Đang dùng", active=True), wifi_conn("Khác")],
    )
    menu = make_menu(snap, rec)
    assert find(menu, "Đang dùng").toggle_state is True
    assert find(menu, "Khác").toggle_state is False


def test_only_active_connections_are_checked(rec):
    snap = snapshot(
        devices=[wifi_device()],
        connections=[wifi_conn("A", active=True), wifi_conn("B"), wifi_conn("C")],
    )
    menu = make_menu(snap, rec)
    # Bỏ công tắc radio Wi-Fi — nó cũng là checkbox nhưng không phải connection.
    checked = [
        n
        for n in menu
        if n.toggle_type is ToggleType.CHECKMARK
        and n.toggle_state
        and n.label != "Bật Wi-Fi"
    ]
    assert [n.label.split("  (")[0] for n in checked] == ["A"]


def test_connections_use_checkbox_not_radio(rec):
    """Nhiều mạng chạy song song là bình thường — radio ngụ ý loại trừ nhau."""
    snap = snapshot(devices=[wifi_device()], connections=[wifi_conn("A")])
    menu = make_menu(snap, rec)
    node = find(menu, "A  (wlp2s0)")
    assert node.toggle_type is ToggleType.CHECKMARK


def test_two_wired_can_both_be_active(rec):
    """Lỗi thật: LAN công ty + iPhone chia sẻ mạng cùng chạy, radio hiện hai
    mục cùng chọn và bấm vào lại thành ngắt."""
    eth_a = eth_device("enp1s0")
    eth_b = eth_device("enx52578a6f8d22", ip="172.20.10.2")
    snap = snapshot(
        devices=[eth_a, eth_b],
        connections=[
            eth_conn("netplan-enp1s0", active=True, interface="enp1s0"),
            eth_conn("Wired connection 1", active=True, interface="enx52578a6f8d22"),
        ],
    )
    menu = make_menu(snap, rec)
    checked = [
        n for n in menu if n.toggle_type is ToggleType.CHECKMARK and n.toggle_state
    ]
    assert len(checked) == 2
    assert all(n.toggle_type is ToggleType.CHECKMARK for n in checked)


def test_label_includes_interface_to_disambiguate(rec):
    """NetworkManager tự đặt tên trùng nhau cho nhiều cổng mạng dây."""
    snap = snapshot(
        devices=[eth_device("enp1s0"), eth_device("enx52578a6f8d22")],
        connections=[
            eth_conn("Wired connection 1", interface="enp1s0"),
            eth_conn("Wired connection 1", interface="enx52578a6f8d22"),
        ],
    )
    menu = make_menu(snap, rec)
    labels_found = [n.label for n in menu if "Wired connection 1" in n.label]
    assert len(labels_found) == 2
    assert len(set(labels_found)) == 2, "hai mục vẫn trùng nhãn"
    assert any("enp1s0" in l for l in labels_found)
    assert any("enx52578a6f8d22" in l for l in labels_found)


def test_connection_without_device_is_disabled(rec):
    """Profile ghim vào cổng đã rút thì kích hoạt chắc chắn thất bại."""
    snap = snapshot(
        devices=[eth_device("enp1s0")],
        connections=[eth_conn("Dock LAN", interface="enx-đã-rút")],
    )
    menu = make_menu(snap, rec)
    node = find(menu, "Dock LAN")
    assert node.enabled is False
    assert "Không tìm thấy thiết bị" in node.description


def test_clicking_inactive_connection_activates_it(rec):
    target = wifi_conn("Mạng A")
    snap = snapshot(devices=[wifi_device()], connections=[target])
    menu = make_menu(snap, rec)
    menu.activate(find(menu, "Mạng A").id)
    assert rec.calls == [("activate", target.uuid)]


def test_clicking_active_connection_deactivates_it(rec):
    target = wifi_conn("Mạng A", active=True)
    snap = snapshot(devices=[wifi_device()], connections=[target])
    menu = make_menu(snap, rec)
    menu.activate(find(menu, "Mạng A").id)
    assert rec.calls == [("deactivate", target.uuid)]


def test_each_item_carries_its_own_uuid(rec):
    """Bẫy closure kinh điển: mọi item cùng trỏ về connection cuối cùng."""
    a, b = wifi_conn("Mạng A"), wifi_conn("Mạng B")
    snap = snapshot(devices=[wifi_device()], connections=[a, b])
    menu = make_menu(snap, rec)
    menu.activate(find(menu, "Mạng A  (").id)
    menu.activate(find(menu, "Mạng B  (").id)
    assert rec.calls == [("activate", a.uuid), ("activate", b.uuid)]


# ── device đang bận ──────────────────────────────────────────────────────────


def test_busy_device_disables_its_connection(rec):
    """Tránh double-click gửi hai lệnh activate chồng nhau."""
    snap = snapshot(
        devices=[wifi_device(state=DeviceState.CONNECTING)],
        connections=[wifi_conn("Mạng A")],
    )
    menu = make_menu(snap, rec)
    node = find(menu, "Mạng A")
    assert node.enabled is False
    assert "đang xử lý" in node.label


def test_busy_item_does_not_fire_action(rec):
    snap = snapshot(
        devices=[wifi_device(state=DeviceState.CONNECTING)],
        connections=[wifi_conn("Mạng A")],
    )
    menu = make_menu(snap, rec)
    menu.activate(find(menu, "Mạng A").id)
    assert rec.calls == []


# ── submenu có dây ───────────────────────────────────────────────────────────


def test_no_ethernet_device_means_no_wired_submenu(rec):
    menu = make_menu(snapshot(devices=[wifi_device()]), rec)
    assert find(menu, "Có dây") is None


def test_wired_submenu_lists_connections(rec):
    snap = snapshot(devices=[eth_device()], connections=[eth_conn("LAN Công ty")])
    menu = make_menu(snap, rec)
    assert find(menu, "Có dây") is not None
    assert find(menu, "LAN Công ty") is not None


def test_unplugged_cable_is_reported(rec):
    snap = snapshot(
        devices=[eth_device(state=DeviceState.UNAVAILABLE, carrier=False, ip=None)]
    )
    menu = make_menu(snap, rec)
    assert find(menu, "chưa cắm cáp") is not None


def test_wired_without_connections_says_so(rec):
    menu = make_menu(snapshot(devices=[eth_device()]), rec)
    assert find(menu, "Chưa có cấu hình có dây") is not None


def test_wifi_and_ethernet_connections_are_separate_groups(rec):
    snap = snapshot(
        devices=[wifi_device(), eth_device()],
        connections=[wifi_conn("WiFi A"), eth_conn("LAN A")],
    )
    menu = make_menu(snap, rec)
    wifi_node = find(menu, "Wi-Fi")
    wired_node = find(menu, "Có dây")
    assert any("WiFi A" in c.label for c in wifi_node.children)
    assert any("LAN A" in c.label for c in wired_node.children)


# ── quyền ────────────────────────────────────────────────────────────────────


def test_denied_network_control_disables_connection_items(rec):
    perms = Permissions(
        modify_system=PermissionState.YES,
        network_control=PermissionState.NO,
        enable_disable_wifi=PermissionState.YES,
    )
    snap = snapshot(
        devices=[wifi_device()], connections=[wifi_conn("Mạng A")], permissions=perms
    )
    menu = make_menu(snap, rec)
    assert find(menu, "Mạng A").enabled is False


def test_unknown_permissions_do_not_disable_anything(rec):
    """Lúc khởi động mọi permission là UNKNOWN — UI không được xám hết."""
    snap = snapshot(
        devices=[wifi_device()],
        connections=[wifi_conn("Mạng A")],
        permissions=Permissions(),
    )
    menu = make_menu(snap, rec)
    assert find(menu, "Mạng A").enabled is True
    assert find(menu, "Bật Wi-Fi").enabled is True


def test_denied_modify_shows_explanation(rec):
    perms = Permissions(modify_system=PermissionState.NO)
    menu = make_menu(snapshot(permissions=perms), rec)
    assert find(menu, "Không đủ quyền") is not None


def test_allowed_permissions_show_no_warning(rec):
    menu = make_menu(snapshot(permissions=ALL_ALLOWED), rec)
    assert find(menu, "Không đủ quyền") is None


# ── mục của giai đoạn sau ────────────────────────────────────────────────────


def test_unimplemented_items_are_disabled_and_labelled(rec):
    """Không để item bấm vào rồi không có gì xảy ra."""
    menu = make_menu(snapshot(), rec)
    for node in find_all(menu, "chưa có ở bản này"):
        assert node.enabled is False


def test_proxy_and_profile_placeholders_present(rec):
    menu = make_menu(snapshot(), rec)
    assert find(menu, "Proxy") is not None
    assert find(menu, "Bộ cấu hình") is not None


def test_proxy_placeholder_when_service_missing(rec):
    menu = make_menu(snapshot(), rec)
    node = find(menu, "Proxy")
    assert node.enabled is False


# ── submenu proxy (P2) ───────────────────────────────────────────────────────


class FakeProxyService:
    def __init__(self, configs=None, active=None, statuses=None) -> None:
        self.configs = configs or []
        self.active = active
        self._statuses = statuses or []

    def layer_statuses(self):
        return self._statuses


def proxy_actions(rec: Recorder) -> MenuActions:
    actions = rec.actions()
    actions.activate_proxy = lambda cid: rec.calls.append(("proxy_on", cid))
    actions.disable_proxy = lambda: rec.calls.append(("proxy_off", None))
    actions.import_current_proxy = lambda: rec.calls.append(("proxy_import", None))
    actions.copy_proxy_snippet = lambda: rec.calls.append(("proxy_copy", None))
    return actions


def proxy_menu(rec: Recorder, proxy) -> Menu:
    snap = snapshot()
    return Menu(build_menu(snap, compute_status(snap), proxy_actions(rec), proxy=proxy))


def make_proxy_config(name="Công ty", cid="p1"):
    from netmgr.domain.models import ProxyConfig, ProxyEndpoint, ProxyMode

    return ProxyConfig(
        id=cid, name=name, mode=ProxyMode.MANUAL, http=ProxyEndpoint("10.0.0.8", 3128)
    )


def test_proxy_submenu_shows_off_selected_when_inactive(rec):
    menu = proxy_menu(rec, FakeProxyService())
    off = find_exact(menu, "Tắt")
    assert off.toggle_type is ToggleType.RADIO
    assert off.toggle_state is True


def test_proxy_label_shows_active_name(rec):
    cfg = make_proxy_config()
    menu = proxy_menu(rec, FakeProxyService(configs=[cfg], active=cfg))
    assert find(menu, "Proxy: Công ty") is not None


def test_proxy_label_shows_off_when_inactive(rec):
    menu = proxy_menu(rec, FakeProxyService())
    assert find(menu, "Proxy: Tắt") is not None


def test_proxy_configs_listed_with_summary(rec):
    cfg = make_proxy_config()
    menu = proxy_menu(rec, FakeProxyService(configs=[cfg]))
    node = find(menu, "Công ty")
    assert "10.0.0.8:3128" in node.label


def test_clicking_proxy_config_activates_it(rec):
    cfg = make_proxy_config(cid="abc")
    menu = proxy_menu(rec, FakeProxyService(configs=[cfg]))
    menu.activate(find(menu, "Công ty").id)
    assert rec.calls == [("proxy_on", "abc")]


def test_each_proxy_item_carries_own_id(rec):
    a = make_proxy_config("Alpha", "id-a")
    b = make_proxy_config("Beta", "id-b")
    menu = proxy_menu(rec, FakeProxyService(configs=[a, b]))
    menu.activate(find(menu, "Alpha").id)
    menu.activate(find(menu, "Beta").id)
    assert rec.calls == [("proxy_on", "id-a"), ("proxy_on", "id-b")]


def test_clicking_off_disables_proxy(rec):
    cfg = make_proxy_config()
    menu = proxy_menu(rec, FakeProxyService(configs=[cfg], active=cfg))
    menu.activate(find_exact(menu, "Tắt").id)
    assert rec.calls == [("proxy_off", None)]


def find_radio(menu: Menu, text: str):
    """Chỉ tìm trong các item radio — nhãn submenu ("Proxy: Beta") cũng chứa
    tên cấu hình nên `find` thường sẽ trúng nhầm nó trước."""
    return next(
        (n for n in menu if n.toggle_type is ToggleType.RADIO and text in n.label), None
    )


def test_active_proxy_marked_selected(rec):
    a = make_proxy_config("Alpha", "id-a")
    b = make_proxy_config("Beta", "id-b")
    menu = proxy_menu(rec, FakeProxyService(configs=[a, b], active=b))
    assert find_radio(menu, "Beta").toggle_state is True
    assert find_radio(menu, "Alpha").toggle_state is False
    assert find_exact(menu, "Tắt").toggle_state is False


def test_exactly_one_proxy_radio_selected(rec):
    a = make_proxy_config("Alpha", "id-a")
    b = make_proxy_config("Beta", "id-b")
    menu = proxy_menu(rec, FakeProxyService(configs=[a, b], active=b))
    selected = [
        n for n in menu if n.toggle_type is ToggleType.RADIO and n.toggle_state
    ]
    assert len(selected) == 1


def test_empty_proxy_list_says_so(rec):
    menu = proxy_menu(rec, FakeProxyService())
    assert find(menu, "Chưa có cấu hình proxy nào") is not None


def test_layer_statuses_shown_in_menu(rec):
    """R4 — phải trả lời được 'tắt proxy rồi mà apt vẫn qua proxy?'."""
    from netmgr.domain.models import ProxyLayerId
    from netmgr.infra.proxy.base import LayerStatus

    statuses = [
        LayerStatus(ProxyLayerId.DESKTOP, "Desktop", True, True, "10.0.0.8:3128"),
        LayerStatus(
            ProxyLayerId.ENVIRONMENT, "Môi trường", True, False, "Tắt",
            note="cần mở terminal mới",
        ),
    ]
    menu = proxy_menu(rec, FakeProxyService(statuses=statuses))
    assert find(menu, "● Desktop: 10.0.0.8:3128") is not None
    assert find(menu, "○ Môi trường: Tắt · cần mở terminal mới") is not None


def test_import_action_wired(rec):
    menu = proxy_menu(rec, FakeProxyService())
    menu.activate(find(menu, "Nhập cấu hình proxy").id)
    assert rec.calls == [("proxy_import", None)]


def test_copy_snippet_action_wired(rec):
    menu = proxy_menu(rec, FakeProxyService())
    menu.activate(find(menu, "Chép lệnh export").id)
    assert rec.calls == [("proxy_copy", None)]


# ── submenu Bộ cấu hình (P5) ─────────────────────────────────────────────────


class FakeProfileService:
    def __init__(self, profiles=None, active=None, busy=False) -> None:
        self.profiles = profiles or []
        self.active = active
        self.busy = busy


def profile_actions(rec: Recorder) -> MenuActions:
    actions = rec.actions()
    actions.apply_profile = lambda pid: rec.calls.append(("apply", pid))
    actions.clear_profile = lambda: rec.calls.append(("clear", None))
    actions.capture_profile = lambda: rec.calls.append(("capture", None))
    actions.manage_profiles = lambda: rec.calls.append(("manage", None))
    return actions


def profile_menu(rec: Recorder, profiles) -> Menu:
    snap = snapshot()
    return Menu(
        build_menu(snap, compute_status(snap), profile_actions(rec), profiles=profiles)
    )


def make_profile(name="Công ty", pid="p1", icon="🏢", broken=None):
    from netmgr.infra.profile_store import new_profile

    profile = new_profile(name, icon=icon)
    profile.id = pid
    profile.broken_reason = broken
    return profile


def test_profile_placeholder_without_service(rec):
    menu = make_menu(snapshot(), rec)
    assert find(menu, "Bộ cấu hình").enabled is False


def test_profile_label_shows_active(rec):
    p = make_profile()
    menu = profile_menu(rec, FakeProfileService([p], active=p))
    assert find(menu, "Bộ cấu hình: 🏢 Công ty") is not None


def test_profile_label_when_none_active(rec):
    menu = profile_menu(rec, FakeProfileService())
    assert find(menu, "Bộ cấu hình: Không dùng") is not None


def test_profile_label_while_applying(rec):
    menu = profile_menu(rec, FakeProfileService(busy=True))
    assert find(menu, "đang áp dụng") is not None


def test_clicking_profile_applies_it(rec):
    p = make_profile(pid="abc")
    menu = profile_menu(rec, FakeProfileService([p]))
    menu.activate(find(menu, "🏢 Công ty").id)
    assert rec.calls == [("apply", "abc")]


def test_each_profile_carries_own_id(rec):
    a = make_profile("Nhà", "id-a", "🏠")
    b = make_profile("Công ty", "id-b", "🏢")
    menu = profile_menu(rec, FakeProfileService([a, b]))
    menu.activate(find(menu, "🏠 Nhà").id)
    menu.activate(find(menu, "🏢 Công ty").id)
    assert rec.calls == [("apply", "id-a"), ("apply", "id-b")]


def test_active_profile_is_selected(rec):
    a = make_profile("Nhà", "id-a", "🏠")
    b = make_profile("Công ty", "id-b", "🏢")
    menu = profile_menu(rec, FakeProfileService([a, b], active=b))
    assert find_radio(menu, "Công ty").toggle_state is True
    assert find_radio(menu, "Nhà").toggle_state is False


def test_no_profile_option_selected_when_inactive(rec):
    menu = profile_menu(rec, FakeProfileService([make_profile()]))
    assert find(menu, "Không dùng Bộ cấu hình").toggle_state is True


def test_clear_profile_action_wired(rec):
    p = make_profile()
    menu = profile_menu(rec, FakeProfileService([p], active=p))
    menu.activate(find(menu, "Không dùng Bộ cấu hình").id)
    assert rec.calls == [("clear", None)]


def test_broken_profile_marked_and_disabled(rec):
    p = make_profile(broken="Cấu hình kết nối đã bị xoá")
    menu = profile_menu(rec, FakeProfileService([p]))
    node = find(menu, "Công ty")
    assert "⚠" in node.label
    assert node.enabled is False


def test_busy_disables_every_profile_choice(rec):
    """Hai lệnh áp dụng chồng nhau sẽ để mạng ở trạng thái lai."""
    p = make_profile()
    menu = profile_menu(rec, FakeProfileService([p], busy=True))
    assert find(menu, "🏢 Công ty").enabled is False
    assert find(menu, "Không dùng Bộ cấu hình").enabled is False


def test_empty_profile_list_says_so(rec):
    menu = profile_menu(rec, FakeProfileService())
    assert find(menu, "Chưa có Bộ cấu hình nào") is not None


def test_capture_and_manage_actions_wired(rec):
    menu = profile_menu(rec, FakeProfileService())
    menu.activate(find(menu, "Lưu trạng thái hiện tại").id)
    menu.activate(find(menu, "Quản lý Bộ cấu hình").id)
    assert rec.calls == [("capture", None), ("manage", None)]


def test_profile_submenu_comes_before_connections(rec):
    """Chuyển bối cảnh là thao tác chính, phải nằm trên cùng."""
    snap = snapshot(devices=[wifi_device()])
    menu = Menu(
        build_menu(snap, compute_status(snap), profile_actions(rec),
                   profiles=FakeProfileService())
    )
    order = [n.label for n in menu]
    assert order.index("Bộ cấu hình: Không dùng") < order.index("Wi-Fi")


def test_no_settings_item_in_tray(rec):
    """Mở cửa sổ bằng app trong menu ứng dụng hoặc click chuột giữa — mục
    "Cài đặt…" trong tray chỉ làm menu dài thêm."""
    menu = make_menu(snapshot(), rec)
    assert find(menu, "Cài đặt") is None


def test_no_wifi_submenu_when_adapter_unavailable(rec):
    """Adapter đã rút: đừng chiếm chỗ bằng submenu rỗng hay dòng 'không khả dụng'.

    (Dòng tiêu đề trạng thái "Wi-Fi đã tắt" ở đầu menu là chuyện khác — nó nói
    trạng thái máy, không phải một mục bấm được.)
    """
    snap = snapshot(
        devices=[wifi_device(state=DeviceState.UNAVAILABLE, ip=None)],
        wifi_enabled=False,
    )
    menu = make_menu(snap, rec)
    assert find(menu, "Bật Wi-Fi") is None
    assert find(menu, "không khả dụng") is None
    assert not any(n.has_children and "Wi-Fi" in n.label for n in menu)
