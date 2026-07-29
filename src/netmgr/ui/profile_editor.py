"""Hộp thoại thêm/sửa một Bộ cấu hình (F6).

Cố ý giữ đơn giản: mỗi thiết bị mạng là MỘT công tắc bật/tắt, kèm một dòng cho
biết nó đang ra sao. Không có ô chọn connection — bật một thiết bị nghĩa là
"cho nó lên mạng", NetworkManager tự chọn cấu hình phù hợp đúng như khi cắm cáp.

Route nằm ở mục riêng bên dưới, mỗi route chọn đi qua thiết bị nào.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..domain.models import Binding, BindingAction, Ipv4Route, Profile
from . import view_models as vm
from .route_editor import RouteEditor

_ICONS = ["🌐", "🏢", "🏠", "🏨", "✈️", "🔒", "🧪", "📡"]


class ProfileEditor(Adw.Dialog):
    def __init__(self, window, controller, profile: Profile | None) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._service = controller.profiles

        self._is_new = profile is None
        self._profile = profile or Profile(id=_new_id(), name="")
        self._snapshot = controller.snapshot()

        # Route giữ dạng phẳng (route, interface) — người dùng nhìn thấy một
        # danh sách route duy nhất, gom lại theo thiết bị khi lưu.
        self._routes: list[tuple[Ipv4Route, str]] = [
            (route, binding.device_match)
            for binding in self._profile.bindings
            for route in binding.routes
        ]
        self._switches: dict[str, Adw.SwitchRow] = {}
        self.set_title("Bộ cấu hình mới" if self._is_new else "Sửa Bộ cấu hình")
        self.set_content_width(560)
        self.set_content_height(680)
        self._build()

    # ── dựng ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self._page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Huỷ")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Lưu", css_classes=["suggested-action"])
        save.connect("clicked", lambda _b: self._on_save())
        header.pack_end(save)

        self._error = Gtk.Label(css_classes=["error", "caption"], visible=False, wrap=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._error)
        box.append(Gtk.ScrolledWindow(child=self._page_box, vexpand=True))

        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)
        self._rebuild()

    def _rebuild(self) -> None:
        """Dựng lại nội dung. Giá trị đang gõ được giữ qua `_remember()`."""
        child = self._page_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._page_box.remove(child)
            child = nxt

        page = Adw.PreferencesPage()
        page.add(self._general_group())
        page.add(self._devices_group())
        page.add(self._routes_group())
        page.add(self._proxy_group())
        self._page_box.append(page)

    def _general_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Chung")
        self._name = Adw.EntryRow(title="Tên", text=self._profile.name)
        group.add(self._name)

        self._icon = Adw.ComboRow(
            title="Biểu tượng",
            model=Gtk.StringList.new(_ICONS),
            selected=_ICONS.index(self._profile.icon) if self._profile.icon in _ICONS else 0,
        )
        group.add(self._icon)

        self._description = Adw.EntryRow(title="Mô tả", text=self._profile.description)
        group.add(self._description)
        return group

    def _devices_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Mạng",
            description=(
                "Bật thiết bị nào thì áp dụng Bộ cấu hình sẽ cho nó lên mạng, "
                "tắt thì ngắt kết nối. Thiết bị vắng mặt được bỏ qua."
            ),
        )
        self._switches = {}

        for device in self._snapshot.devices:
            existing = self._profile.binding_for(device.interface)
            row = Adw.SwitchRow(
                title=f"{vm.device_label(device)}  ·  {device.interface}",
                subtitle=vm.device_status(device, self._snapshot),
                active=existing is not None and existing.action is BindingAction.ACTIVATE,
            )
            row.set_subtitle_lines(2)
            group.add(row)
            self._switches[device.interface] = row

        if not self._snapshot.devices:
            group.add(Adw.ActionRow(title="Không tìm thấy thiết bị mạng", sensitive=False))
        return group

    def _routes_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Route riêng",
            description=(
                "Mạng có route ở đây sẽ tự tắt Automatic để chỉ dùng route này; "
                "mạng không có thì về Automatic. Chỉ áp khi Bộ cấu hình đang "
                "bật — tắt đi là máy trở về cấu hình gốc."
            ),
        )
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                         tooltip_text="Thêm route")
        add.connect("clicked", lambda _b: self._edit_route(None))
        group.set_header_suffix(add)

        self._route_switches = []
        for index, (route, iface) in enumerate(self._routes):
            device = self._snapshot.device_by_interface(iface)
            via = vm.device_label(device) if device else iface
            row = Adw.ActionRow(
                title=str(route),
                subtitle=f"qua {via}  ·  {iface}"
                + ("" if route.enabled else "  ·  đang tắt"),
                css_classes=[] if route.enabled else ["dim-label"],
            )
            row.set_title_selectable(True)

            # Tắt = tạm không áp, KHÔNG phải xoá. Vẫn nằm trong Bộ cấu hình và
            # bật lại được, nên không phải gõ lại route.
            switch = Gtk.Switch(active=route.enabled, valign=Gtk.Align.CENTER,
                                tooltip_text="Bật/tắt route này")
            switch.connect("state-set", self._on_route_toggled, index)
            row.add_prefix(switch)
            self._route_switches.append(switch)

            edit = Gtk.Button(icon_name="document-edit-symbolic", css_classes=["flat"],
                              valign=Gtk.Align.CENTER, tooltip_text="Sửa")
            edit.connect("clicked", lambda _b, i=index: self._edit_route(i))
            row.add_suffix(edit)

            remove = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"],
                                valign=Gtk.Align.CENTER, tooltip_text="Xoá")
            remove.connect("clicked", lambda _b, i=index: self._delete_route(i))
            row.add_suffix(remove)
            group.add(row)

        if not self._routes:
            group.add(Adw.ActionRow(title="Chưa có route riêng nào", sensitive=False))

        for iface in dict.fromkeys(i for _r, i in self._routes):
            warning = vm.lost_default_route_warning(
                iface, [r for r, i in self._routes if i == iface], self._snapshot
            )
            if warning:
                row = Adw.ActionRow(title=warning, css_classes=["warning"])
                row.set_title_lines(4)
                group.add(row)
        return group

    def _proxy_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Proxy")
        self._proxy_choices: list[tuple[str | None, str]] = [
            (None, "Không đụng tới"),
            ("", "Tắt proxy"),
        ]
        self._proxy_choices += [(c.id, c.name) for c in self._controller.proxy.configs]
        self._proxy = Adw.ComboRow(
            title="Khi áp dụng Bộ cấu hình này",
            model=Gtk.StringList.new([label for _v, label in self._proxy_choices]),
            selected=next(
                (i for i, (v, _l) in enumerate(self._proxy_choices)
                 if v == self._profile.proxy_id),
                0,
            ),
        )
        group.add(self._proxy)
        return group

    # ── route ───────────────────────────────────────────────────────────────

    def _edit_route(self, index: int | None) -> None:
        if not self._snapshot.devices:
            self._window.toast("Không có thiết bị mạng nào để gắn route")
            return

        current_route, current_iface = (
            self._routes[index] if index is not None else (None, None)
        )
        others = [r for i, (r, _d) in enumerate(self._routes) if i != index]
        choices = [
            (d.interface, f"{vm.device_label(d)}  ·  {d.interface}")
            for d in self._snapshot.devices
        ]

        def on_save(route: Ipv4Route, iface: str) -> None:
            if index is None:
                self._routes.append((route, iface))
            else:
                self._routes[index] = (route, iface)
            self._remember()
            self._rebuild()

        RouteEditor(
            current_route,
            existing=others,
            local_subnets=self._subnets(current_iface),
            on_save=on_save,
            devices=choices,
            current_device=current_iface,
        ).present(self._window)

    def _on_route_toggled(self, _switch, active: bool, index: int) -> bool:
        from dataclasses import replace

        route, iface = self._routes[index]
        self._routes[index] = (replace(route, enabled=active), iface)
        self._remember()
        self._rebuild()
        return False        # để Gtk.Switch tự cập nhật trạng thái hiển thị

    def _delete_route(self, index: int) -> None:
        del self._routes[index]
        self._remember()
        self._rebuild()

    def _subnets(self, iface: str | None):
        device = self._snapshot.device_by_interface(iface) if iface else None
        return list(device.ip4_addresses) if device else []

    # ── lưu ─────────────────────────────────────────────────────────────────

    def _remember(self) -> None:
        """Giữ lại giá trị đang gõ trước khi dựng lại giao diện."""
        self._profile.name = self._name.get_text().strip()
        self._profile.icon = _ICONS[self._icon.get_selected()]
        self._profile.description = self._description.get_text().strip()
        self._profile.proxy_id = self._proxy_choices[self._proxy.get_selected()][0]
        self._profile.bindings = self._collect_bindings()

    def _collect_bindings(self) -> list[Binding]:
        routes_by_iface: dict[str, list[Ipv4Route]] = {}
        for route, iface in self._routes:
            routes_by_iface.setdefault(iface, []).append(route)

        bindings: list[Binding] = []
        for iface, switch in self._switches.items():
            on = switch.get_active()
            routes = routes_by_iface.pop(iface, [])
            # Thiết bị chưa từng cấu hình, đang tắt, không có route → bỏ qua cho
            # file gọn. Nhưng nếu đã từng có binding thì phải giữ, vì tắt là một
            # lựa chọn có ý nghĩa (ngắt kết nối) chứ không phải "chưa đụng tới".
            if not on and not routes and self._profile.binding_for(iface) is None:
                continue
            bindings.append(
                Binding(
                    device_match=iface,
                    action=BindingAction.ACTIVATE if on else BindingAction.DISCONNECT,
                    connection_uuid=None,   # để NetworkManager tự chọn
                    routes=routes,
                )
            )

        # Route trỏ tới thiết bị không còn trong danh sách (tạm rút) vẫn giữ lại,
        # nếu không người dùng sẽ mất cấu hình chỉ vì rút cáp một lúc.
        for iface, routes in routes_by_iface.items():
            bindings.append(
                Binding(iface, BindingAction.LEAVE_ALONE, None, routes=routes)
            )
        return bindings

    def _on_save(self) -> None:
        self._remember()
        result = self._service.save(self._profile)
        if not result.ok:
            self._error.set_text(result.message)
            self._error.set_visible(True)
            return
        self.close()

        # Đang dùng chính Bộ cấu hình vừa sửa thì áp lại ngay. Nếu không, người
        # dùng thấy thay đổi đã lưu nhưng mạng vẫn chạy theo cấu hình cũ.
        active = self._service.active
        if active is not None and active.id == self._profile.id:
            self._window.toast(f"Đang áp lại '{self._profile.name}'…")
            self._controller.apply_profile(self._profile.id)
        else:
            self._controller.refresh()


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())
