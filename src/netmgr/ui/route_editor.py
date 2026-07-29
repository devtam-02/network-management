"""Hộp thoại thêm/sửa một route IPv4 (FR-R2, FR-R3).

Validate realtime khi gõ, dùng `domain.validators.validate_route` — cùng bộ luật
với mọi nơi khác trong app, không viết lại.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..domain.models import Ipv4Address, Ipv4Route
from ..domain.validators import (
    DEFAULT_NETMASK,
    Severity,
    netmask_to_prefix,
    parse_route_line,
    prefix_to_netmask,
    validate_route,
)


class RouteEditor(Adw.Dialog):
    def __init__(
        self,
        route: Ipv4Route | None,
        *,
        existing: list[Ipv4Route],
        local_subnets: list[Ipv4Address],
        on_save,
        devices: list[tuple[str, str]] | None = None,
        current_device: str | None = None,
    ) -> None:
        """
        devices: [(interface, nhãn hiển thị)] — có thì hiện ô chọn thiết bị và
            `on_save(route, interface)`. Không có thì `on_save(route)`.
        """
        super().__init__()
        self._original = route
        self._existing = existing
        self._local_subnets = local_subnets
        self._on_save = on_save
        self._devices = devices or []
        self._current_device = current_device

        self.set_title("Sửa route" if route else "Thêm route")
        # Không đặt chiều cao thì Adw.Dialog co lại vừa nội dung và cắt mất
        # phần dưới — form này có tới 7 hàng cộng ô nhập nhanh.
        self.set_content_width(560)
        self.set_content_height(720)
        self._build(route)
        self._validate()

    # ── dựng ────────────────────────────────────────────────────────────────

    def _build(self, route: Ipv4Route | None) -> None:
        page = Adw.PreferencesPage()

        quick = Adw.PreferencesGroup(
            title="Nhập nhanh",
            description="Dán một dòng dạng: 10.20.0.0/16 via 192.168.1.1 metric 100",
        )
        self._quick = Adw.EntryRow(title="Một dòng")
        self._quick.connect("apply", lambda _r: self._apply_quick())
        self._quick.set_show_apply_button(True)
        quick.add(self._quick)
        page.add(quick)

        main = Adw.PreferencesGroup(title="Route")

        self._device_row = None
        if self._devices:
            # Đặt ngay đầu: chọn đi qua thiết bị nào là quyết định đầu tiên
            # người dùng cần đưa ra khi máy có nhiều đường mạng.
            self._device_row = Adw.ComboRow(
                title="Đi qua",
                model=Gtk.StringList.new([label for _i, label in self._devices]),
                selected=next(
                    (i for i, (iface, _l) in enumerate(self._devices)
                     if iface == self._current_device),
                    0,
                ),
            )
            main.add(self._device_row)

        self._dest = Adw.EntryRow(title="Địa chỉ đích")
        # Netmask, không phải prefix: Cài đặt của Ubuntu hiển thị route theo dạng
        # này và người dùng mạng doanh nghiệp quen nghĩ theo netmask. Vẫn nhận cả
        # dạng prefix ("8") cho ai muốn gõ nhanh.
        self._netmask = Adw.EntryRow(title="Netmask")
        self._next_hop = Adw.EntryRow(title="Gateway")
        self._metric = Adw.SpinRow.new_with_range(-1, 4294967295, 1)
        self._metric.set_title("Metric (-1 = mặc định)")
        self._table = Adw.SpinRow.new_with_range(0, 4294967295, 1)
        self._table.set_title("Routing table (0 = mặc định)")
        self._onlink = Adw.SwitchRow(
            title="On-link",
            subtitle="Gateway nằm trực tiếp trên liên kết dù không cùng subnet",
        )
        for row in (self._dest, self._netmask, self._next_hop, self._metric,
                    self._table, self._onlink):
            main.add(row)
        page.add(main)

        if route is not None:
            self._dest.set_text(route.dest)
            self._netmask.set_text(prefix_to_netmask(route.prefix))
            self._next_hop.set_text(route.next_hop or "")
            self._metric.set_value(-1 if route.metric is None else route.metric)
            self._table.set_value(route.table or 0)
            self._onlink.set_active(route.onlink)
        else:
            self._netmask.set_text(DEFAULT_NETMASK)
            self._metric.set_value(-1)

        for widget in (self._dest, self._netmask, self._next_hop):
            widget.connect("changed", lambda _w: self._validate())
        for widget in (self._metric, self._table):
            widget.connect("notify::value", lambda *_a: self._validate())
        self._onlink.connect("notify::active", lambda *_a: self._validate())

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Huỷ")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)

        self._save_button = Gtk.Button(label="Lưu", css_classes=["suggested-action"])
        self._save_button.connect("clicked", lambda _b: self._on_save_clicked())
        header.pack_end(self._save_button)

        self._banner = Adw.Banner(revealed=False)
        self._banner.connect("button-clicked", lambda _b: self._apply_suggestion())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._banner)
        box.append(Gtk.ScrolledWindow(child=page, vexpand=True))

        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

    # ── nhập nhanh ──────────────────────────────────────────────────────────

    def _apply_quick(self) -> None:
        route, error = parse_route_line(self._quick.get_text())
        if error:
            self._show_banner(error, suggestion=None)
            return
        if route is None:
            return
        self._dest.set_text(route.dest)
        self._netmask.set_text(prefix_to_netmask(route.prefix))
        self._next_hop.set_text(route.next_hop or "")
        self._metric.set_value(-1 if route.metric is None else route.metric)
        self._table.set_value(route.table or 0)
        self._quick.set_text("")
        self._validate()

    # ── validate ────────────────────────────────────────────────────────────

    def _collect(self) -> Ipv4Route:
        metric = int(self._metric.get_value())
        table = int(self._table.get_value())
        prefix = netmask_to_prefix(self._netmask.get_text())
        return Ipv4Route(
            dest=self._dest.get_text().strip(),
            prefix=32 if prefix is None else prefix,
            next_hop=self._next_hop.get_text().strip() or None,
            metric=None if metric < 0 else metric,
            table=table or None,
            onlink=self._onlink.get_active(),
            enabled=self._original.enabled if self._original else True,
        )

    def _validate(self) -> None:
        # Hai luật riêng của form, không đặt ở domain: `parse_route_line` phải
        # đọc được dạng một dòng đang nằm trong file cấu hình, kể cả route
        # on-link không gateway.
        if netmask_to_prefix(self._netmask.get_text()) is None:
            self._save_button.set_sensitive(False)
            self._show_banner(
                f"'{self._netmask.get_text().strip()}' không phải netmask hợp lệ. "
                f"Ví dụ: {DEFAULT_NETMASK}, 255.255.0.0, 255.255.255.0 — hoặc gõ "
                "số prefix như 8.",
                suggestion=None,
            )
            return

        if not self._next_hop.get_text().strip() and not self._onlink.get_active():
            self._save_button.set_sensitive(False)
            self._show_banner(
                "Thiếu gateway. Điền địa chỉ gateway, hoặc bật On-link nếu đích "
                "nằm trực tiếp trên liên kết.",
                suggestion=None,
            )
            return

        result = validate_route(
            self._collect(),
            existing=self._existing,
            local_subnets=self._local_subnets,
            editing_identity=self._original.identity() if self._original else None,
        )
        self._save_button.set_sensitive(result.ok)

        if not result.issues:
            self._banner.set_revealed(False)
            self._suggestion = None
            return

        # Ưu tiên hiện lỗi chặn; hết lỗi rồi mới hiện cảnh báo.
        issue = (result.errors or result.warnings)[0]
        self._show_banner(
            issue.message,
            suggestion=issue.suggestion,
            warning=issue.severity is Severity.WARNING,
        )

    def _show_banner(self, text: str, *, suggestion: str | None, warning: bool = False) -> None:
        self._suggestion = suggestion
        self._banner.set_title(("⚠ " if warning else "") + text)
        # Lỗi "không phải địa chỉ mạng" luôn kèm giá trị sửa sẵn — cho bấm một
        # nút thay vì bắt người dùng tự tính lại.
        self._banner.set_button_label("Sửa giúp tôi" if suggestion else "")
        self._banner.set_revealed(True)

    def _apply_suggestion(self) -> None:
        if self._suggestion:
            self._dest.set_text(self._suggestion)
            self._validate()

    def _on_save_clicked(self) -> None:
        route = self._collect()
        if self._device_row is not None:
            iface = self._devices[self._device_row.get_selected()][0]
            self._on_save(route, iface)
        else:
            self._on_save(route)
        self.close()
