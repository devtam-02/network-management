"""Smoke test cho NMFacade với NetworkManager THẬT.

Chỉ đọc, không ghi gì — an toàn để chạy trên máy dev. Bỏ qua tự động nếu không
có NM hoặc không có typelib (vd: trong CI).

    pytest tests/integration -m needs_nm
"""

from __future__ import annotations

import pytest

from netmgr.domain.models import ConnType, DeviceState, Ipv4Method, RouteSource

pytestmark = pytest.mark.needs_nm


@pytest.fixture(scope="module")
def facade():
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("NM", "1.0")
    except ValueError:
        pytest.skip("Không có typelib NM-1.0")

    from netmgr.infra.nm_facade import NMFacade, NMUnavailableError

    try:
        f = NMFacade()
    except NMUnavailableError as exc:
        pytest.skip(f"NetworkManager không khả dụng: {exc}")

    yield f
    f.close()


@pytest.fixture(scope="module")
def snap(facade):
    facade.wait_for_permissions()
    return facade.snapshot()


def test_nm_version_is_reported(facade):
    assert facade.nm_version and facade.nm_version != "?"


def test_snapshot_has_devices(snap):
    assert snap.devices, "Máy nào cũng phải có ít nhất một NIC được NM quản lý"


def test_devices_are_only_ethernet_or_wifi(snap):
    """`_managed_devices` phải lọc bỏ lo/docker0/veth."""
    assert all(d.type in (ConnType.ETHERNET, ConnType.WIFI) for d in snap.devices)
    assert all(d.interface not in ("lo", "docker0") for d in snap.devices)


def test_device_fields_are_sane(snap):
    for d in snap.devices:
        assert d.interface
        assert isinstance(d.state, DeviceState)
        for addr in d.ip4_addresses:
            assert 0 <= addr.prefix <= 32
            assert addr.address.count(".") == 3


def test_runtime_routes_are_marked_non_editable(snap):
    """Route đọc từ device là runtime → phải là DHCP, UI không cho sửa (FR-R1)."""
    for d in snap.devices:
        for route in d.ip4_routes:
            assert route.source is RouteSource.DHCP
            assert not route.editable


def test_saved_routes_are_marked_static(snap):
    for conn in snap.connections:
        for route in conn.ipv4.routes:
            assert route.source is RouteSource.STATIC
            assert route.editable


def test_connection_fields_are_sane(snap):
    assert snap.connections
    for c in snap.connections:
        assert c.uuid and len(c.uuid) == 36
        assert isinstance(c.ipv4.method, Ipv4Method)


def test_wifi_connections_have_ssid(snap):
    for c in snap.saved_wifi_connections():
        assert c.ssid, f"Wi-Fi connection {c.id} thiếu SSID"


def test_active_connection_matches_device(snap):
    """UUID active trên device phải trỏ tới một connection có thật."""
    for d in snap.devices:
        if d.active_connection_uuid:
            assert snap.connection_by_uuid(d.active_connection_uuid) is not None


def test_connected_wifi_device_exposes_status(snap):
    for d in snap.wifi_devices:
        if d.is_connected:
            assert d.wifi is not None
            assert 0 <= d.wifi.strength <= 100
            assert 0 <= d.wifi.signal_bars <= 4


def test_permissions_load_after_main_loop(facade, snap):
    assert snap.permissions.loaded, "wait_for_permissions() phải nạp xong permission"


def test_primary_device_is_connected_when_online(snap):
    primary = snap.primary_device()
    if any(d.is_connected for d in snap.devices):
        assert primary is not None and primary.is_connected


def test_snapshot_is_repeatable(facade):
    """Gọi hai lần liên tiếp phải ra cùng tập interface — không rò rỉ trạng thái."""
    a = {d.interface for d in facade.snapshot().devices}
    b = {d.interface for d in facade.snapshot().devices}
    assert a == b


# ── đường ghi ────────────────────────────────────────────────────────────────
#
# Các test dưới đây cố tình KHÔNG làm thay đổi trạng thái mạng của máy dev:
# hoặc là set lại đúng giá trị đang có, hoặc là thao tác trên uuid không tồn tại.


def pump(condition, timeout_ms: int = 5000) -> bool:
    """Chạy main loop tới khi `condition()` đúng hoặc hết giờ.

    Cần thiết vì thao tác ghi là async: callback chỉ đến khi main loop chạy.
    """
    from gi.repository import GLib

    loop = GLib.MainLoop()
    # Theo dõi source nào đã tự gỡ; gọi source_remove lên source đã gỡ sẽ sinh
    # cảnh báo của GLib chứ không phải exception, nên try/except không chặn được.
    alive = {"tick": True, "timeout": True}

    def tick() -> bool:
        if condition():
            alive["tick"] = False
            loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def on_timeout() -> bool:
        alive["timeout"] = False
        loop.quit()
        return GLib.SOURCE_REMOVE

    tick_id = GLib.timeout_add(20, tick)
    timeout_id = GLib.timeout_add(timeout_ms, on_timeout)
    loop.run()

    if alive["tick"]:
        GLib.source_remove(tick_id)
    if alive["timeout"]:
        GLib.source_remove(timeout_id)
    return not alive["tick"]


def test_set_wifi_enabled_to_current_value_succeeds(facade, snap):
    """Chứng minh đường ghi chạy được mà không làm rớt Wi-Fi đang dùng."""
    results = []
    facade.set_wifi_enabled(snap.wifi_enabled, results.append)

    assert pump(lambda: bool(results)), "callback không đến trong 5 giây"
    assert results[0].ok, results[0].message
    assert facade.snapshot().wifi_enabled == snap.wifi_enabled


def test_activate_unknown_uuid_fails_cleanly(facade):
    """Lỗi phải thành OpResult, không được ném GLib.Error ra ngoài facade."""
    results = []
    facade.activate_connection(
        "00000000-0000-0000-0000-000000000000", None, results.append
    )

    assert len(results) == 1
    assert results[0].ok is False
    assert "Không tìm thấy" in results[0].message


def test_deactivate_inactive_connection_is_idempotent(facade):
    """Đích đến (không kết nối) đã đạt được → coi là thành công, không phải lỗi."""
    results = []
    facade.deactivate_connection("00000000-0000-0000-0000-000000000000", results.append)

    assert len(results) == 1
    assert results[0].ok is True


def test_write_ops_tolerate_missing_callback(facade, snap):
    """Không truyền callback thì cũng không được nổ."""
    facade.set_wifi_enabled(snap.wifi_enabled)
    facade.deactivate_connection("00000000-0000-0000-0000-000000000000")
