"""Trang chi tiết một connection — xem trạng thái và SỬA IPv4/route (P4).

Trang giữ một bản **nháp** của `Ipv4Config`. Mọi thao tác sửa chỉ chạm vào bản
nháp; chỉ khi bấm Lưu mới ghi xuống NetworkManager. Nhờ vậy người dùng sửa nhiều
thứ rồi áp dụng một lần, và Huỷ luôn quay về được trạng thái cũ.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..domain.models import Ipv4Address, Ipv4Config, Ipv4Method, Ipv4Route
from ..domain.validators import (
    format_route_line,
    parse_ipv4,
    parse_route_block,
    validate_route_table,
)
from . import view_models as vm
from .route_editor import RouteEditor

_METHODS = [
    (Ipv4Method.AUTO, "Tự động (DHCP)"),
    (Ipv4Method.MANUAL, "Thủ công"),
    (Ipv4Method.LINK_LOCAL, "Link-Local"),
    (Ipv4Method.DISABLED, "Tắt IPv4"),
]


class ConnectionDetailPage(Adw.NavigationPage):
    def __init__(self, window, controller, uuid: str) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._uuid = uuid

        self._draft: Ipv4Config | None = None
        self._dirty = False
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        self._save_button = Gtk.Button(
            label="Lưu", css_classes=["suggested-action"], visible=False
        )
        self._save_button.connect("clicked", lambda _b: self._save(apply_now=False))
        self._apply_button = Gtk.Button(
            label="Lưu & Áp dụng", css_classes=["suggested-action"], visible=False
        )
        self._apply_button.connect("clicked", lambda _b: self._save(apply_now=True))
        self._revert_button = Gtk.Button(label="Huỷ", visible=False)
        self._revert_button.connect("clicked", lambda _b: self.reload())
        header.pack_end(self._apply_button)
        header.pack_end(self._save_button)
        header.pack_start(self._revert_button)

        self._banner = Adw.Banner(title="Có thay đổi chưa lưu", revealed=False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._banner)
        box.append(Gtk.ScrolledWindow(child=self._content, vexpand=True))
        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        self.reload()

    # ── nạp lại ─────────────────────────────────────────────────────────────

    def reload(self) -> None:
        """Bỏ bản nháp và dựng lại từ trạng thái thật."""
        self._draft = None
        self._dirty = False
        self.rebuild()

    def rebuild(self) -> None:
        snapshot = self._controller.snapshot()
        detail = vm.connection_detail(snapshot, self._uuid)
        if detail is None:
            self._window.pop_to_root()
            return

        conn = snapshot.connection_by_uuid(self._uuid)
        if self._draft is None:
            self._draft = conn.ipv4.copy()

        self.set_title(detail.title)
        self._device = snapshot.device_by_interface(
            conn.device_interface or conn.interface_name or ""
        )

        child = self._content.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._content.remove(child)
            child = nxt

        page = Adw.PreferencesPage()
        page.add(self._status_group(detail))
        page.add(self._ipv4_group())
        page.add(self._flags_group())
        page.add(self._routes_group(detail))
        page.add(self._actions_group(detail))
        self._content.append(page)
        self._sync_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._sync_dirty()

    def _sync_dirty(self) -> None:
        self._banner.set_revealed(self._dirty)
        for button in (self._save_button, self._apply_button, self._revert_button):
            button.set_visible(self._dirty)

    # ── các nhóm ────────────────────────────────────────────────────────────

    def _status_group(self, detail) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Trạng thái")
        for row_vm in detail.status:
            row = Adw.ActionRow(title=row_vm.label, subtitle=row_vm.value)
            row.set_subtitle_selectable(True)
            if row_vm.runtime:
                badge = Gtk.Label(
                    label="đang chạy", css_classes=["dim-label", "caption"],
                    valign=Gtk.Align.CENTER,
                )
                row.add_suffix(badge)
            group.add(row)
        return group

    def _ipv4_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="IPv4")

        combo = Adw.ComboRow(
            title="Chế độ",
            model=Gtk.StringList.new([label for _m, label in _METHODS]),
            selected=next(
                (i for i, (m, _l) in enumerate(_METHODS) if m is self._draft.method), 0
            ),
        )
        combo.connect("notify::selected", self._on_method_changed)
        group.add(combo)

        manual = self._draft.method is Ipv4Method.MANUAL

        self._address_row = Adw.EntryRow(
            title="Địa chỉ (vd 10.0.5.20/24, cách nhau bằng dấu phẩy)",
            text=", ".join(str(a) for a in self._draft.addresses),
            visible=manual,
        )
        self._address_row.connect("changed", lambda _w: self._on_addresses_changed())
        group.add(self._address_row)

        self._gateway_row = Adw.EntryRow(
            title="Gateway", text=self._draft.gateway or "", visible=manual
        )
        self._gateway_row.connect("changed", lambda _w: self._on_gateway_changed())
        group.add(self._gateway_row)

        self._dns_row = Adw.EntryRow(
            title="DNS (cách nhau bằng dấu phẩy)", text=", ".join(self._draft.dns)
        )
        self._dns_row.connect("changed", lambda _w: self._on_dns_changed())
        group.add(self._dns_row)
        return group

    def _flags_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Tuỳ chọn",
            description=(
                "Route tĩnh bên dưới được giữ nguyên ở cả chế độ Tự động lẫn Thủ công."
            ),
        )
        for attr, title, subtitle in (
            ("ignore_auto_dns", "Bỏ qua DNS tự động",
             "Không dùng DNS do DHCP cấp"),
            ("ignore_auto_routes", "Bỏ qua route tự động",
             "Không nhận route do DHCP đẩy về"),
            ("never_default", "Không dùng làm default route",
             "Kết nối này không nhận traffic mặc định"),
        ):
            row = Adw.SwitchRow(
                title=title, subtitle=subtitle, active=getattr(self._draft, attr)
            )
            row.connect("notify::active", self._make_flag_handler(attr))
            group.add(row)
        return group

    def _make_flag_handler(self, attr: str):
        def handler(row, _param) -> None:
            if getattr(self._draft, attr) != row.get_active():
                setattr(self._draft, attr, row.get_active())
                self._mark_dirty()

        return handler

    def _routes_group(self, detail) -> Adw.PreferencesGroup:
        problems = validate_route_table(
            self._draft.routes, local_subnets=self._local_subnets()
        )
        group = Adw.PreferencesGroup(
            title="Route IPv4",
            description="🔵 tĩnh (sửa được) · ⚪ do DHCP cấp (chỉ đọc)",
        )

        buttons = Gtk.Box(spacing=6)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Thêm route",
                         css_classes=["flat"])
        add.connect("clicked", lambda _b: self._edit_route(None))
        paste = Gtk.Button(icon_name="edit-paste-symbolic",
                           tooltip_text="Dán nhiều route", css_classes=["flat"])
        paste.connect("clicked", lambda _b: self._paste_routes())
        copy = Gtk.Button(icon_name="edit-copy-symbolic",
                          tooltip_text="Chép route tĩnh", css_classes=["flat"])
        copy.connect("clicked", lambda _b: self._copy_routes())
        for b in (copy, paste, add):
            buttons.append(b)
        group.set_header_suffix(buttons)

        for index, route in enumerate(self._draft.routes):
            group.add(self._static_route_row(index, route, problems.get(index)))

        for row_vm in detail.routes:
            if row_vm.editable:
                continue                    # route tĩnh đã hiện ở trên
            row = Adw.ActionRow(title=row_vm.text, subtitle=f"⚪ {row_vm.source_label}")
            row.set_title_selectable(True)
            group.add(row)

        if not self._draft.routes and not detail.routes:
            group.add(Adw.ActionRow(title="Chưa có route nào", sensitive=False))
        return group

    def _static_route_row(self, index: int, route: Ipv4Route, problem) -> Adw.ActionRow:
        subtitle = "🔵 Tĩnh"
        if problem is not None and problem.issues:
            issue = (problem.errors or problem.warnings)[0]
            subtitle = ("⛔ " if problem.errors else "⚠ ") + issue.message
        row = Adw.ActionRow(title=str(route), subtitle=subtitle)
        row.set_title_selectable(True)
        row.set_subtitle_lines(2)

        toggle = Gtk.Switch(
            active=route.enabled, valign=Gtk.Align.CENTER,
            tooltip_text="Tắt tạm route này mà không xoá",
        )
        toggle.connect("notify::active", self._make_route_toggle(index))
        row.add_suffix(toggle)

        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                          css_classes=["flat"], tooltip_text="Sửa")
        edit.connect("clicked", lambda _b, i=index: self._edit_route(i))
        row.add_suffix(edit)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text="Xoá")
        remove.connect("clicked", lambda _b, i=index: self._delete_route(i))
        row.add_suffix(remove)
        return row

    def _make_route_toggle(self, index: int):
        from dataclasses import replace

        def handler(switch, _param) -> None:
            route = self._draft.routes[index]
            if route.enabled != switch.get_active():
                self._draft.routes[index] = replace(route, enabled=switch.get_active())
                self._mark_dirty()

        return handler

    def _actions_group(self, detail) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Kết nối")

        act = Adw.ActionRow(title=detail.action_label)
        button = Gtk.Button(
            label=detail.action_label, valign=Gtk.Align.CENTER,
            sensitive=detail.can_act,
            css_classes=["destructive-action"] if detail.is_active else [],
        )
        button.connect(
            "clicked",
            lambda _b: (
                self._controller.deactivate_connection(self._uuid)
                if detail.is_active
                else self._controller.activate_connection(self._uuid)
            ),
        )
        act.add_suffix(button)
        group.add(act)

        delete = Adw.ActionRow(
            title="Quên cấu hình này",
            subtitle=(
                "Ngắt kết nối trước khi xoá" if detail.is_active
                else "Xoá vĩnh viễn khỏi NetworkManager"
            ),
        )
        del_button = Gtk.Button(
            label="Xoá", valign=Gtk.Align.CENTER,
            sensitive=detail.can_delete, css_classes=["destructive-action"],
        )
        del_button.connect("clicked", lambda _b: self._window.confirm_delete(detail))
        delete.add_suffix(del_button)
        group.add(delete)
        return group

    # ── sửa IPv4 ────────────────────────────────────────────────────────────

    def _on_method_changed(self, combo, _param) -> None:
        method = _METHODS[combo.get_selected()][0]
        if method is self._draft.method:
            return

        # FR-A2 — chuyển sang Thủ công thì điền sẵn giá trị đang nhận từ DHCP.
        # Không có bước này người dùng phải tự gõ lại IP/gateway/DNS đang chạy.
        if method is Ipv4Method.MANUAL and not self._draft.addresses and self._device:
            self._draft.addresses = list(self._device.ip4_addresses)
            self._draft.gateway = self._device.ip4_gateway
            if not self._draft.dns:
                self._draft.dns = list(self._device.ip4_dns)

        self._draft.method = method
        self._mark_dirty()
        self.rebuild()

    def _on_addresses_changed(self) -> None:
        addresses = []
        for chunk in self._address_row.get_text().split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            host, _, prefix = chunk.partition("/")
            if parse_ipv4(host) and prefix.isdigit() and 0 <= int(prefix) <= 32:
                addresses.append(Ipv4Address(host, int(prefix)))
        if addresses != self._draft.addresses:
            self._draft.addresses = addresses
            self._mark_dirty()

    def _on_gateway_changed(self) -> None:
        value = self._gateway_row.get_text().strip() or None
        if value != self._draft.gateway:
            self._draft.gateway = value
            self._mark_dirty()

    def _on_dns_changed(self) -> None:
        servers = [s.strip() for s in self._dns_row.get_text().split(",") if s.strip()]
        if servers != self._draft.dns:
            self._draft.dns = servers
            self._mark_dirty()

    # ── sửa route ───────────────────────────────────────────────────────────

    def _local_subnets(self) -> list[Ipv4Address]:
        """Subnet của interface — để cảnh báo gateway không reachable."""
        if self._draft.addresses:
            return list(self._draft.addresses)
        return list(self._device.ip4_addresses) if self._device else []

    def _edit_route(self, index: int | None) -> None:
        route = self._draft.routes[index] if index is not None else None
        others = [r for i, r in enumerate(self._draft.routes) if i != index]

        def on_save(new_route: Ipv4Route) -> None:
            if index is None:
                self._draft.routes.append(new_route)
            else:
                self._draft.routes[index] = new_route
            self._mark_dirty()
            self.rebuild()

        RouteEditor(
            route,
            existing=others,
            local_subnets=self._local_subnets(),
            on_save=on_save,
        ).present(self._window)

    def _delete_route(self, index: int) -> None:
        del self._draft.routes[index]
        self._mark_dirty()
        self.rebuild()

    def _copy_routes(self) -> None:
        if not self._draft.routes:
            self._window.toast("Không có route tĩnh nào để chép")
            return
        text = "\n".join(format_route_line(r) for r in self._draft.routes)
        self._window.get_clipboard().set(text)
        self._window.toast(f"Đã chép {len(self._draft.routes)} route")

    def _paste_routes(self) -> None:
        """Dán hàng loạt (FR-R9) — nguồn thường là tài liệu mạng nội bộ."""
        dialog = Adw.AlertDialog(
            heading="Dán nhiều route",
            body="Mỗi dòng một route: 10.20.0.0/16 via 192.168.1.1 metric 100",
        )
        view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True,
            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8,
        )
        dialog.set_extra_child(
            Gtk.Frame(child=view, height_request=160, width_request=420)
        )
        dialog.add_response("cancel", "Huỷ")
        dialog.add_response("add", "Thêm")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_dialog, response) -> None:
            if response != "add":
                return
            buf = view.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            routes, errors = parse_route_block(text)
            self._draft.routes.extend(routes)
            if routes:
                self._mark_dirty()
            message = f"Đã thêm {len(routes)} route"
            if errors:
                message += f" · {len(errors)} dòng lỗi: {errors[0]}"
            self._window.toast(message)
            self.rebuild()

        dialog.connect("response", on_response)
        dialog.present(self._window)

    # ── lưu ─────────────────────────────────────────────────────────────────

    def _save(self, *, apply_now: bool) -> None:
        problems = validate_route_table(
            self._draft.routes, local_subnets=self._local_subnets()
        )
        blocking = [p for p in problems.values() if p.errors]
        if blocking:
            self._window.toast(f"Route không hợp lệ: {blocking[0].errors[0].message}")
            return

        self._controller.save_ipv4(self._uuid, self._draft, apply_now=apply_now)
        self._dirty = False
        self._sync_dirty()
