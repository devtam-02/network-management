"""Hộp thoại thêm/sửa một Bộ cấu hình (F6).

Mỗi thiết bị mạng là một hàng: chọn hành động (kết nối / ngắt / không đụng tới)
và cấu hình kết nối tương ứng.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..domain.models import Binding, BindingAction, Profile

_ACTIONS = [
    (BindingAction.LEAVE_ALONE, "Không đụng tới"),
    (BindingAction.ACTIVATE, "Kết nối"),
    (BindingAction.DISCONNECT, "Ngắt kết nối"),
]

_WIFI_CHOICES = [
    (None, "Không đụng tới"),
    (True, "Bật"),
    (False, "Tắt"),
]

_ICONS = ["🌐", "🏢", "🏠", "🏨", "✈️", "🔒", "🧪", "📡"]


class ProfileEditor(Adw.Dialog):
    def __init__(self, window, controller, profile: Profile | None) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._service = controller.profiles

        self._is_new = profile is None
        self._profile = profile or Profile(id=_new_id(), name="")
        self.set_title("Bộ cấu hình mới" if self._is_new else "Sửa Bộ cấu hình")
        self.set_content_width(600)
        self.set_content_height(720)
        self._build()

    # ── dựng ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        page = Adw.PreferencesPage()

        general = Adw.PreferencesGroup(title="Chung")
        self._name = Adw.EntryRow(title="Tên", text=self._profile.name)
        general.add(self._name)

        self._icon = Adw.ComboRow(
            title="Biểu tượng",
            model=Gtk.StringList.new(_ICONS),
            selected=_ICONS.index(self._profile.icon) if self._profile.icon in _ICONS else 0,
        )
        general.add(self._icon)

        self._description = Adw.EntryRow(title="Mô tả", text=self._profile.description)
        general.add(self._description)
        page.add(general)

        # ── mạng ──
        snapshot = self._controller.snapshot()
        network = Adw.PreferencesGroup(
            title="Mạng",
            description=(
                "Thiết bị vắng mặt sẽ được bỏ qua, trừ khi bạn đánh dấu bắt buộc."
            ),
        )
        self._device_rows: dict[str, tuple] = {}
        for device in snapshot.devices:
            network.add(self._device_expander(device))
        if not snapshot.devices:
            network.add(Adw.ActionRow(title="Không tìm thấy thiết bị mạng", sensitive=False))
        page.add(network)

        # ── Wi-Fi radio ──
        if snapshot.has_wifi_hardware:
            radio_group = Adw.PreferencesGroup(title="Radio Wi-Fi")
            self._wifi = Adw.ComboRow(
                title="Khi áp dụng Bộ cấu hình này",
                model=Gtk.StringList.new([label for _v, label in _WIFI_CHOICES]),
                selected=next(
                    i for i, (v, _l) in enumerate(_WIFI_CHOICES)
                    if v is self._profile.wifi_enabled
                ),
            )
            radio_group.add(self._wifi)
            page.add(radio_group)
        else:
            self._wifi = None

        # ── proxy ──
        proxy_group = Adw.PreferencesGroup(title="Proxy")
        self._proxy_choices: list[tuple[str | None, str]] = [
            (None, "Không đụng tới"),
            ("", "Tắt proxy"),
        ]
        self._proxy_choices += [(c.id, c.name) for c in self._controller.proxy.configs]
        self._proxy = Adw.ComboRow(
            title="Cấu hình proxy",
            model=Gtk.StringList.new([label for _v, label in self._proxy_choices]),
            selected=next(
                (i for i, (v, _l) in enumerate(self._proxy_choices)
                 if v == self._profile.proxy_id),
                0,
            ),
        )
        proxy_group.add(self._proxy)
        page.add(proxy_group)

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
        box.append(Gtk.ScrolledWindow(child=page, vexpand=True))

        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

    def _device_expander(self, device) -> Adw.ExpanderRow:
        existing = self._profile.binding_for(device.interface)
        candidates = self._service.candidate_connections(device.interface)

        row = Adw.ExpanderRow(
            title=device.interface,
            subtitle="Wi-Fi" if device.type.name == "WIFI" else "Có dây",
        )

        action = Adw.ComboRow(
            title="Hành động",
            model=Gtk.StringList.new([label for _a, label in _ACTIONS]),
            selected=next(
                (i for i, (a, _l) in enumerate(_ACTIONS)
                 if existing is not None and a is existing.action),
                0,
            ),
        )
        row.add_row(action)

        labels = [c.display_name for c in candidates] or ["(không có cấu hình nào)"]
        connection = Adw.ComboRow(
            title="Dùng cấu hình",
            model=Gtk.StringList.new(labels),
            selected=next(
                (i for i, c in enumerate(candidates)
                 if existing is not None and c.uuid == existing.connection_uuid),
                0,
            ),
            sensitive=bool(candidates),
        )
        row.add_row(connection)

        required = Adw.SwitchRow(
            title="Bắt buộc",
            subtitle="Thiếu thiết bị này thì coi là áp dụng thất bại",
            active=existing.required if existing else False,
        )
        row.add_row(required)

        self._device_rows[device.interface] = (action, connection, required, candidates)
        return row

    # ── lưu ─────────────────────────────────────────────────────────────────

    def _collect(self) -> Profile:
        profile = self._profile
        profile.name = self._name.get_text().strip()
        profile.icon = _ICONS[self._icon.get_selected()]
        profile.description = self._description.get_text().strip()

        bindings: list[Binding] = []
        for interface, (action_row, conn_row, req_row, candidates) in self._device_rows.items():
            action = _ACTIONS[action_row.get_selected()][0]
            if action is BindingAction.LEAVE_ALONE:
                continue        # không lưu binding rỗng cho gọn file
            uuid = None
            if action is BindingAction.ACTIVATE and candidates:
                uuid = candidates[conn_row.get_selected()].uuid
            bindings.append(
                Binding(
                    device_match=interface,
                    action=action,
                    connection_uuid=uuid,
                    required=req_row.get_active(),
                )
            )
        profile.bindings = bindings

        profile.wifi_enabled = (
            _WIFI_CHOICES[self._wifi.get_selected()][0] if self._wifi is not None else None
        )
        profile.proxy_id = self._proxy_choices[self._proxy.get_selected()][0]
        return profile

    def _on_save(self) -> None:
        profile = self._collect()
        result = self._service.save(profile)
        if not result.ok:
            self._error.set_text(result.message)
            self._error.set_visible(True)
            return
        self._controller.refresh()
        self.close()


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())
