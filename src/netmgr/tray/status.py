"""Chọn icon, nhãn và dòng trạng thái cho tray từ trạng thái mạng (§5.1).

Thuần Python — nhận vào `NetworkSnapshot` và trả về mô tả những gì cần hiển
thị. Không đụng D-Bus, nên test được toàn bộ ma trận trạng thái.

QUAN TRỌNG — tooltip không dùng được trên GNOME:
    Extension `ubuntu-appindicators` KHÔNG hỗ trợ tooltip. Bằng chứng:
    `interfaces-xml/StatusNotifierItem.xml` comment out hẳn property `ToolTip`
    kèm ghi chú "we don't support tooltip", và không file .js nào của extension
    nhắc tới tooltip.

    Vì vậy `status_lines` mới là kênh thông tin thật — chúng được dựng thành các
    item disabled ở đầu menu. `tooltip` vẫn được sinh ra và export qua D-Bus vì
    host khác (KDE Plasma, waybar) có hỗ trợ, nhưng không được coi là nơi đặt
    thông tin thiết yếu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import ConnType, DeviceInfo

#: Icon symbolic để tự đổi màu theo theme sáng/tối của người dùng.
ICON_WIRED = "network-wired-symbolic"
ICON_WIRED_OFFLINE = "network-wired-disconnected-symbolic"
ICON_ACQUIRING = "network-wireless-acquiring-symbolic"
ICON_OFFLINE = "network-offline-symbolic"
ICON_WIFI_DISABLED = "network-wireless-disabled-symbolic"
ICON_WIFI_BY_BARS = {
    0: "network-wireless-signal-none-symbolic",
    1: "network-wireless-signal-weak-symbolic",
    2: "network-wireless-signal-ok-symbolic",
    3: "network-wireless-signal-good-symbolic",
    4: "network-wireless-signal-excellent-symbolic",
}

#: Emblem chồng lên icon nền khi proxy bật — dấu hiệu trực quan mà GNOME
#: không hề cho thấy ở đâu cả.
ICON_OVERLAY_PROXY = "changes-prevent-symbolic"

#: Nhãn cạnh icon bị cắt để không chiếm hết panel (§5.1).
MAX_LABEL_LEN = 12


@dataclass(slots=True)
class TrayStatus:
    icon_name: str
    #: Tiêu đề ngắn — cũng là fallback cho nhãn khi chưa có Bộ cấu hình.
    title: str
    #: Nguồn thông tin THẬT: dựng thành item disabled ở đầu menu.
    status_lines: list[str] = field(default_factory=list)
    #: Nhãn text cạnh icon. Hoạt động nhờ property XAyatanaLabel.
    label: str = ""
    overlay_icon: str = ""
    attention: bool = False
    #: Cho screen reader (appIndicator.js dùng IconAccessibleDesc).
    accessible_desc: str = ""

    @property
    def tooltip(self) -> str:
        """Chỉ dành cho host ngoài GNOME. Đừng đặt thông tin thiết yếu ở đây."""
        return "\n".join([self.title, *self.status_lines])


#: Cường độ Wi-Fi dao động từng giây (88 → 91 → 89…). Hiển thị số thô sẽ khiến
#: nội dung menu đổi liên tục, làm host phải tải lại menu không cần thiết. Làm
#: tròn về bội của 10 — không ai cần độ chính xác hơn thế.
STRENGTH_ROUNDING = 10


def _round_strength(value: int) -> int:
    return round(value / STRENGTH_ROUNDING) * STRENGTH_ROUNDING


def _device_summary(dev: DeviceInfo) -> str:
    ip = str(dev.ip4_addresses[0]) if dev.ip4_addresses else "chưa có IP"
    if dev.type is ConnType.WIFI and dev.wifi:
        w = dev.wifi
        name = w.ssid or dev.interface
        return f"{name} · {_round_strength(w.strength)}% · {ip}"
    return f"{dev.interface} · {ip}"


def _icon_for_device(dev: DeviceInfo) -> str:
    if dev.state.is_busy:
        return ICON_ACQUIRING
    if dev.type is ConnType.WIFI:
        bars = dev.wifi.signal_bars if dev.wifi else 0
        return ICON_WIFI_BY_BARS[bars]
    return ICON_WIRED if dev.is_connected else ICON_WIRED_OFFLINE


def _device_title(dev: DeviceInfo) -> str:
    if dev.type is ConnType.WIFI and dev.wifi and dev.wifi.ssid:
        return dev.wifi.ssid
    return dev.interface


def compute_status(
    snapshot,
    *,
    proxy_summary: str | None = None,
    profile_label: str | None = None,
    profile_drifted: bool = False,
    show_label: bool = True,
) -> TrayStatus:
    """Gộp mọi thứ thành thứ tray cần hiển thị.

    proxy_summary:   None = proxy tắt; có giá trị = đang bật, hiện emblem (§5.1).
    profile_label:   tên Bộ cấu hình đang active (F6) — ưu tiên làm nhãn tray.
    profile_drifted: cấu hình thực tế đã lệch khỏi Bộ cấu hình (FR-PR5).
    """
    lines: list[str] = []

    if not snapshot.networking_enabled:
        status = TrayStatus(
            icon_name=ICON_OFFLINE,
            title="Mạng đã tắt",
            status_lines=["NetworkManager đang tắt toàn bộ kết nối mạng"],
        )
        lines = list(status.status_lines)
    else:
        primary = snapshot.primary_device()
        if primary is None:
            if snapshot.has_wifi_hardware and not snapshot.wifi_enabled:
                status = TrayStatus(icon_name=ICON_WIFI_DISABLED, title="Wi-Fi đã tắt")
            else:
                status = TrayStatus(icon_name=ICON_OFFLINE, title="Chưa kết nối")
        elif primary.state.is_busy:
            status = TrayStatus(
                icon_name=ICON_ACQUIRING,
                title="Đang kết nối…",
            )
            lines = [_device_summary(primary)]
        else:
            status = TrayStatus(
                icon_name=_icon_for_device(primary),
                title=_device_title(primary),
            )
            lines = [
                _device_summary(d)
                for d in snapshot.devices
                if d.is_connected or d.state.is_busy
            ]

    if proxy_summary:
        status.overlay_icon = ICON_OVERLAY_PROXY
        lines.append(f"Proxy: {proxy_summary}")

    if profile_label:
        # KHÔNG thêm dòng "Bộ cấu hình: X" vào đây: submenu ngay bên dưới đã mang
        # đúng nhãn đó rồi, thêm nữa là lặp hai lần trong cùng một menu ngắn.
        status.attention = profile_drifted
        if profile_drifted:
            lines.append(f"Bộ cấu hình đã bị chỉnh sửa ngoài app")

    status.status_lines = lines
    status.accessible_desc = status.title
    if show_label:
        status.label = truncate(profile_label or status.title)
    return status


def truncate(text: str, limit: int = MAX_LABEL_LEN) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
