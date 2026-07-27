"""Model cho menu tray — thuần Python, không phụ thuộc D-Bus.

Tách riêng để logic "menu gồm những gì" test được mà không cần dựng bus. Module
`sni` chỉ làm việc chuyển model này sang giao thức `com.canonical.dbusmenu`.

Giới hạn của chuẩn dbusmenu (§4.1): menu do GNOME Shell render, nên chỉ có
label, checkbox, radio, submenu, separator. Không nhúng được widget tuỳ ý.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum

#: id 0 luôn là node gốc theo đặc tả dbusmenu.
ROOT_ID = 0


class ToggleType(str, Enum):
    NONE = ""
    CHECKMARK = "checkmark"
    RADIO = "radio"


@dataclass(slots=True)
class MenuItem:
    label: str = ""
    action: Callable[[], None] | None = None
    enabled: bool = True
    visible: bool = True
    icon_name: str = ""
    toggle_type: ToggleType = ToggleType.NONE
    toggle_state: bool | None = None      # None = không phải toggle
    children: list[MenuItem] = field(default_factory=list)
    is_separator: bool = False
    #: Mô tả dài cho screen reader / tooltip của item.
    description: str = ""

    #: Gán khi flatten, không set thủ công.
    id: int = -1

    @property
    def has_children(self) -> bool:
        return bool(self.children)

    def dbus_properties(self) -> dict[str, object]:
        """Thuộc tính theo đặc tả dbusmenu. Chỉ trả về cái khác mặc định."""
        if self.is_separator:
            return {"type": "separator", "visible": self.visible}

        props: dict[str, object] = {"label": _escape_mnemonics(self.label)}
        if not self.enabled:
            props["enabled"] = False
        if not self.visible:
            props["visible"] = False
        if self.icon_name:
            props["icon-name"] = self.icon_name
        if self.toggle_type is not ToggleType.NONE:
            props["toggle-type"] = self.toggle_type.value
            # -1 nghĩa là "không xác định"; chỉ dùng 0/1 khi thực sự là toggle.
            props["toggle-state"] = 1 if self.toggle_state else 0
        if self.has_children:
            props["children-display"] = "submenu"
        if self.description:
            props["accessible-desc"] = self.description
        return props


def _escape_mnemonics(label: str) -> str:
    """`_` trong dbusmenu là ký tự phím tắt — nhân đôi để hiện đúng chữ.

    Tên interface như `enp3s0` không có `_`, nhưng SSID và tên connection do
    người dùng đặt thì hoàn toàn có thể ("Wifi_Nha").
    """
    return label.replace("_", "__")


# ─────────────────────────────────────────────────────────────────────────────
# Hàm dựng cho gọn
# ─────────────────────────────────────────────────────────────────────────────


def separator() -> MenuItem:
    return MenuItem(is_separator=True)


def item(label: str, action: Callable[[], None] | None = None, **kw) -> MenuItem:
    return MenuItem(label=label, action=action, **kw)


def info(label: str, **kw) -> MenuItem:
    """Dòng chỉ để đọc — không bấm được."""
    return MenuItem(label=label, enabled=False, **kw)


def submenu(label: str, children: list[MenuItem], **kw) -> MenuItem:
    return MenuItem(label=label, children=children, **kw)


def checkbox(label: str, checked: bool, action=None, **kw) -> MenuItem:
    return MenuItem(
        label=label, action=action, toggle_type=ToggleType.CHECKMARK,
        toggle_state=checked, **kw,
    )


def radio(label: str, selected: bool, action=None, **kw) -> MenuItem:
    return MenuItem(
        label=label, action=action, toggle_type=ToggleType.RADIO,
        toggle_state=selected, **kw,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Menu hoàn chỉnh
# ─────────────────────────────────────────────────────────────────────────────


class Menu:
    """Cây menu đã gán id, sẵn sàng phục vụ dbusmenu."""

    def __init__(self, items: list[MenuItem]) -> None:
        self.root = MenuItem(label="root", children=items, id=ROOT_ID)
        self._by_id: dict[int, MenuItem] = {ROOT_ID: self.root}
        self._assign_ids()

    def _assign_ids(self) -> None:
        next_id = ROOT_ID + 1
        for node in self._walk(self.root, skip_root=True):
            node.id = next_id
            self._by_id[next_id] = node
            next_id += 1

    def _walk(self, node: MenuItem, skip_root: bool = False) -> Iterator[MenuItem]:
        if not skip_root:
            yield node
        for child in node.children:
            yield from self._walk(child)

    def __iter__(self) -> Iterator[MenuItem]:
        return self._walk(self.root, skip_root=True)

    def __len__(self) -> int:
        return len(self._by_id) - 1          # không tính root

    def get(self, item_id: int) -> MenuItem | None:
        return self._by_id.get(item_id)

    def activate(self, item_id: int) -> bool:
        """Chạy action của item. Trả về True nếu có action và đã chạy."""
        node = self.get(item_id)
        if node is None or node.action is None or not node.enabled:
            return False
        node.action()
        return True

    def layout(self, parent_id: int = ROOT_ID, depth: int = -1) -> tuple | None:
        """Layout đệ quy theo định dạng `(ia{sv}av)` của dbusmenu."""
        node = self.get(parent_id)
        if node is None:
            return None
        return self._layout_node(node, depth)

    def _layout_node(self, node: MenuItem, depth: int) -> tuple:
        children: list[tuple] = []
        if depth != 0:
            children = [
                self._layout_node(child, depth - 1)
                for child in node.children
                if child.visible
            ]
        return (node.id, node.dbus_properties(), children)
