"""Cửa sổ cấu hình — GTK4 + libadwaita.

Module này CHỈ dựng widget. Mọi quyết định "hiện chữ gì, bật/tắt nút nào" nằm ở
`view_models`, nơi test được. Nếu thấy mình sắp viết một câu `if` về nghiệp vụ ở
đây thì nó thuộc về `view_models`.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..domain.models import ConnType
from . import view_models as vm
from .proxy_editor import ProxyEditor

log = logging.getLogger(__name__)


class MainWindow(Adw.ApplicationWindow):
    """Cửa sổ chính. `controller` là NetmgrApp — cung cấp dữ liệu và hành động."""

    PAGES = (
        ("profiles", "Bộ cấu hình", "view-list-bullet-symbolic"),
        ("connections", "Kết nối", "network-workgroup-symbolic"),
        ("routing", "Định tuyến", "network-transmit-receive-symbolic"),
        ("proxy", "Proxy", "preferences-system-network-symbolic"),
        ("diagnostics", "Chẩn đoán", "dialog-information-symbolic"),
        ("settings", "Tuỳ chọn", "preferences-system-symbolic"),
    )

    def __init__(self, application, controller) -> None:
        super().__init__(application=application, title="Quản lý mạng")
        self._controller = controller
        self.set_default_size(940, 660)

        self._toasts = Adw.ToastOverlay()
        self._split = Adw.NavigationSplitView()
        self._toasts.set_child(self._split)
        self.set_content(self._toasts)

        # Khởi tạo state TRƯỚC khi dựng sidebar: chọn hàng đầu tiên trong
        # `_build_sidebar` sẽ kích hoạt ngay `_on_sidebar_selected` → `refresh()`.
        self._pages: dict[str, Gtk.Widget] = {}
        self._current = "profiles"
        #: Báo cáo tiến trình khi đang áp dụng Bộ cấu hình; None = không chạy.
        self._apply_report = None
        #: interface -> cấu hình IPv4 đang chạy, đọc bất đồng bộ. Cần vì
        #: `snapshot()` chỉ thấy cấu hình đã lưu trên đĩa.
        self._runtime_ipv4: dict[str, object] = {}
        self._lookup_query = ""
        self._lookup_result = None
        self._content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE
        )

        # NavigationSplitView chỉ chia đôi màn hình, nó KHÔNG có push/pop. Muốn
        # đi sâu vào trang chi tiết thì cần một NavigationView lồng bên trong.
        self._nav = Adw.NavigationView()
        self._root_page = Adw.NavigationPage(child=self._wrap_content(), title="Bộ cấu hình")
        self._nav.add(self._root_page)
        self._split.set_content(Adw.NavigationPage(child=self._nav, title="Chi tiết"))

        self._build_sidebar()
        self.refresh()

    # ── khung ───────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> None:
        self._sidebar_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.connect("row-selected", self._on_sidebar_selected)

        for key, title, icon in self.PAGES:
            row = Adw.ActionRow(title=title)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            row.page_key = key
            self._sidebar_list.append(row)

        scroller = Gtk.ScrolledWindow(child=self._sidebar_list, vexpand=True)
        toolbar = Adw.ToolbarView(content=scroller)
        toolbar.add_top_bar(Adw.HeaderBar())
        self._split.set_sidebar(
            Adw.NavigationPage(child=toolbar, title="Quản lý mạng")
        )
        self._sidebar_list.select_row(self._sidebar_list.get_row_at_index(0))

    def _wrap_content(self) -> Gtk.Widget:
        # Không có nút "làm mới": app đã nghe signal của NetworkManager nên mọi
        # thay đổi tự hiện ra. Một nút không rõ để làm gì chỉ gây phân vân.
        header = Adw.HeaderBar()
        toolbar = Adw.ToolbarView(content=self._content_stack)
        toolbar.add_top_bar(header)
        return toolbar

    def _on_sidebar_selected(self, _list, row) -> None:
        if row is None:
            return
        self._current = row.page_key
        # Trang chi tiết nằm ĐÈ LÊN vùng nội dung. Không đóng nó thì đổi mục ở
        # sidebar chỉ đổi thứ nằm bên dưới, người dùng vẫn thấy y nguyên trang
        # chi tiết và tưởng nút không hoạt động.
        self._nav.pop_to_page(self._root_page)
        self.refresh()

    def toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def pop_to_root(self) -> None:
        """Quay về trang gốc — gọi khi thứ đang xem không còn tồn tại."""
        self._nav.pop_to_page(self._root_page)
        self.refresh()

    # ── vẽ lại ──────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Dựng lại trang đang hiện từ trạng thái mới nhất.

        Dựng lại toàn bộ thay vì cập nhật từng widget: trang lớn nhất chỉ vài
        chục hàng nên chi phí không đáng kể, đổi lại không bao giờ có widget
        lệch pha với dữ liệu.
        """
        # Trang chi tiết đang mở tự cập nhật phần trạng thái nhưng GIỮ bản nháp —
        # mạng đổi giữa chừng không được xoá thứ người dùng đang gõ dở.
        top = self._nav.get_visible_page()
        if top is not None and hasattr(top, "rebuild"):
            top.rebuild()

        snapshot = self._controller.snapshot()
        builder = {
            "profiles": self._build_profiles_page,
            "connections": self._build_connections_page,
            "routing": self._build_routing_page,
            "proxy": self._build_proxy_page,
            "diagnostics": self._build_diagnostics_page,
            "settings": self._build_settings_page,
        }[self._current]

        # Tiêu đề phải theo trang đang xem, nếu không mọi trang đều mang tên
        # trang đầu tiên.
        self._root_page.set_title(
            next(t for k, t, _i in self.PAGES if k == self._current)
        )

        page = builder(snapshot)
        old = self._pages.get(self._current)
        if old is not None:
            self._content_stack.remove(old)
        self._content_stack.add_named(page, self._current)
        self._pages[self._current] = page
        self._content_stack.set_visible_child(page)

    # ── trang Kết nối ───────────────────────────────────────────────────────

    def _build_connections_page(self, snapshot) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        if snapshot.has_wifi_hardware:
            page.add(self._wifi_group(snapshot))
        if snapshot.ethernet_devices:
            page.add(self._wired_group(snapshot))
        if not snapshot.devices:
            page.add(self._placeholder("Không tìm thấy thiết bị mạng nào"))
        return self._scrolled(page)

    def _wifi_group(self, snapshot) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Wi-Fi")

        radio = Adw.SwitchRow(
            title="Bật Wi-Fi",
            active=snapshot.wifi_enabled,
            sensitive=(
                snapshot.wifi_hardware_enabled
                and snapshot.permissions.can_toggle_wifi
            ),
        )
        if not snapshot.wifi_hardware_enabled:
            radio.set_subtitle("Đang bị tắt bằng công tắc phần cứng")
        radio.connect("notify::active", self._on_wifi_switch)
        group.add(radio)

        hint = vm.empty_wifi_hint(snapshot)
        if hint:
            row = Adw.ActionRow(title="Chưa có mạng nào được lưu", subtitle=hint)
            row.set_subtitle_lines(3)
            button = Gtk.Button(label="Mở cài đặt Wi-Fi", valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _b: self._controller.open_wifi_settings())
            row.add_suffix(button)
            group.add(row)

        for row_vm in vm.connection_rows(snapshot, ConnType.WIFI):
            group.add(self._connection_row(row_vm))
        return group

    def _wired_group(self, snapshot) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Có dây")
        unplugged = [d for d in snapshot.ethernet_devices if not d.carrier]
        if unplugged:
            group.set_description(
                "Chưa cắm cáp: " + ", ".join(d.interface for d in unplugged)
            )

        rows = vm.connection_rows(snapshot, ConnType.ETHERNET)
        if not rows:
            group.add(Adw.ActionRow(title="Chưa có cấu hình có dây nào", sensitive=False))
        for row_vm in rows:
            group.add(self._connection_row(row_vm))
        return group

    def _connection_row(self, row_vm: vm.ConnectionRow) -> Adw.ActionRow:
        row = Adw.ActionRow(title=row_vm.title, subtitle=row_vm.subtitle)
        row.add_prefix(Gtk.Image.new_from_icon_name(row_vm.icon_name))

        if row_vm.is_active:
            row.add_prefix(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))

        button = Gtk.Button(
            label=row_vm.action_label,
            valign=Gtk.Align.CENTER,
            sensitive=row_vm.can_act,
            css_classes=["destructive-action"] if row_vm.is_active else ["suggested-action"],
        )
        button.connect(
            "clicked",
            lambda _b, uuid=row_vm.uuid, active=row_vm.is_active: (
                self._controller.deactivate_connection(uuid)
                if active
                else self._controller.activate_connection(uuid)
            ),
        )
        row.add_suffix(button)

        # Nhãn chữ thay vì mũi tên trần: người dùng không đoán được ">" làm gì.
        details = Gtk.Button(
            label="Chi tiết",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
            tooltip_text="Xem trạng thái, IP, DNS và route đang áp dụng",
        )
        details.connect("clicked", lambda _b, uuid=row_vm.uuid: self.show_detail(uuid))
        row.add_suffix(details)
        row.set_activatable_widget(details)
        return row

    def _on_wifi_switch(self, switch_row, _param) -> None:
        wanted = switch_row.get_active()
        if wanted != self._controller.snapshot().wifi_enabled:
            self._controller.set_wifi_enabled(wanted)

    # ── chi tiết kết nối ────────────────────────────────────────────────────

    def show_detail(self, uuid: str) -> None:
        from .detail_page import ConnectionDetailPage

        if vm.connection_detail(self._controller.snapshot(), uuid) is None:
            self.toast("Cấu hình này không còn tồn tại")
            self.refresh()
            return
        self._nav.push(ConnectionDetailPage(self, self._controller, uuid))

    def confirm_delete(self, detail: vm.ConnectionDetail) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Xoá '{detail.title}'?",
            body="Cấu hình sẽ bị xoá vĩnh viễn khỏi NetworkManager.",
        )
        dialog.add_response("cancel", "Huỷ")
        dialog.add_response("delete", "Xoá")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda _d, response: (
                None    # xoá connection đã bỏ: app không sửa cấu hình máy
                if response == "delete"
                else None
            ),
        )
        dialog.present(self)

    # ── trang Proxy ─────────────────────────────────────────────────────────

    def _build_proxy_page(self, _snapshot) -> Gtk.Widget:
        proxy = self._controller.proxy
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title="Cấu hình proxy",
            description="Chọn một cấu hình để bật. Mật khẩu lưu trong GNOME Keyring.",
        )
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                         tooltip_text="Thêm cấu hình")
        add.connect("clicked", lambda _b: self._edit_proxy(None))
        group.set_header_suffix(add)

        off = Adw.ActionRow(title="Tắt proxy", subtitle="Không dùng proxy ở lớp nào")
        off_btn = Gtk.Button(
            label="Đang tắt" if not proxy.is_enabled else "Tắt",
            valign=Gtk.Align.CENTER,
            sensitive=proxy.is_enabled,
        )
        off_btn.connect("clicked", lambda _b: self._controller.disable_proxy())
        off.add_suffix(off_btn)
        group.add(off)

        rows = vm.proxy_rows(proxy.configs, proxy.active.id if proxy.active else None)
        if not rows:
            group.add(
                Adw.ActionRow(
                    title="Chưa có cấu hình nào",
                    subtitle="Bấm + để thêm, hoặc nhập cấu hình sẵn có bên dưới",
                    sensitive=False,
                )
            )
        for row_vm in rows:
            group.add(self._proxy_row(row_vm))
        page.add(group)

        page.add(self._proxy_import_group())
        return self._scrolled(page)

    def _proxy_row(self, row_vm: vm.ProxyRow) -> Adw.ActionRow:
        row = Adw.ActionRow(title=row_vm.title, subtitle=row_vm.subtitle)
        if row_vm.is_active:
            row.add_prefix(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))

        use = Gtk.Button(
            label="Đang dùng" if row_vm.is_active else "Dùng",
            valign=Gtk.Align.CENTER,
            sensitive=not row_vm.is_active,
            css_classes=[] if row_vm.is_active else ["suggested-action"],
        )
        use.connect(
            "clicked", lambda _b, cid=row_vm.config_id: self._controller.activate_proxy(cid)
        )
        row.add_suffix(use)

        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                          css_classes=["flat"], tooltip_text="Sửa")
        edit.connect("clicked", lambda _b, cid=row_vm.config_id: self._edit_proxy(cid))
        row.add_suffix(edit)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text="Xoá")
        remove.connect("clicked", lambda _b, r=row_vm: self._confirm_delete_proxy(r))
        row.add_suffix(remove)
        return row

    def _proxy_import_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Công cụ")

        imp = Adw.ActionRow(
            title="Nhập cấu hình proxy đang có của hệ thống",
            subtitle="Giữ nguyên danh sách bỏ qua bạn đã cấu hình",
        )
        imp_btn = Gtk.Button(label="Nhập", valign=Gtk.Align.CENTER)
        imp_btn.connect("clicked", lambda _b: self._controller.import_current_proxy())
        imp.add_suffix(imp_btn)
        group.add(imp)

        snippet = Adw.ActionRow(
            title="Chép lệnh export",
            subtitle="Dán vào terminal đang mở để có hiệu lực ngay",
        )
        snippet_btn = Gtk.Button(label="Chép", valign=Gtk.Align.CENTER)
        snippet_btn.connect("clicked", lambda _b: self._copy_snippet())
        snippet.add_suffix(snippet_btn)
        group.add(snippet)
        return group

    def _copy_snippet(self) -> None:
        text = self._controller.proxy.export_snippet()
        if not text:
            self.toast("Proxy đang tắt — không có gì để chép")
            return
        self.get_clipboard().set(text)
        self.toast("Đã chép. Dán vào terminal đang mở.")

    def _edit_proxy(self, config_id: str | None) -> None:
        ProxyEditor(self, self._controller, config_id).present(self)

    def _confirm_delete_proxy(self, row_vm: vm.ProxyRow) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Xoá cấu hình '{row_vm.title}'?",
            body=(
                "Cấu hình này đang được dùng — proxy sẽ bị tắt."
                if row_vm.is_active
                else "Mật khẩu đã lưu cũng sẽ bị xoá."
            ),
        )
        dialog.add_response("cancel", "Huỷ")
        dialog.add_response("delete", "Xoá")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, r: (
                self._controller.delete_proxy(row_vm.config_id) if r == "delete" else None
            ),
        )
        dialog.present(self)

    # ── trang Bộ cấu hình ───────────────────────────────────────────────────

    def _build_profiles_page(self, _snapshot) -> Gtk.Widget:
        service = self._controller.profiles
        page = Adw.PreferencesPage()

        if self._apply_report is not None:
            page.add(self._apply_progress_group(self._apply_report))

        group = Adw.PreferencesGroup(
            title="Bộ cấu hình",
            description=(
                "Mỗi bộ gom cả cấu hình mạng lẫn proxy. Chọn một bộ để chuyển "
                "toàn bộ bối cảnh trong một thao tác."
            ),
        )
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                         tooltip_text="Tạo Bộ cấu hình")
        add.connect("clicked", lambda _b: self._edit_profile(None))
        group.set_header_suffix(add)

        active = service.active
        busy = service.busy

        none_row = Adw.ActionRow(
            title="Không dùng Bộ cấu hình",
            subtitle="Quản lý mạng thủ công như bình thường",
        )
        none_btn = Gtk.Button(
            label="Đang chọn" if active is None else "Chọn",
            valign=Gtk.Align.CENTER,
            sensitive=active is not None and not busy,
        )
        none_btn.connect("clicked", lambda _b: self._controller.clear_profile())
        none_row.add_suffix(none_btn)
        group.add(none_row)

        for profile in service.profiles:
            group.add(self._profile_row(profile, service, active, busy))

        if not service.profiles:
            group.add(
                Adw.ActionRow(
                    title="Chưa có Bộ cấu hình nào",
                    subtitle="Bấm + để tạo, hoặc dùng nút bên dưới để chụp trạng thái hiện tại",
                    sensitive=False,
                )
            )
        page.add(group)

        tools = Adw.PreferencesGroup(title="Công cụ")
        capture = Adw.ActionRow(
            title="Lưu trạng thái hiện tại thành Bộ cấu hình",
            subtitle="Cấu hình mạng bằng tay cho chạy được, rồi lưu lại để dùng sau",
        )
        capture_btn = Gtk.Button(label="Chụp", valign=Gtk.Align.CENTER, sensitive=not busy)
        capture_btn.connect("clicked", lambda _b: self.prompt_capture_profile())
        capture.add_suffix(capture_btn)
        tools.add(capture)
        page.add(tools)
        return self._scrolled(page)

    def _profile_row(self, profile, service, active, busy) -> Adw.ActionRow:
        is_active = active is not None and active.id == profile.id
        subtitle = profile.broken_reason if profile.is_broken else service.describe(profile)

        row = Adw.ActionRow(title=profile.label, subtitle=subtitle)
        row.set_subtitle_lines(2)
        if profile.is_broken:
            row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        elif is_active:
            row.add_prefix(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))

        # Bộ cấu hình đang dùng vẫn cần áp lại được: sau khi sửa route hoặc khi
        # mạng bị thay đổi từ bên ngoài, người dùng phải có cách đưa nó về đúng
        # trạng thái mà không phải chọn bộ khác rồi chọn lại.
        use = Gtk.Button(
            label="Áp dụng lại" if is_active else "Áp dụng",
            valign=Gtk.Align.CENTER,
            sensitive=not busy and not profile.is_broken,
            css_classes=["suggested-action"] if not is_active else [],
            tooltip_text=(
                "Áp lại Bộ cấu hình này lên trạng thái mạng hiện tại"
                if is_active else ""
            ),
        )
        use.connect("clicked", lambda _b, pid=profile.id: self._controller.apply_profile(pid))
        row.add_suffix(use)

        for icon, tip, handler in (
            ("document-edit-symbolic", "Sửa", self._edit_profile),
            ("edit-copy-symbolic", "Nhân bản", self._duplicate_profile),
            ("user-trash-symbolic", "Xoá", self._confirm_delete_profile),
        ):
            button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER,
                                css_classes=["flat"], tooltip_text=tip)
            button.connect("clicked", lambda _b, p=profile, h=handler: h(p.id))
            row.add_suffix(button)
        return row

    def _apply_progress_group(self, report) -> Adw.PreferencesGroup:
        """Tiến trình từng bước, không phải spinner vô định (§3.5.4)."""
        from ..domain.models import StepStatus

        marks = {
            StepStatus.PENDING: "○",
            StepStatus.RUNNING: "◐",
            StepStatus.OK: "✓",
            StepStatus.FAILED: "✕",
            StepStatus.SKIPPED: "–",
            StepStatus.ROLLED_BACK: "↩",
        }
        group = Adw.PreferencesGroup(title=f"Đang áp dụng '{report.profile_name}'")
        for step in report.steps:
            row = Adw.ActionRow(
                title=f"{marks[step.status]}  {step.label}", subtitle=step.detail
            )
            group.add(row)
        return group

    def update_apply_progress(self, report) -> None:
        self._apply_report = report
        if self._current == "profiles":
            self.refresh()

    def prompt_capture_profile(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Lưu trạng thái hiện tại",
            body="Bộ cấu hình mới sẽ ghi lại các kết nối đang chạy, trạng thái "
                 "Wi-Fi và proxy đang bật.",
        )
        entry = Adw.EntryRow(title="Tên Bộ cấu hình")
        listbox = Gtk.ListBox(css_classes=["boxed-list"], width_request=340)
        listbox.append(entry)
        dialog.set_extra_child(listbox)
        dialog.add_response("cancel", "Huỷ")
        dialog.add_response("save", "Lưu")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def on_response(_dialog, response) -> None:
            if response != "save":
                return
            name = entry.get_text().strip()
            if not name:
                self.toast("Cần đặt tên cho Bộ cấu hình")
                return
            profile = self._controller.profiles.capture_current(name)
            self.toast(f"Đã lưu '{profile.name}'")
            self.select_page("profiles")

        dialog.connect("response", on_response)
        dialog.present(self)

    def _edit_profile(self, profile_id: str | None) -> None:
        from .profile_editor import ProfileEditor

        profile = self._controller.profiles.get(profile_id) if profile_id else None
        ProfileEditor(self, self._controller, profile).present(self)

    def _duplicate_profile(self, profile_id: str) -> None:
        source = self._controller.profiles.get(profile_id)
        if source is None:
            return
        clone = self._controller.profiles.duplicate(profile_id, f"{source.name} (bản sao)")
        if clone is not None:
            self.toast(f"Đã tạo '{clone.name}'")
        self.refresh()

    def _confirm_delete_profile(self, profile_id: str) -> None:
        profile = self._controller.profiles.get(profile_id)
        if profile is None:
            return
        dialog = Adw.AlertDialog(
            heading=f"Xoá '{profile.name}'?",
            body="Chỉ xoá Bộ cấu hình. Các cấu hình kết nối và proxy vẫn giữ nguyên.",
        )
        dialog.add_response("cancel", "Huỷ")
        dialog.add_response("delete", "Xoá")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, r: self._delete_profile(profile_id) if r == "delete" else None,
        )
        dialog.present(self)

    def _delete_profile(self, profile_id: str) -> None:
        self._controller.profiles.delete(profile_id)
        self.toast("Đã xoá Bộ cấu hình")
        self.refresh()

    def select_page(self, key: str) -> None:
        for index, (page_key, _t, _i) in enumerate(self.PAGES):
            if page_key == key:
                self._sidebar_list.select_row(self._sidebar_list.get_row_at_index(index))
                return

    # ── trang Định tuyến ────────────────────────────────────────────────────

    def _build_routing_page(self, snapshot) -> Gtk.Widget:
        from ..domain import routing

        page = Adw.PreferencesPage()
        routes = snapshot.system_routes
        rules = snapshot.routing_rules

        # ── tra cứu ──
        lookup_group = Adw.PreferencesGroup(
            title="Địa chỉ này đi đường nào?",
            description=(
                "Mô phỏng đúng cách kernel chọn route, kể cả luật định tuyến do "
                "phần mềm khác cài."
            ),
        )
        entry = Adw.EntryRow(title="Địa chỉ IPv4", text=self._lookup_query)
        entry.set_show_apply_button(True)
        entry.connect("apply", lambda row: self._do_lookup(row.get_text()))
        lookup_group.add(entry)

        if self._lookup_result is not None:
            result = self._lookup_result
            row = Adw.ActionRow(
                title=result.interface or "Không tới được",
                subtitle=result.explain(),
            )
            row.set_subtitle_lines(3)
            row.add_prefix(
                Gtk.Image.new_from_icon_name(
                    "emblem-ok-symbolic" if result.found else "dialog-warning-symbolic"
                )
            )
            lookup_group.add(row)
            for other in result.also_matched:
                lookup_group.add(
                    Adw.ActionRow(
                        title=str(other), subtitle="cũng khớp nhưng kém ưu tiên hơn",
                        sensitive=False,
                    )
                )
        page.add(lookup_group)

        # ── nhận xét ──
        issues = routing.analyze(routes, rules)
        if issues:
            group = Adw.PreferencesGroup(title="Nhận xét")
            for issue in issues:
                row = Adw.ActionRow(title=issue.title, subtitle=issue.detail)
                row.set_subtitle_lines(3)
                row.add_prefix(
                    Gtk.Image.new_from_icon_name(
                        "dialog-warning-symbolic"
                        if issue.level is routing.IssueLevel.WARNING
                        else "dialog-information-symbolic"
                    )
                )
                group.add(row)
            page.add(group)

        # ── bảng route theo interface ──
        self._request_runtime_ipv4(snapshot)
        for group_vm in routing.group_by_interface(routes):
            group = Adw.PreferencesGroup(
                title=group_vm.interface,
                description=vm.runtime_routes_summary(
                    self._runtime_ipv4.get(group_vm.interface)
                ),
            )
            for entry_vm in group_vm.routes:
                r = entry_vm.route
                bits = []
                if r.table not in (None, 0, 254):
                    bits.append(f"bảng {r.table}")
                if r.metric is not None:
                    bits.append(f"metric {r.metric}")
                row = Adw.ActionRow(title=str(r), subtitle=" · ".join(bits))
                row.set_title_selectable(True)
                group.add(row)
            page.add(group)

        if not routes:
            page.add(self._placeholder("Không đọc được bảng định tuyến"))

        # ── ip rule ──
        if rules:
            group = Adw.PreferencesGroup(
                title="Luật định tuyến của hệ thống (ip rule)",
                description=(
                    "Quyết định bảng nào được tra trước. Gồm cả luật do phần mềm "
                    "khác cài — NetworkManager không quản lý những luật đó."
                ),
            )
            for rule in rules:
                row = Adw.ActionRow(title=str(rule))
                row.set_title_selectable(True)
                if not rule.matches_plain_traffic:
                    row.set_subtitle("không áp cho traffic thông thường")
                group.add(row)
            page.add(group)
        return self._scrolled(page)

    def _request_runtime_ipv4(self, snapshot) -> None:
        """Đọc cấu hình đang chạy của các thiết bị đã kết nối.

        Bất đồng bộ nên lần vẽ đầu chưa có dữ liệu; kết quả về thì vẽ lại. Chỉ
        vẽ lại khi có thay đổi thật, nếu không sẽ thành vòng lặp vô tận.
        """
        for device in snapshot.devices:
            if not device.is_connected:
                continue
            self._controller.read_runtime_ipv4(
                device.interface,
                lambda runtime, iface=device.interface: self._on_runtime_ipv4(
                    iface, runtime
                ),
            )

    def _on_runtime_ipv4(self, interface: str, runtime) -> None:
        previous = self._runtime_ipv4.get(interface)
        if previous == runtime:
            return
        self._runtime_ipv4[interface] = runtime
        if self._current == "routing":
            self.refresh()

    def _do_lookup(self, text: str) -> None:
        from ..domain import routing

        snapshot = self._controller.snapshot()
        self._lookup_query = text.strip()
        self._lookup_result = (
            routing.lookup(snapshot.system_routes, self._lookup_query,
                           snapshot.routing_rules)
            if self._lookup_query
            else None
        )
        self.refresh()

    # ── trang Tuỳ chọn ──────────────────────────────────────────────────────

    def _build_settings_page(self, _snapshot) -> Gtk.Widget:
        from ..infra import autostart

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Khởi động")

        row = Adw.SwitchRow(
            title="Mở khi máy khởi động",
            subtitle=(
                "App chạy nền ở khay hệ thống ngay sau khi đăng nhập, "
                "không bật cửa sổ."
            ),
            active=autostart.is_enabled(),
        )
        row.set_subtitle_lines(2)
        row.connect("notify::active", self._on_autostart_toggled)
        group.add(row)

        path_row = Adw.ActionRow(
            title="Lệnh chạy nền",
            subtitle=autostart.launch_command(),
            sensitive=False,
        )
        path_row.set_subtitle_lines(2)
        group.add(path_row)
        page.add(group)

        window_group = Adw.PreferencesGroup(
            title="Cửa sổ này",
            description=(
                "Đóng cửa sổ chỉ ẩn đi, app vẫn chạy ở khay. Muốn thoát hẳn thì "
                "dùng Thoát trong menu khay."
            ),
        )
        quit_row = Adw.ActionRow(
            title="Thoát ứng dụng",
            subtitle="Tắt cả khay; cấu hình mạng trở về nguyên gốc",
        )
        quit_button = Gtk.Button(
            label="Thoát", valign=Gtk.Align.CENTER, css_classes=["destructive-action"]
        )
        quit_button.connect("clicked", lambda _b: self._controller.quit_app())
        quit_row.add_suffix(quit_button)
        window_group.add(quit_row)
        page.add(window_group)
        return self._scrolled(page)

    def _on_autostart_toggled(self, row, _param) -> None:
        from ..infra import autostart

        wanted = row.get_active()
        if wanted == autostart.is_enabled():
            return
        if autostart.set_enabled(wanted):
            self.toast(
                "Sẽ tự chạy khi đăng nhập" if wanted else "Đã tắt khởi động cùng máy"
            )
        else:
            self.toast("Không đổi được cài đặt khởi động")
            row.set_active(not wanted)

    # ── trang Chẩn đoán ─────────────────────────────────────────────────────

    def _build_diagnostics_page(self, snapshot) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title="Các lớp proxy",
            description=(
                "Proxy trên Linux gồm nhiều lớp độc lập. Bảng này cho thấy từng "
                "lớp đang ở trạng thái nào."
            ),
        )
        for row_vm in vm.layer_rows(self._controller.proxy.layer_statuses()):
            row = Adw.ActionRow(title=row_vm.label, subtitle=row_vm.value)
            row.set_subtitle_lines(2)
            row.add_prefix(
                Gtk.Image.new_from_icon_name(
                    "emblem-ok-symbolic" if row_vm.runtime else "window-close-symbolic"
                )
            )
            group.add(row)
        page.add(group)

        perms = Adw.PreferencesGroup(
            title="Quyền",
            description="NetworkManager nạp quyền bất đồng bộ sau khi khởi động.",
        )
        p = snapshot.permissions
        for label, state in (
            ("Sửa cấu hình hệ thống", p.modify_system),
            ("Điều khiển mạng", p.network_control),
            ("Bật/tắt Wi-Fi", p.enable_disable_wifi),
            ("Checkpoint rollback", p.checkpoint_rollback),
        ):
            perms.add(Adw.ActionRow(title=label, subtitle=state.value))
        page.add(perms)
        return self._scrolled(page)

    # ── tiện ích ────────────────────────────────────────────────────────────

    @staticmethod
    def _scrolled(child: Gtk.Widget) -> Gtk.Widget:
        return Gtk.ScrolledWindow(child=child, vexpand=True, hexpand=True)

    @staticmethod
    def _placeholder(text: str) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.add(Adw.ActionRow(title=text, sensitive=False))
        return group
