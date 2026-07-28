"""Trang chi tiết một connection — CHỈ ĐỌC.

App cố tình không sửa cấu hình mạng của máy. Lý do là một sự cố có thật:
`update2(TO_DISK)` khiến NetworkManager ghi connection ra
`/etc/netplan/90-NM-<uuid>.yaml`, và vòng chuyển đổi đó làm mất
`connection.interface-name`. Profile của cổng LAN biến thành profile ethernet
chung rồi tự bám sang cổng USB vừa cắm.

Muốn đổi route thì đổi trong **Bộ cấu hình** — route ở đó chỉ được áp ở runtime
nên tắt Bộ cấu hình là máy trở về đúng cấu hình gốc.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import view_models as vm


class ConnectionDetailPage(Adw.NavigationPage):
    def __init__(self, window, controller, uuid: str) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._uuid = uuid

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar = Adw.ToolbarView(
            content=Gtk.ScrolledWindow(child=self._content, vexpand=True)
        )
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        self.rebuild()

    def reload(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        snapshot = self._controller.snapshot()
        detail = vm.connection_detail(snapshot, self._uuid)
        if detail is None:
            self._window.pop_to_root()
            return

        self.set_title(detail.title)

        child = self._content.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._content.remove(child)
            child = nxt

        page = Adw.PreferencesPage()
        page.add(self._rows_group("Trạng thái", detail.status))
        page.add(self._rows_group("IPv4 đã lưu", detail.ipv4))
        page.add(self._routes_group(detail))
        page.add(self._actions_group(detail))
        self._content.append(page)

    # ── các nhóm ────────────────────────────────────────────────────────────

    def _rows_group(self, title: str, rows) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title)
        for row_vm in rows:
            row = Adw.ActionRow(title=row_vm.label, subtitle=row_vm.value)
            row.set_subtitle_selectable(True)
            if row_vm.runtime:
                row.add_suffix(
                    Gtk.Label(
                        label="đang chạy",
                        css_classes=["dim-label", "caption"],
                        valign=Gtk.Align.CENTER,
                    )
                )
            group.add(row)
        if not rows:
            group.add(Adw.ActionRow(title="Không có dữ liệu", sensitive=False))
        return group

    def _routes_group(self, detail) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Route đang áp dụng",
            description=(
                "Đây là cấu hình của máy, app không sửa. Muốn thêm route riêng "
                "cho một bối cảnh thì thêm vào Bộ cấu hình."
            ),
        )
        goto = Gtk.Button(label="Mở Bộ cấu hình", valign=Gtk.Align.CENTER)
        goto.connect("clicked", lambda _b: self._window.select_page("profiles"))
        group.set_header_suffix(goto)

        for route in detail.routes:
            row = Adw.ActionRow(title=route.text, subtitle=route.source_label)
            row.set_title_selectable(True)
            group.add(row)
        if not detail.routes:
            group.add(Adw.ActionRow(title="Không có route nào", sensitive=False))
        return group

    def _actions_group(self, detail) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Kết nối")
        row = Adw.ActionRow(title=detail.action_label)

        button = Gtk.Button(
            label=detail.action_label,
            valign=Gtk.Align.CENTER,
            css_classes=["destructive-action"] if detail.is_active else [],
        )
        # Nút LUÔN bấm được. Khi không dùng được thì bấm vào phải nói rõ lý do —
        # nút xám câm lặng là kiểu UI khó chịu nhất.
        button.connect("clicked", lambda _b: self._on_action(detail))
        row.add_suffix(button)
        group.add(row)
        return group

    def _on_action(self, detail) -> None:
        if not detail.can_act:
            self._window.toast(detail.blocked_reason or "Thao tác này hiện không dùng được")
            return
        if detail.is_active:
            self._controller.deactivate_connection(detail.uuid)
        else:
            self._controller.activate_connection(detail.uuid)
