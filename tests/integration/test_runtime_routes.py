"""Test áp route Ở RUNTIME với NetworkManager thật — và chứng minh không ghi đĩa.

An toàn:
  * Route thử nghiệm dùng dải TEST-NET-2 (198.51.100.0/24, RFC 5737) — không có
    traffic thật nào đi tới đó.
  * Chỉ áp ở runtime, và fixture luôn khôi phục sau khi chạy.
  * Test kiểm tra cả việc cấu hình TRÊN ĐĨA không hề đổi — đây chính là lỗi đã
    làm hỏng máy người dùng: `update2(TO_DISK)` khiến NetworkManager ghi lại
    /etc/netplan/90-NM-*.yaml và làm mất `connection.interface-name`.
"""

from __future__ import annotations

import subprocess

import pytest

from netmgr.domain.models import Ipv4Route

pytestmark = pytest.mark.needs_nm

TEST_NET = "198.51.100.0"
TEST_PREFIX = 24


def pump(condition, timeout_ms: int = 8000) -> bool:
    from gi.repository import GLib

    loop = GLib.MainLoop()
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


def kernel_routes() -> str:
    return subprocess.run(
        ["ip", "route", "show"], capture_output=True, text=True
    ).stdout


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
    f.wait_for_permissions()
    yield f
    f.close()


@pytest.fixture
def target(facade):
    """Một device ethernet đang kết nối; khôi phục sau mỗi test."""
    snapshot = facade.snapshot()
    device = next(
        (d for d in snapshot.ethernet_devices if d.is_connected), None
    )
    if device is None:
        pytest.skip("Không có ethernet nào đang kết nối")

    yield device.interface

    done = []
    facade.restore_runtime(device.interface, done.append)
    pump(lambda: bool(done))


def apply_routes(facade, interface, routes, ignore_auto=True):
    results = []
    facade.apply_runtime_routes(interface, routes, ignore_auto, results.append)
    assert pump(lambda: bool(results)), "callback không đến"
    return results[0]


# ── áp và khôi phục ──────────────────────────────────────────────────────────


def test_route_appears_in_kernel(facade, target):
    result = apply_routes(facade, target, [Ipv4Route(TEST_NET, TEST_PREFIX, metric=9999)])
    assert result.ok, result.message
    assert pump(lambda: TEST_NET in kernel_routes()), "route không vào bảng kernel"


def test_restore_removes_route(facade, target):
    apply_routes(facade, target, [Ipv4Route(TEST_NET, TEST_PREFIX, metric=9999)])
    assert pump(lambda: TEST_NET in kernel_routes())

    done = []
    facade.restore_runtime(target, done.append)
    assert pump(lambda: bool(done))
    assert done[0].ok, done[0].message
    assert pump(lambda: TEST_NET not in kernel_routes()), "route vẫn còn sau khi khôi phục"


def test_disabled_route_not_applied(facade, target):
    apply_routes(
        facade, target, [Ipv4Route(TEST_NET, TEST_PREFIX, metric=9999, enabled=False)]
    )
    assert TEST_NET not in kernel_routes()


def test_unknown_interface_fails_cleanly(facade):
    results = []
    facade.apply_runtime_routes("khong-co-that0", [], True, results.append)
    assert pump(lambda: bool(results))
    assert results[0].ok is False
    assert "Không tìm thấy thiết bị" in results[0].message


def test_restore_unknown_interface_is_noop(facade):
    results = []
    facade.restore_runtime("khong-co-that0", results.append)
    assert pump(lambda: bool(results))
    assert results[0].ok is True


# ── KHÔNG ghi đĩa (đây là điểm mấu chốt) ─────────────────────────────────────


def stored_ipv4(facade, interface):
    """Cấu hình IPv4 ĐÃ LƯU của connection đang chạy trên interface."""
    snapshot = facade.snapshot()
    device = snapshot.device_by_interface(interface)
    conn = snapshot.connection_by_uuid(device.active_connection_uuid)
    return conn


def test_stored_config_unchanged_after_apply(facade, target):
    """Áp route runtime KHÔNG được đụng tới cấu hình đã lưu."""
    before = stored_ipv4(facade, target)
    before_routes = [str(r) for r in before.ipv4.routes]
    before_iface = before.interface_name

    apply_routes(facade, target, [Ipv4Route(TEST_NET, TEST_PREFIX, metric=9999)])
    pump(lambda: TEST_NET in kernel_routes())

    after = stored_ipv4(facade, target)
    assert [str(r) for r in after.ipv4.routes] == before_routes
    # Chính trường này đã bị mất trong sự cố netplan.
    assert after.interface_name == before_iface


def test_netplan_files_untouched(facade, target):
    """File /etc/netplan không được thay đổi — đây là gốc rễ của sự cố cũ."""
    import pathlib

    netplan = pathlib.Path("/etc/netplan")
    if not netplan.is_dir():
        pytest.skip("Máy không dùng netplan")

    def fingerprint():
        return sorted(
            (f.name, f.stat().st_mtime_ns) for f in netplan.glob("*.yaml")
        )

    before = fingerprint()
    apply_routes(facade, target, [Ipv4Route(TEST_NET, TEST_PREFIX, metric=9999)])
    pump(lambda: TEST_NET in kernel_routes())
    assert fingerprint() == before, "app đã ghi vào /etc/netplan"


def test_connection_count_unchanged(facade, target):
    before = {c.uuid for c in facade.snapshot().connections}
    apply_routes(facade, target, [Ipv4Route(TEST_NET, TEST_PREFIX, metric=9999)])
    assert {c.uuid for c in facade.snapshot().connections} == before
