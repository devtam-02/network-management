"""StatusNotifierItem + dbusmenu tự implement trên Gio.DBus.

Thay cho `AyatanaAppIndicator3` (chỉ có binding GTK3, mà GTK3 không sống chung
tiến trình với GTK4 được). Tự làm nên app chạy GTK4/libadwaita trong một tiến
trình và không cần cài thêm gói hệ thống.

Contract dưới đây đã được xác minh bằng `busctl introspect` trên các item đang
chạy thật trên máy, và bằng cách đọc source của extension
`ubuntu-appindicators@ubuntu.com`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from gi.repository import Gio, GLib

from .menu_model import ROOT_ID, Menu
from .status import TrayStatus

log = logging.getLogger(__name__)

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"

SNI_IFACE = "org.kde.StatusNotifierItem"
SNI_PATH = "/StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"

# CỐ TÌNH không khai báo method `Activate`.
#
# appIndicator.js:474 đặt `supportsActivation = !!interfaceInfo.lookup_method('Activate')`,
# và indicatorStatusIcon.js:440 — nếu supportsActivation khác false thì click trái
# sẽ CHỜ xem có double-click không rồi mới xử lý. Bỏ `Activate` đi thì GNOME mở
# menu ngay lập tức, đúng hành vi mong muốn cho một applet mạng.
SNI_XML = f"""
<node>
  <interface name="{SNI_IFACE}">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="IconAccessibleDesc" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionAccessibleDesc" type="s" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <method name="ProvideXdgActivationToken">
      <arg name="token" type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
    <signal name="XAyatanaNewLabel">
      <arg name="label" type="s"/>
      <arg name="guide" type="s"/>
    </signal>
  </interface>
</node>
"""

MENU_XML = f"""
<node>
  <interface name="{MENU_IFACE}">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{{sv}}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{{sv}})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{{sv}})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""


def _to_variant(value: object) -> GLib.Variant:
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        return GLib.Variant("i", value)
    return GLib.Variant("s", str(value))


def _layout_to_variant(node: tuple) -> GLib.Variant:
    """Chuyển (id, props, children) sang kiểu đệ quy `(ia{sv}av)` của dbusmenu."""
    item_id, props, children = node
    return GLib.Variant(
        "(ia{sv}av)",
        (
            item_id,
            {key: _to_variant(val) for key, val in props.items()},
            [_layout_to_variant(child) for child in children],
        ),
    )


