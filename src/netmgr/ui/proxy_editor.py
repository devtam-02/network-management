"""Hộp thoại thêm/sửa cấu hình proxy.

Validate dùng chung `domain.validators.validate_proxy` — không viết lại luật ở
tầng UI, nếu không hai nơi sẽ lệch nhau.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..domain.models import (
    DEFAULT_IGNORE_HOSTS,
    ProxyConfig,
    ProxyEndpoint,
    ProxyLayerId,
    ProxyMode,
)
from ..domain.validators import validate_proxy

_MODES = [
    (ProxyMode.NONE, "Tắt"),
    (ProxyMode.MANUAL, "Thủ công"),
    (ProxyMode.AUTO, "Tự động (PAC)"),
]


class ProxyEditor(Adw.Dialog):
    def __init__(self, window, controller, config_id: str | None) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._config_id = config_id

        existing = controller.proxy.get(config_id) if config_id else None
        self._is_new = existing is None
        self._config = existing or _blank_config()

        self.set_title("Sửa cấu hình proxy" if existing else "Cấu hình proxy mới")
        self.set_content_width(560)
        self.set_content_height(680)
        self._build()

    # ── dựng ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        page = Adw.PreferencesPage()

        general = Adw.PreferencesGroup(title="Chung")
        self._name = Adw.EntryRow(title="Tên", text=self._config.name)
        general.add(self._name)

        self._mode = Adw.ComboRow(
            title="Chế độ",
            model=Gtk.StringList.new([label for _m, label in _MODES]),
            selected=next(
                i for i, (m, _l) in enumerate(_MODES) if m is self._config.mode
            ),
        )
        self._mode.connect("notify::selected", lambda *_a: self._sync_visibility())
        general.add(self._mode)
        page.add(general)

        # ── thủ công ──
        self._manual_group = Adw.PreferencesGroup(title="Máy chủ proxy")
        self._same = Adw.SwitchRow(
            title="Dùng chung cho HTTP, HTTPS và FTP",
            subtitle="SOCKS luôn phải khai báo riêng — đó là giao thức khác",
            active=self._config.use_same_for_all,
        )
        self._same.connect("notify::active", lambda *_a: self._sync_visibility())
        self._manual_group.add(self._same)

        self._hosts: dict[str, tuple[Adw.EntryRow, Adw.SpinRow]] = {}
        for scheme, label in (
            ("http", "HTTP"),
            ("https", "HTTPS"),
            ("ftp", "FTP"),
            ("socks", "SOCKS"),
        ):
            endpoint: ProxyEndpoint = getattr(self._config, scheme)
            host = Adw.EntryRow(title=f"{label} — máy chủ", text=endpoint.host)
            port = Adw.SpinRow.new_with_range(0, 65535, 1)
            port.set_title(f"{label} — cổng")
            port.set_value(endpoint.port)
            self._manual_group.add(host)
            self._manual_group.add(port)
            self._hosts[scheme] = (host, port)
        page.add(self._manual_group)

        # ── PAC ──
        self._auto_group = Adw.PreferencesGroup(title="Tự động")
        self._pac = Adw.EntryRow(title="URL file PAC", text=self._config.pac_url)
        self._auto_group.add(self._pac)
        page.add(self._auto_group)

        # ── xác thực ──
        auth_group = Adw.PreferencesGroup(title="Xác thực")
        self._auth = Adw.SwitchRow(title="Proxy yêu cầu đăng nhập",
                                   active=self._config.auth_enabled)
        self._auth.connect("notify::active", lambda *_a: self._sync_visibility())
        auth_group.add(self._auth)

        self._username = Adw.EntryRow(title="Tên đăng nhập", text=self._config.username)
        auth_group.add(self._username)

        self._password = Adw.PasswordEntryRow(title="Mật khẩu")
        if not self._is_new:
            stored = self._controller.proxy_password(self._config.id)
            if stored:
                self._password.set_text(stored)
        auth_group.add(self._password)

        note = Adw.ActionRow(
            title="Mật khẩu lưu trong GNOME Keyring",
            subtitle=(
                "Không ghi vào file cấu hình. Khi bật proxy, một bản sao được đặt "
                "vào dconf vì đó là chỗ duy nhất trình duyệt đọc được."
            ),
            sensitive=False,
        )
        note.set_subtitle_lines(3)
        auth_group.add(note)
        page.add(auth_group)

        # ── bỏ qua ──
        ignore_group = Adw.PreferencesGroup(
            title="Không dùng proxy cho",
            description=(
                "Mỗi dòng một mục. Cú pháp GNOME (*.vidu.com, 10.*.*.*) được tự "
                "dịch sang no_proxy cho terminal."
            ),
        )
        self._ignore = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8,
        )
        self._ignore.get_buffer().set_text("\n".join(self._config.ignore_hosts))
        frame = Gtk.Frame(child=self._ignore, height_request=120)
        ignore_group.add(frame)
        page.add(ignore_group)

        # ── lớp áp dụng ──
        layer_group = Adw.PreferencesGroup(
            title="Áp dụng lên",
            description="Mỗi lớp độc lập; lớp không chọn sẽ bị tắt khi bật cấu hình này.",
        )
        self._layers: dict[ProxyLayerId, Adw.SwitchRow] = {}
        for layer_id, title, subtitle in (
            (ProxyLayerId.DESKTOP, "Ứng dụng desktop",
             "Trình duyệt và app GTK — có hiệu lực ngay"),
            (ProxyLayerId.ENVIRONMENT, "Biến môi trường",
             "terminal, curl, git — cần session đăng nhập mới"),
        ):
            row = Adw.SwitchRow(
                title=title, subtitle=subtitle, active=layer_id in self._config.layers
            )
            layer_group.add(row)
            self._layers[layer_id] = row
        page.add(layer_group)

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
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        mode = _MODES[self._mode.get_selected()][0]
        self._manual_group.set_visible(mode is ProxyMode.MANUAL)
        self._auto_group.set_visible(mode is ProxyMode.AUTO)

        same = self._same.get_active()
        for scheme in ("https", "ftp"):
            host, port = self._hosts[scheme]
            host.set_visible(not same)
            port.set_visible(not same)

        auth = self._auth.get_active()
        self._username.set_sensitive(auth)
        self._password.set_sensitive(auth)

    # ── lưu ─────────────────────────────────────────────────────────────────

    def _collect(self) -> ProxyConfig:
        config = self._config
        config.name = self._name.get_text().strip()
        config.mode = _MODES[self._mode.get_selected()][0]
        config.use_same_for_all = self._same.get_active()
        config.pac_url = self._pac.get_text().strip()
        config.auth_enabled = self._auth.get_active()
        config.username = self._username.get_text().strip()

        for scheme, (host_row, port_row) in self._hosts.items():
            host = host_row.get_text().strip()
            port = int(port_row.get_value())
            setattr(config, scheme, ProxyEndpoint(host, port) if host else ProxyEndpoint())

        buffer = self._ignore.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        config.ignore_hosts = [line.strip() for line in text.splitlines() if line.strip()]

        config.layers = {lid for lid, row in self._layers.items() if row.get_active()}
        return config

    def _on_save(self) -> None:
        config = self._collect()
        result = validate_proxy(config)
        if not result.ok:
            self._error.set_text(result.errors[0].message)
            self._error.set_visible(True)
            return

        password = self._password.get_text() if config.auth_enabled else None
        self._controller.save_proxy(config, password)
        self.close()


def _blank_config() -> ProxyConfig:
    from uuid import uuid4

    return ProxyConfig(
        id=str(uuid4()),
        name="",
        mode=ProxyMode.MANUAL,
        ignore_hosts=list(DEFAULT_IGNORE_HOSTS),
    )
