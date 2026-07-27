"""Test cho việc chọn icon/nhãn của tray — ma trận trạng thái ở §5.1."""

from __future__ import annotations

import pytest

from netmgr.domain.models import DeviceState
from netmgr.tray import status as st
from netmgr.tray.status import compute_status, truncate

from .factories import eth_device, snapshot, wifi_device


# ── không có mạng ────────────────────────────────────────────────────────────


def test_networking_disabled_shows_offline_icon():
    s = compute_status(snapshot(networking_enabled=False))
    assert s.icon_name == st.ICON_OFFLINE
    assert "tắt" in s.title.lower()


def test_no_devices_shows_offline():
    assert compute_status(snapshot()).icon_name == st.ICON_OFFLINE


def test_wifi_hardware_present_but_radio_off():
    s = compute_status(
        snapshot(
            devices=[wifi_device(state=DeviceState.UNAVAILABLE, ip=None)],
            wifi_enabled=False,
        )
    )
    assert s.icon_name == st.ICON_WIFI_DISABLED
    assert s.title == "Wi-Fi đã tắt"


def test_disconnected_device_shows_not_connected():
    s = compute_status(
        snapshot(devices=[wifi_device(state=DeviceState.DISCONNECTED, ip=None)])
    )
    assert s.icon_name == st.ICON_OFFLINE
    assert s.title == "Chưa kết nối"


# ── đang kết nối ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", [DeviceState.CONNECTING, DeviceState.DEACTIVATING])
def test_busy_device_shows_acquiring(state):
    s = compute_status(snapshot(devices=[wifi_device(state=state)]))
    assert s.icon_name == st.ICON_ACQUIRING
    assert "Đang kết nối" in s.title


# ── Wi-Fi theo cường độ ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "strength,expected",
    [
        (95, st.ICON_WIFI_BY_BARS[4]),
        (60, st.ICON_WIFI_BY_BARS[3]),
        (40, st.ICON_WIFI_BY_BARS[2]),
        (10, st.ICON_WIFI_BY_BARS[1]),
        (0, st.ICON_WIFI_BY_BARS[0]),
    ],
)
def test_wifi_icon_follows_signal_strength(strength, expected):
    s = compute_status(snapshot(devices=[wifi_device(strength=strength)]))
    assert s.icon_name == expected


def test_wifi_title_is_ssid():
    s = compute_status(snapshot(devices=[wifi_device(ssid="Viettel Digital")]))
    assert s.title == "Viettel Digital"


def test_wifi_without_ap_info_falls_back_to_interface():
    dev = wifi_device()
    dev.wifi = None
    s = compute_status(snapshot(devices=[dev]))
    assert s.title == dev.interface


# ── ethernet ─────────────────────────────────────────────────────────────────


def test_connected_ethernet_icon():
    s = compute_status(snapshot(devices=[eth_device()]))
    assert s.icon_name == st.ICON_WIRED


def test_ethernet_preferred_over_wifi_as_primary():
    """Cắm cáp thì icon phải là icon có dây, dù Wi-Fi cũng đang nối."""
    s = compute_status(snapshot(devices=[wifi_device(), eth_device()]))
    assert s.icon_name == st.ICON_WIRED


def test_unplugged_ethernet_is_not_primary():
    s = compute_status(
        snapshot(
            devices=[
                eth_device(state=DeviceState.UNAVAILABLE, carrier=False, ip=None),
                wifi_device(),
            ]
        )
    )
    assert s.icon_name == st.ICON_WIFI_BY_BARS[4]


# ── dòng trạng thái (thay cho tooltip) ───────────────────────────────────────


def test_status_lines_include_every_connected_device():
    s = compute_status(snapshot(devices=[wifi_device(), eth_device()]))
    assert len(s.status_lines) == 2


def test_status_line_contains_ip_and_signal():
    s = compute_status(snapshot(devices=[wifi_device(ssid="X", strength=70)]))
    line = s.status_lines[0]
    assert "X" in line and "70%" in line and "192.168.1.42" in line


@pytest.mark.parametrize("raw,shown", [(88, "90%"), (91, "90%"), (74, "70%"), (75, "80%")])
def test_signal_strength_rounded_to_keep_menu_stable(raw, shown):
    """Số thô dao động từng giây sẽ khiến host phải tải lại menu liên tục."""
    s = compute_status(snapshot(devices=[wifi_device(strength=raw)]))
    assert shown in s.status_lines[0]


def test_device_without_ip_says_so():
    s = compute_status(snapshot(devices=[wifi_device(ip=None)]))
    assert "chưa có IP" in s.status_lines[0]


def test_disconnected_devices_excluded_from_status_lines():
    s = compute_status(
        snapshot(
            devices=[wifi_device(), eth_device(state=DeviceState.DISCONNECTED, ip=None)]
        )
    )
    assert len(s.status_lines) == 1


# ── proxy ────────────────────────────────────────────────────────────────────


def test_no_proxy_means_no_overlay():
    s = compute_status(snapshot(devices=[wifi_device()]))
    assert s.overlay_icon == ""


def test_proxy_adds_overlay_and_status_line():
    s = compute_status(snapshot(devices=[wifi_device()]), proxy_summary="10.0.0.8:3128")
    assert s.overlay_icon == st.ICON_OVERLAY_PROXY
    assert any("Proxy" in line for line in s.status_lines)


# ── Bộ cấu hình ──────────────────────────────────────────────────────────────


def test_profile_label_becomes_tray_label():
    s = compute_status(snapshot(devices=[wifi_device()]), profile_label="Nhà")
    assert s.label == "Nhà"


def test_profile_line_comes_first():
    s = compute_status(snapshot(devices=[wifi_device()]), profile_label="Công ty")
    assert s.status_lines[0].startswith("Bộ cấu hình:")


def test_drifted_profile_raises_attention():
    s = compute_status(
        snapshot(devices=[wifi_device()]), profile_label="Công ty", profile_drifted=True
    )
    assert s.attention is True
    assert "đã chỉnh sửa" in s.status_lines[0]


def test_no_drift_means_no_attention():
    s = compute_status(snapshot(devices=[wifi_device()]), profile_label="Công ty")
    assert s.attention is False


# ── nhãn ─────────────────────────────────────────────────────────────────────


def test_label_falls_back_to_title_without_profile():
    s = compute_status(snapshot(devices=[wifi_device(ssid="ShortSSID")]))
    assert s.label == "ShortSSID"


def test_label_can_be_disabled():
    s = compute_status(snapshot(devices=[wifi_device()]), show_label=False)
    assert s.label == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ngắn", "ngắn"),
        ("a" * 12, "a" * 12),
        ("a" * 13, "a" * 11 + "…"),
        ("", ""),
    ],
)
def test_truncate(text, expected):
    assert truncate(text) == expected


def test_long_ssid_is_truncated_in_label():
    s = compute_status(snapshot(devices=[wifi_device(ssid="Mạng Wifi Rất Dài Của Công Ty")]))
    assert len(s.label) <= st.MAX_LABEL_LEN
    assert s.label.endswith("…")


def test_accessible_desc_is_set():
    """appIndicator.js dùng IconAccessibleDesc cho screen reader."""
    s = compute_status(snapshot(devices=[wifi_device(ssid="X")]))
    assert s.accessible_desc == "X"


# ── tooltip (chỉ dành cho host ngoài GNOME) ──────────────────────────────────


def test_tooltip_combines_title_and_lines():
    s = compute_status(snapshot(devices=[wifi_device()]))
    assert s.tooltip.splitlines()[0] == s.title
    assert len(s.tooltip.splitlines()) == 1 + len(s.status_lines)
