"""Test luồng ghi IPv4/route với NetworkManager THẬT (§4.5).

An toàn: mọi thao tác diễn ra trên một connection tạm do test tự tạo, gắn với
interface KHÔNG TỒN TẠI (`netmgrtest0`) và `autoconnect=False`. Nó không bao giờ
kích hoạt được nên không thể ảnh hưởng tới mạng của máy. Fixture xoá nó sau khi
chạy, kể cả khi test thất bại.
"""

from __future__ import annotations

import pytest

from netmgr.domain.models import Ipv4Address, Ipv4Config, Ipv4Method, Ipv4Route

pytestmark = pytest.mark.needs_nm

TEST_PREFIX = "__netmgr_test__"
TEST_IFACE = "netmgrtest0"


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
    if not f.snapshot().permissions.can_edit_connections:
        pytest.skip("Không đủ quyền sửa cấu hình")
    yield f
    f.close()


@pytest.fixture
def scratch(facade):
    """Một connection dùng-một-lần, chắc chắn không kích hoạt được."""
    from gi.repository import GLib, NM

    client = facade._client
    conn = NM.SimpleConnection.new()
    s_con = NM.SettingConnection.new()
    s_con.set_property("id", f"{TEST_PREFIX}{NM.utils_uuid_generate()[:8]}")
    s_con.set_property("type", "802-3-ethernet")
    s_con.set_property("uuid", NM.utils_uuid_generate())
    s_con.set_property("interface-name", TEST_IFACE)
    s_con.set_property("autoconnect", False)
    conn.add_setting(s_con)
    conn.add_setting(NM.SettingWired.new())
    s_ip4 = NM.SettingIP4Config.new()
    s_ip4.set_property("method", "auto")
    conn.add_setting(s_ip4)

    created: dict = {}

    def on_added(cli, result):
        try:
            created["conn"] = cli.add_connection_finish(result)
        except GLib.Error as exc:
            created["error"] = exc.message
        created["done"] = True

    client.add_connection_async(conn, True, None, on_added)
    if not pump(lambda: created.get("done")):
        pytest.skip("Tạo connection test quá lâu")
    if "error" in created:
        pytest.skip(f"Không tạo được connection test: {created['error']}")

    uuid = created["conn"].get_uuid()
    yield uuid

    # Teardown: xoá theo uuid, im lặng nếu test đã tự xoá.
    remaining = client.get_connection_by_uuid(uuid)
    if remaining is not None:
        finished = {}
        remaining.delete_async(None, lambda c, r: finished.__setitem__("x", True))
        pump(lambda: finished.get("x"), 4000)


def save(facade, uuid, config, apply_now=False):
    results = []
    facade.save_ipv4(uuid, config, apply_now=apply_now, callback=results.append)
    assert pump(lambda: bool(results)), "callback không đến"
    return results[0]


def reload_ipv4(facade, uuid) -> Ipv4Config:
    conn = facade.snapshot().connection_by_uuid(uuid)
    assert conn is not None, "connection test biến mất"
    return conn.ipv4


# ── cơ bản ───────────────────────────────────────────────────────────────────


def test_scratch_connection_exists(facade, scratch):
    conn = facade.snapshot().connection_by_uuid(scratch)
    assert conn is not None
    assert conn.ipv4.method is Ipv4Method.AUTO


def test_save_unknown_uuid_fails(facade):
    result = save(facade, "00000000-0000-0000-0000-000000000000", Ipv4Config())
    assert result.ok is False
    assert "Không tìm thấy" in result.message


# ── route CRUD ───────────────────────────────────────────────────────────────


