"""Test cho cây menu — gán id, layout dbusmenu, kích hoạt action."""

from __future__ import annotations

from netmgr.tray.menu_model import (
    ROOT_ID,
    Menu,
    MenuItem,
    ToggleType,
    checkbox,
    info,
    item,
    radio,
    separator,
    submenu,
)


# ── gán id ───────────────────────────────────────────────────────────────────


def test_root_has_id_zero():
    assert Menu([]).root.id == ROOT_ID


def test_ids_are_unique_and_start_after_root():
    menu = Menu([item("a"), item("b"), submenu("c", [item("d"), item("e")])])
    ids = [node.id for node in menu]
    assert len(ids) == len(set(ids))
    assert min(ids) == ROOT_ID + 1


def test_len_counts_all_items_excluding_root():
    menu = Menu([item("a"), submenu("b", [item("c")])])
    assert len(menu) == 3


def test_get_returns_the_right_node():
    target = item("tìm tôi")
    menu = Menu([item("a"), submenu("b", [target])])
    assert menu.get(target.id) is target


def test_get_unknown_id_returns_none():
    assert Menu([item("a")]).get(999) is None


# ── thuộc tính dbusmenu ──────────────────────────────────────────────────────


def test_separator_properties():
    props = separator().dbus_properties()
    assert props["type"] == "separator"


def test_plain_item_only_emits_label():
    assert item("Thoát").dbus_properties() == {"label": "Thoát"}


def test_disabled_item_emits_enabled_false():
    assert info("Chỉ đọc").dbus_properties()["enabled"] is False


def test_submenu_declares_children_display():
    props = submenu("Wi-Fi", [item("a")]).dbus_properties()
    assert props["children-display"] == "submenu"


def test_checkbox_properties():
    props = checkbox("Bật Wi-Fi", checked=True).dbus_properties()
    assert props["toggle-type"] == "checkmark"
    assert props["toggle-state"] == 1


def test_unchecked_checkbox_state_is_zero():
    assert checkbox("x", checked=False).dbus_properties()["toggle-state"] == 0


def test_radio_properties():
    props = radio("Mạng A", selected=True).dbus_properties()
    assert props["toggle-type"] == "radio"
    assert props["toggle-state"] == 1


def test_non_toggle_item_has_no_toggle_keys():
    props = item("Thường").dbus_properties()
    assert "toggle-type" not in props and "toggle-state" not in props


def test_icon_name_included_when_set():
    assert item("x", icon_name="network-wired-symbolic").dbus_properties()[
        "icon-name"
    ] == "network-wired-symbolic"


# ── escape mnemonic ──────────────────────────────────────────────────────────


def test_underscore_in_label_is_escaped():
    """SSID người dùng đặt hoàn toàn có thể chứa `_`, mà dbusmenu coi đó là phím tắt."""
    assert item("Wifi_Nha").dbus_properties()["label"] == "Wifi__Nha"


def test_multiple_underscores_all_escaped():
    assert item("a_b_c").dbus_properties()["label"] == "a__b__c"


def test_label_without_underscore_unchanged():
    assert item("Viettel Digital").dbus_properties()["label"] == "Viettel Digital"


# ── layout ───────────────────────────────────────────────────────────────────


def test_layout_returns_root_with_children():
    menu = Menu([item("a"), item("b")])
    node_id, props, children = menu.layout()
    assert node_id == ROOT_ID
    assert props["children-display"] == "submenu"
    assert len(children) == 2


def test_layout_is_recursive_by_default():
    menu = Menu([submenu("outer", [submenu("inner", [item("deep")])])])
    _, _, root_children = menu.layout()
    _, _, outer_children = root_children[0]
    _, _, inner_children = outer_children[0]
    deep_id, deep_props, deep_children = inner_children[0]
    assert deep_props["label"] == "deep"
    assert deep_children == []          # "deep" là lá


def test_layout_depth_zero_returns_no_children():
    menu = Menu([item("a")])
    _, _, children = menu.layout(depth=0)
    assert children == []


def test_layout_depth_one_returns_only_direct_children():
    menu = Menu([submenu("outer", [item("inner")])])
    _, _, children = menu.layout(depth=1)
    assert len(children) == 1
    _, _, grandchildren = children[0]
    assert grandchildren == []


def test_layout_of_subtree():
    target = submenu("Wi-Fi", [item("a"), item("b")])
    menu = Menu([item("trước"), target])
    node_id, _, children = menu.layout(target.id)
    assert node_id == target.id
    assert len(children) == 2


def test_layout_of_unknown_id_returns_none():
    assert Menu([item("a")]).layout(999) is None


def test_invisible_items_excluded_from_layout():
    menu = Menu([item("hiện"), MenuItem(label="ẩn", visible=False)])
    _, _, children = menu.layout()
    assert len(children) == 1


# ── kích hoạt ────────────────────────────────────────────────────────────────


def test_activate_runs_action():
    calls = []
    target = item("bấm tôi", action=lambda: calls.append(1))
    menu = Menu([target])
    assert menu.activate(target.id) is True
    assert calls == [1]


def test_activate_disabled_item_does_nothing():
    calls = []
    target = item("khoá", action=lambda: calls.append(1), enabled=False)
    menu = Menu([target])
    assert menu.activate(target.id) is False
    assert calls == []


def test_activate_item_without_action_returns_false():
    target = info("chỉ đọc")
    assert Menu([target]).activate(target.id) is False


def test_activate_unknown_id_returns_false():
    assert Menu([item("a")]).activate(999) is False


def test_activate_nested_item():
    calls = []
    target = item("sâu", action=lambda: calls.append(1))
    menu = Menu([submenu("ngoài", [submenu("trong", [target])])])
    assert menu.activate(target.id) is True
    assert calls == [1]


def test_toggle_type_enum_default_is_none():
    assert item("x").toggle_type is ToggleType.NONE
