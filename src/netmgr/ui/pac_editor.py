"""Sửa nội dung file PAC của một cấu hình proxy.

Với "Cấu hình mặc định" thì đây là thứ DUY NHẤT sửa được: phần còn lại (tên, chế
độ, đường dẫn PAC) do app định nghĩa nên không cho đổi.

Cố tình là trình soạn thảo văn bản trần, không phải form: PAC là một hàm
JavaScript, mọi cố gắng gói nó vào ô nhập liệu đều sẽ chặn mất những thứ hợp lệ.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class PacEditor(Adw.Dialog):
    def __init__(self, window, controller, config_id: str) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._service = controller.proxy
        self._config_id = config_id

        config = self._service.get(config_id)
        self.set_title(f"File PAC — {config.name}" if config else "File PAC")
        self.set_content_width(760)
        self.set_content_height(680)
        self._build(config)

    def _build(self, config) -> None:
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Đóng")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Lưu", css_classes=["suggested-action"])
        save.connect("clicked", lambda _b: self._on_save())
        header.pack_end(save)

        self._error = Gtk.Label(css_classes=["error", "caption"], visible=False, wrap=True,
                                margin_top=6, margin_bottom=6)

        text = self._service.pac_content(self._config_id)
        self._buffer = Gtk.TextBuffer(text=text or "")
        view = Gtk.TextView(
            buffer=self._buffer,
            monospace=True,
            top_margin=12, bottom_margin=12, left_margin=12, right_margin=12,
        )
        scroller = Gtk.ScrolledWindow(child=view, vexpand=True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if config is not None and config.pac_path:
            # Đường dẫn phải chọn-sao-chép được: người dùng còn sửa bằng editor
            # khác, hoặc đưa vào git.
            path_row = Gtk.Label(
                label=config.pac_path,
                css_classes=["caption", "dim-label"],
                selectable=True, wrap=True, xalign=0,
                margin_start=12, margin_end=12, margin_top=8,
            )
            box.append(path_row)
        box.append(self._error)
        box.append(scroller)

        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

    def _on_save(self) -> None:
        start, end = self._buffer.get_bounds()
        text = self._buffer.get_text(start, end, False)

        result = self._service.save_pac_content(self._config_id, text)
        if not result.ok:
            self._error.set_text(result.message)
            self._error.set_visible(True)
            return

        self._window.toast(result.message or "Đã lưu file PAC")
        self.close()
        self._controller.refresh()