def test_add_single_route(facade, scratch):
    config = Ipv4Config(
        method=Ipv4Method.AUTO,
        routes=[Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1", metric=100)],
    )
    assert save(facade, scratch, config).ok

    routes = reload_ipv4(facade, scratch).routes
    assert len(routes) == 1
    assert routes[0].dest == "10.20.0.0"
    assert routes[0].prefix == 16
    assert routes[0].next_hop == "192.168.1.1"
    assert routes[0].metric == 100


def test_route_attributes_survive(facade, scratch):
    """table/src/onlink đi qua `NM.IPRoute.set_attribute` — dễ rơi mất nhất."""
    config = Ipv4Config(
        routes=[
            Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1", table=200, onlink=True)
        ]
    )
    assert save(facade, scratch, config).ok

    route = reload_ipv4(facade, scratch).routes[0]
    assert route.table == 200
    assert route.onlink is True


def test_add_multiple_routes_keeps_order(facade, scratch):
    config = Ipv4Config(
        routes=[
            Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1"),
            Ipv4Route("172.16.0.0", 12, next_hop="192.168.1.254", metric=50),
            Ipv4Route("10.99.0.0", 24),
        ]
    )
    assert save(facade, scratch, config).ok

    routes = reload_ipv4(facade, scratch).routes
    assert [r.cidr for r in routes] == ["10.20.0.0/16", "172.16.0.0/12", "10.99.0.0/24"]


def test_edit_route(facade, scratch):
    save(facade, scratch, Ipv4Config(routes=[Ipv4Route("10.20.0.0", 16, metric=100)]))
    save(facade, scratch, Ipv4Config(routes=[Ipv4Route("10.20.0.0", 16, metric=500)]))
    assert reload_ipv4(facade, scratch).routes[0].metric == 500


def test_delete_route(facade, scratch):
    save(
        facade, scratch,
        Ipv4Config(routes=[Ipv4Route("10.20.0.0", 16), Ipv4Route("172.16.0.0", 12)]),
    )
    save(facade, scratch, Ipv4Config(routes=[Ipv4Route("172.16.0.0", 12)]))

    routes = reload_ipv4(facade, scratch).routes
    assert [r.cidr for r in routes] == ["172.16.0.0/12"]


def test_clear_all_routes(facade, scratch):
    save(facade, scratch, Ipv4Config(routes=[Ipv4Route("10.20.0.0", 16)]))
    save(facade, scratch, Ipv4Config(routes=[]))
    assert reload_ipv4(facade, scratch).routes == []


def test_disabled_route_is_not_written(facade, scratch):
    """FR-R5 — `enabled` là metadata của app, NM không có khái niệm đó."""
    config = Ipv4Config(
        routes=[
            Ipv4Route("10.20.0.0", 16),
            Ipv4Route("172.16.0.0", 12, enabled=False),
        ]
    )
    assert save(facade, scratch, config).ok

    routes = reload_ipv4(facade, scratch).routes
    assert [r.cidr for r in routes] == ["10.20.0.0/16"]


def test_onlink_route_without_gateway(facade, scratch):
    assert save(facade, scratch, Ipv4Config(routes=[Ipv4Route("10.99.0.0", 24)])).ok
    assert reload_ipv4(facade, scratch).routes[0].next_hop is None


def test_default_route_accepted(facade, scratch):
    config = Ipv4Config(routes=[Ipv4Route("0.0.0.0", 0, next_hop="192.168.1.1")])
    assert save(facade, scratch, config).ok
    assert reload_ipv4(facade, scratch).routes[0].is_default


# ── chế độ Automatic / Manual (F5) ───────────────────────────────────────────


def test_switch_to_manual(facade, scratch):
    config = Ipv4Config(
        method=Ipv4Method.MANUAL,
        addresses=[Ipv4Address("10.0.5.20", 24)],
        gateway="10.0.5.1",
        dns=["1.1.1.1", "8.8.8.8"],
    )
    assert save(facade, scratch, config).ok

    ip4 = reload_ipv4(facade, scratch)
    assert ip4.method is Ipv4Method.MANUAL
    assert str(ip4.addresses[0]) == "10.0.5.20/24"
    assert ip4.gateway == "10.0.5.1"
    assert ip4.dns == ["1.1.1.1", "8.8.8.8"]


def test_switch_back_to_automatic_clears_static_address(facade, scratch):
    save(
        facade, scratch,
        Ipv4Config(
            method=Ipv4Method.MANUAL,
            addresses=[Ipv4Address("10.0.5.20", 24)],
            gateway="10.0.5.1",
        ),
    )
    assert save(facade, scratch, Ipv4Config(method=Ipv4Method.AUTO)).ok

    ip4 = reload_ipv4(facade, scratch)
    assert ip4.method is Ipv4Method.AUTO
    assert ip4.gateway is None


def test_routes_survive_method_change(facade, scratch):
    """FR-A4 — `ipv4.routes` độc lập với `ipv4.method`; nhiều người hiểu nhầm."""
    route = Ipv4Route("10.20.0.0", 16, next_hop="192.168.1.1")
    save(
        facade, scratch,
        Ipv4Config(
            method=Ipv4Method.MANUAL,
            addresses=[Ipv4Address("10.0.5.20", 24)],
            gateway="10.0.5.1",
            routes=[route],
        ),
    )
    save(facade, scratch, Ipv4Config(method=Ipv4Method.AUTO, routes=[route]))

    ip4 = reload_ipv4(facade, scratch)
    assert ip4.method is Ipv4Method.AUTO
    assert [r.cidr for r in ip4.routes] == ["10.20.0.0/16"]


def test_manual_without_address_is_rejected_before_dbus(facade, scratch):
    """`verify()` phải bắt lỗi này, không để nó thành lỗi D-Bus khó hiểu."""
    result = save(facade, scratch, Ipv4Config(method=Ipv4Method.MANUAL))
    assert result.ok is False
    assert "không hợp lệ" in result.message
    # Cấu hình cũ phải còn nguyên
    assert reload_ipv4(facade, scratch).method is Ipv4Method.AUTO


# ── cờ IPv4 ──────────────────────────────────────────────────────────────────


def test_ipv4_flags_roundtrip(facade, scratch):
    config = Ipv4Config(
        method=Ipv4Method.AUTO,
        ignore_auto_dns=True,
        ignore_auto_routes=True,
        never_default=True,
        may_fail=False,
        route_metric=700,
    )
    assert save(facade, scratch, config).ok

    ip4 = reload_ipv4(facade, scratch)
    assert ip4.ignore_auto_dns is True
    assert ip4.ignore_auto_routes is True
    assert ip4.never_default is True
    assert ip4.may_fail is False
    assert ip4.route_metric == 700


def test_flags_can_be_turned_off_again(facade, scratch):
    save(facade, scratch, Ipv4Config(ignore_auto_routes=True, never_default=True))
    save(facade, scratch, Ipv4Config())

    ip4 = reload_ipv4(facade, scratch)
    assert ip4.ignore_auto_routes is False
    assert ip4.never_default is False


def test_dns_search_roundtrip(facade, scratch):
    config = Ipv4Config(dns=["1.1.1.1"], dns_search=["viettel.vn", "digital.vn"])
    assert save(facade, scratch, config).ok
    assert reload_ipv4(facade, scratch).dns_search == ["viettel.vn", "digital.vn"]


# ── ngữ nghĩa apply ──────────────────────────────────────────────────────────


def test_save_without_apply_reports_deferred(facade, scratch):
    result = save(facade, scratch, Ipv4Config(), apply_now=False)
    assert result.ok
    assert "lần kết nối sau" in result.message


def test_save_with_apply_on_inactive_connection_just_saves(facade, scratch):
    """Không có device nào chạy profile này thì không có gì để reapply."""
    result = save(facade, scratch, Ipv4Config(), apply_now=True)
    assert result.ok
    assert result.message == "Đã lưu"


def test_write_does_not_touch_other_connections(facade, scratch):
    before = {c.uuid for c in facade.snapshot().connections}
    save(facade, scratch, Ipv4Config(routes=[Ipv4Route("10.20.0.0", 16)]))
    assert {c.uuid for c in facade.snapshot().connections} == before