class StatusNotifierItem:
    """Icon tray sống trên D-Bus.

    Vòng đời: `start()` → cập nhật bằng `set_status()`/`set_menu()` → `close()`.
    """

    def __init__(
        self,
        app_id: str,
        *,
        title: str = "",
        on_secondary_activate: Callable[[], None] | None = None,
        on_scroll: Callable[[int, str], None] | None = None,
        on_about_to_show: Callable[[], None] | None = None,
    ) -> None:
        self.app_id = app_id
        self._title = title or app_id
        self._on_secondary_activate = on_secondary_activate
        self._on_scroll = on_scroll
        self._on_about_to_show = on_about_to_show

        self._bus_name = f"{SNI_IFACE}-{os.getpid()}-1"
        self._conn: Gio.DBusConnection | None = None
        self._owner_id = 0
        self._watcher_id = 0
        self._reg_ids: list[int] = []
        self._registered_with_watcher = False

        self._status = TrayStatus(icon_name="network-offline-symbolic", title=self._title)
        self._menu = Menu([])
        self._menu_revision = 1
        #: Layout đã báo cho host lần gần nhất, để phát hiện thay đổi thật sự.
        self._last_layout: tuple | None = None

    # ── vòng đời ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).lookup_interface(SNI_IFACE)
        menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).lookup_interface(MENU_IFACE)

        # Đăng ký object TRƯỚC khi giành bus name: ngay khi name xuất hiện,
        # host có thể hỏi ngay, không được để nó hỏi vào chỗ trống.
        self._reg_ids.append(
            self._conn.register_object(
                SNI_PATH, sni_info, self._sni_method, self._sni_get_property, None
            )
        )
        self._reg_ids.append(
            self._conn.register_object(
                MENU_PATH, menu_info, self._menu_method, self._menu_get_property, None
            )
        )

        self._owner_id = Gio.bus_own_name_on_connection(
            self._conn,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_name_acquired,
            self._on_name_lost,
        )

        # Theo dõi watcher: gnome-shell restart hoặc extension bật/tắt sẽ làm
        # watcher biến mất rồi hiện lại. Không đăng ký lại thì icon mất vĩnh viễn.
        self._watcher_id = Gio.bus_watch_name_on_connection(
            self._conn,
            WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            self._on_watcher_vanished,
        )

    def _on_name_acquired(self, _conn, name) -> None:
        log.debug("Đã giành bus name %s", name)

    def _on_name_lost(self, _conn, name) -> None:
        log.warning("Mất bus name %s — tray sẽ không hiển thị", name)

    def _on_watcher_appeared(self, conn, _name, _owner) -> None:
        log.debug("Tìm thấy StatusNotifierWatcher, đang đăng ký")
        conn.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_IFACE,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_register_done,
        )

    def _on_register_done(self, conn, result) -> None:
        try:
            conn.call_finish(result)
        except GLib.Error as exc:
            log.error("Đăng ký với StatusNotifierWatcher thất bại: %s", exc.message)
            self._registered_with_watcher = False
        else:
            self._registered_with_watcher = True
            log.info("Tray icon đã đăng ký (%s)", self._bus_name)

    def _on_watcher_vanished(self, _conn, _name) -> None:
        # Không phải lỗi: người dùng có thể vừa tắt extension, hoặc gnome-shell
        # đang khởi động lại. Chỉ cần nhớ là chưa đăng ký để lúc nó quay lại
        # thì đăng ký lại.
        self._registered_with_watcher = False
        log.info("StatusNotifierWatcher biến mất — chờ nó quay lại")

    @property
    def is_registered(self) -> bool:
        return self._registered_with_watcher

    def close(self) -> None:
        if self._watcher_id:
            Gio.bus_unwatch_name(self._watcher_id)
            self._watcher_id = 0
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        if self._conn is not None:
            for reg_id in self._reg_ids:
                self._conn.unregister_object(reg_id)
        self._reg_ids.clear()
        self._registered_with_watcher = False

    # ── cập nhật nội dung ───────────────────────────────────────────────────

    def set_status(self, status: TrayStatus) -> None:
        previous, self._status = self._status, status
        if self._conn is None:
            return

        if previous.icon_name != status.icon_name:
            self._emit(SNI_PATH, SNI_IFACE, "NewIcon", None)
        if previous.overlay_icon != status.overlay_icon:
            self._emit(SNI_PATH, SNI_IFACE, "NewOverlayIcon", None)
        if previous.title != status.title:
            self._emit(SNI_PATH, SNI_IFACE, "NewTitle", None)
        if previous.status_lines != status.status_lines:
            self._emit(SNI_PATH, SNI_IFACE, "NewToolTip", None)
        if previous.attention != status.attention:
            self._emit(
                SNI_PATH, SNI_IFACE, "NewStatus",
                GLib.Variant("(s)", (self._sni_status_string(),)),
            )
        if previous.label != status.label:
            self._emit(
                SNI_PATH, SNI_IFACE, "XAyatanaNewLabel",
                GLib.Variant("(ss)", (status.label, "")),
            )

    def set_menu(self, menu: Menu) -> None:
        """Thay menu; chỉ báo cho host khi nội dung THỰC SỰ đổi.

        Luôn thay `self._menu` (closure trong action trỏ tới trạng thái mới),
        nhưng chỉ tăng revision khi layout khác đi. Nếu tăng vô điều kiện thì mỗi
        signal của NM sẽ khiến GNOME tải lại toàn bộ menu — lãng phí, và làm
        `AboutToShow` lúc nào cũng báo "đã đổi".
        """
        layout = menu.layout()
        self._menu = menu
        if layout == self._last_layout:
            return

        self._last_layout = layout
        self._menu_revision += 1
        if self._conn is not None:
            self._emit(
                MENU_PATH, MENU_IFACE, "LayoutUpdated",
                GLib.Variant("(ui)", (self._menu_revision, ROOT_ID)),
            )

    def _emit(self, path: str, iface: str, signal: str, params) -> None:
        try:
            self._conn.emit_signal(None, path, iface, signal, params)
        except GLib.Error as exc:  # pragma: no cover
            log.warning("Không phát được signal %s: %s", signal, exc.message)

    def _sni_status_string(self) -> str:
        return "NeedsAttention" if self._status.attention else "Active"

    # ── org.kde.StatusNotifierItem ──────────────────────────────────────────

    def _sni_get_property(self, _conn, _sender, _path, _iface, name):
        s = self._status
        empty_pixmap = GLib.Variant("a(iiay)", [])

        match name:
            case "Category":
                return GLib.Variant("s", "SystemServices")
            case "Id":
                return GLib.Variant("s", self.app_id)
            case "Title":
                return GLib.Variant("s", s.title or self._title)
            case "Status":
                return GLib.Variant("s", self._sni_status_string())
            case "WindowId":
                return GLib.Variant("i", 0)
            case "IconThemePath":
                return GLib.Variant("s", "")
            case "Menu":
                return GLib.Variant("o", MENU_PATH)
            case "ItemIsMenu":
                # true = icon chỉ là cái mở menu, không có hành động riêng.
                return GLib.Variant("b", True)
            case "IconName":
                return GLib.Variant("s", s.icon_name)
            case "OverlayIconName":
                return GLib.Variant("s", s.overlay_icon)
            case "AttentionIconName":
                return GLib.Variant("s", s.icon_name if s.attention else "")
            case "IconAccessibleDesc" | "AttentionAccessibleDesc":
                return GLib.Variant("s", s.accessible_desc)
            case "AttentionMovieName":
                return GLib.Variant("s", "")
            case "IconPixmap" | "OverlayIconPixmap" | "AttentionIconPixmap":
                # Dùng icon theo TÊN (symbolic) để tự đổi màu theo theme sáng/tối.
                return empty_pixmap
            case "ToolTip":
                # GNOME bỏ qua hoàn toàn — xem docstring của tray/status.py.
                # Vẫn export vì KDE Plasma và waybar có dùng.
                return GLib.Variant(
                    "(sa(iiay)ss)", ("", [], s.title, "\n".join(s.status_lines))
                )
            case "XAyatanaLabel":
                return GLib.Variant("s", s.label)
            case "XAyatanaLabelGuide":
                return GLib.Variant("s", "")
        return None

    def _sni_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        match method:
            case "SecondaryActivate":
                if self._on_secondary_activate:
                    self._on_secondary_activate()
            case "Scroll":
                if self._on_scroll:
                    delta, orientation = params.unpack()
                    self._on_scroll(delta, orientation)
            case "ContextMenu" | "ProvideXdgActivationToken":
                pass          # menu do host tự mở; token không cần dùng
        invocation.return_value(None)

    # ── com.canonical.dbusmenu ──────────────────────────────────────────────

    def _menu_get_property(self, _conn, _sender, _path, _iface, name):
        match name:
            case "Version":
                return GLib.Variant("u", 3)
            case "TextDirection":
                return GLib.Variant("s", "ltr")
            case "Status":
                return GLib.Variant("s", "normal")
            case "IconThemePath":
                return GLib.Variant("as", [])
        return None

    def _menu_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        match method:
            case "GetLayout":
                parent_id, depth, _props = params.unpack()
                node = self._menu.layout(parent_id, depth)
                if node is None:
                    invocation.return_error_literal(
                        Gio.dbus_error_quark(),
                        Gio.DBusError.INVALID_ARGS,
                        f"Không có menu item id {parent_id}",
                    )
                    return
                invocation.return_value(
                    GLib.Variant.new_tuple(
                        GLib.Variant("u", self._menu_revision), _layout_to_variant(node)
                    )
                )

            case "GetGroupProperties":
                ids, _names = params.unpack()
                out = []
                for item_id in ids:
                    node = self._menu.get(item_id)
                    if node is not None:
                        out.append((
                            item_id,
                            {k: _to_variant(v) for k, v in node.dbus_properties().items()},
                        ))
                invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))

            case "GetProperty":
                item_id, prop = params.unpack()
                node = self._menu.get(item_id)
                value = node.dbus_properties().get(prop) if node else None
                # Out-arg kiểu `v`, nên tuple trả về phải là "(v)". Dùng
                # new_tuple(variant) sẽ ra "(s)" và GDBus im lặng bỏ reply —
                # phía client chỉ thấy timeout, rất khó lần ra.
                invocation.return_value(
                    GLib.Variant("(v)", (_to_variant(value if value is not None else ""),))
                )

            case "Event":
                item_id, event_id, _data, _timestamp = params.unpack()
                self._handle_event(item_id, event_id)
                invocation.return_value(None)

            case "EventGroup":
                errors = []
                for item_id, event_id, _data, _ts in params.unpack()[0]:
                    if not self._handle_event(item_id, event_id):
                        errors.append(item_id)
                invocation.return_value(GLib.Variant("(ai)", (errors,)))

            case "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (self._about_to_show(),)))

            case "AboutToShowGroup":
                updated = [ROOT_ID] if self._about_to_show() else []
                invocation.return_value(GLib.Variant("(aiai)", (updated, [])))

            case _:
                invocation.return_value(None)

    def _handle_event(self, item_id: int, event_id: str) -> bool:
        if event_id != "clicked":
            return True          # hovered/opened/closed: không cần làm gì
        try:
            return self._menu.activate(item_id)
        except Exception:  # noqa: BLE001 — action lỗi không được kéo sập tray
            log.exception("Action của menu item %s ném exception", item_id)
            return False

    def _about_to_show(self) -> bool:
        """Hook dựng lại menu ngay trước khi host hiện nó.

        Nhờ đó menu luôn tươi mà không phải rebuild liên tục theo mọi signal.
        """
        if self._on_about_to_show is None:
            return False
        before = self._menu_revision
        self._on_about_to_show()
        return self._menu_revision != before
