"""Test luồng áp dụng Bộ cấu hình 9 bước (§3.5.4).

Phủ hết các bước và mọi điểm có thể thất bại — theo yêu cầu ở §6.4: "inject lỗi
ở từng bước → kiểm tra rollback đúng".
"""

from __future__ import annotations

import pytest

from netmgr.domain.models import (
    Binding,
    BindingAction,
    DeviceState,
    ProxyConfig,
    ProxyMode,
    StepStatus,
)
from netmgr.infra.profile_store import new_profile
from netmgr.services.profile_applier import STEP_DEFS, ProfileApplier

from .factories import eth_conn, eth_device, snapshot, wifi_conn, wifi_device
from .fakes import FakeNetwork, FakeProxyService, ImmediateScheduler


def proxy_config(cid="p1", name="Công ty"):
    return ProxyConfig(id=cid, name=name, mode=ProxyMode.MANUAL)


@pytest.fixture
def world():
    """Máy có 1 ethernet + 1 wifi, đang nối Wi-Fi."""
    wifi_dev = wifi_device()
    eth_dev = eth_device(state=DeviceState.DISCONNECTED, ip=None)
    lan = eth_conn("LAN Công ty")
    wifi_home = wifi_conn("WiFi Nhà", active=True)
    wifi_dev.active_connection_uuid = wifi_home.uuid

    snap = snapshot(devices=[eth_dev, wifi_dev], connections=[lan, wifi_home])
    network = FakeNetwork(snap)
    proxy = FakeProxyService([proxy_config()])
    return network, proxy, lan, wifi_home


def run(applier, profile):
    result = {}
    progress = []
    applier.apply(
        profile,
        on_done=lambda r: result.update(report=r),
        on_progress=lambda r: progress.append(
            [(s.key, s.status) for s in r.steps]
        ),
    )
    return result["report"], progress


def make_applier(network, proxy, **kw):
    return ProfileApplier(network, proxy, schedule=ImmediateScheduler(), **kw)


# ── đường thành công ─────────────────────────────────────────────────────────


def test_apply_switches_connection_and_proxy(world):
    network, proxy, lan, wifi_home = world
    profile = new_profile(
        "Công ty",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),
            Binding("wlp2s0", BindingAction.DISCONNECT),
        ],
        proxy_id="p1",
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok, report.summary()
    assert lan.is_active is True
    assert wifi_home.is_active is False
    assert proxy.active.id == "p1"


def test_all_steps_reported(world):
    network, proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    report, _ = run(make_applier(network, proxy), profile)

    assert [s.key for s in report.steps] == [k for k, _l in STEP_DEFS]
    assert all(s.finished for s in report.steps)


def test_untouched_steps_are_skipped(world):
    network, proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    report, _ = run(make_applier(network, proxy), profile)

    assert report.step("radio_on").status is StepStatus.SKIPPED
    assert report.step("radio_off").status is StepStatus.SKIPPED
    assert report.step("proxy").status is StepStatus.SKIPPED


def test_progress_is_emitted_per_step(world):
    network, proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    _report, progress = run(make_applier(network, proxy), profile)
    assert len(progress) >= len(STEP_DEFS)


def test_leave_alone_binding_does_nothing(world):
    network, proxy, _lan, wifi_home = world
    profile = new_profile("X", bindings=[Binding("wlp2s0", BindingAction.LEAVE_ALONE)])
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok
    assert wifi_home.is_active is True
    assert network.operations() == []


# ── thứ tự bước (§3.5.4) ─────────────────────────────────────────────────────


def test_radio_turned_on_before_activating(world):
    """Bật radio sau khi kết nối thì kết nối Wi-Fi không thể thành công."""
    network, proxy, _lan, wifi_home = world
    # Trạng thái nhất quán: radio tắt ⇒ device không khả dụng, connection rời ra.
    snap = network._snapshot
    snap.wifi_enabled = False
    wifi_dev = snap.wifi_devices[0]
    wifi_dev.state = DeviceState.UNAVAILABLE
    wifi_dev.active_connection_uuid = None
    wifi_home.is_active = False

    profile = new_profile(
        "X",
        bindings=[Binding("wlp2s0", BindingAction.ACTIVATE, wifi_home.uuid)],
        wifi_enabled=True,
    )
    run(make_applier(network, proxy), profile)

    ops = network.operations()
    assert ops.index("wifi") < ops.index("activate")


def test_radio_turned_off_last(world):
    """Tắt radio sớm sẽ cắt mất kết nối đang dùng ở các bước trên."""
    network, proxy, lan, _ = world
    profile = new_profile(
        "Offline",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        wifi_enabled=False,
    )
    run(make_applier(network, proxy), profile)

    ops = network.operations()
    assert ops.index("activate") < ops.index("wifi")


def test_proxy_applied_before_network_changes(world):
    """Proxy không phụ thuộc mạng — làm trước phần dễ hỏng."""
    network, proxy, lan, _ = world
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        proxy_id="p1",
    )
    run(make_applier(network, proxy), profile)

    assert proxy.calls[0] == ("activate", "p1")
    assert network.operations()[0] == "activate"     # mạng đụng sau proxy


def test_disconnect_happens_before_activate(world):
    network, proxy, lan, wifi_home = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("wlp2s0", BindingAction.DISCONNECT),
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),
        ],
    )
    run(make_applier(network, proxy), profile)

    ops = network.operations()
    assert ops.index("deactivate") < ops.index("activate")


# ── validate ─────────────────────────────────────────────────────────────────


def test_missing_required_device_fails(world):
    network, proxy, _lan, _ = world
    profile = new_profile(
        "X",
        bindings=[Binding("eth-không-có", BindingAction.ACTIVATE, "u", required=True)],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok
    assert report.step("validate").status is StepStatus.FAILED
    assert "Không tìm thấy thiết bị" in report.step("validate").detail


def test_missing_optional_device_is_ignored(world):
    """Laptop rời dock không được biến thành lỗi (§3.5.2)."""
    network, proxy, lan, _ = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("dock-eth", BindingAction.ACTIVATE, "u", required=False),
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),
        ],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok, report.summary()
    assert lan.is_active


def test_binding_without_connection_lets_nm_choose(world):
    """Bật thiết bị mà không chỉ định connection — như khi cắm cáp bình thường."""
    network, proxy, _lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, None)])
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok, report.summary()
    assert ("activate", None) in network.calls
    assert network.last_activate_interface == "enp3s0"


def test_deleted_connection_fails_validation(world):
    network, proxy, _lan, _ = world
    profile = new_profile(
        "X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, "uuid-đã-xoá")]
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok
    assert "không còn tồn tại" in report.step("validate").detail


def test_deleted_proxy_fails_validation(world):
    network, proxy, lan, _ = world
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        proxy_id="proxy-đã-xoá",
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok
    assert "proxy không còn tồn tại" in report.step("validate").detail.lower()


def test_validation_failure_changes_nothing(world):
    network, proxy, _lan, wifi_home = world
    profile = new_profile(
        "X", bindings=[Binding("eth0", BindingAction.ACTIVATE, "u", required=True)]
    )
    run(make_applier(network, proxy), profile)

    assert network.operations() == []
    assert wifi_home.is_active is True


# ── rollback ─────────────────────────────────────────────────────────────────


def test_activate_failure_triggers_rollback(world):
    network, proxy, lan, wifi_home = world
    network.fail["activate"] = "thiết bị bận"
    profile = new_profile(
        "X",
        bindings=[
            Binding("wlp2s0", BindingAction.DISCONNECT),
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),
        ],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok
    assert report.rolled_back
    assert "thiết bị bận" in report.summary()


def test_rollback_restores_previous_connections(world):
    network, proxy, lan, wifi_home = world
    network.fail["activate"] = "hỏng"
    profile = new_profile(
        "X",
        bindings=[
            Binding("wlp2s0", BindingAction.DISCONNECT),
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),
        ],
    )
    run(make_applier(network, proxy), profile)

    # Wi-Fi đã bị ngắt ở bước 5, rollback phải bật lại
    assert ("activate", wifi_home.uuid) in network.calls


def test_rollback_restores_proxy(world):
    network, proxy, lan, _ = world
    proxy.activate("p1")
    proxy.calls.clear()
    network.fail["activate"] = "hỏng"

    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        proxy_id="",           # profile muốn tắt proxy
    )
    run(make_applier(network, proxy), profile)

    assert ("disable", None) in proxy.calls        # đã tắt theo profile
    assert ("activate", "p1") in proxy.calls       # rollback bật lại
    assert proxy.active.id == "p1"


def test_rollback_restores_wifi_radio(world):
    network, proxy, lan, _ = world
    network.fail["activate"] = "hỏng"
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        wifi_enabled=False,
    )
    run(make_applier(network, proxy), profile)

    # radio_off chạy SAU activate nên chưa kịp tắt; radio phải còn nguyên
    assert network._snapshot.wifi_enabled is True


def test_proxy_failure_triggers_rollback(world):
    network, proxy, lan, _ = world
    proxy.fail = True
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        proxy_id="p1",
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok and report.rolled_back
    assert report.step("proxy").status is StepStatus.FAILED
    # Mạng chưa bị đụng vì proxy chạy trước
    assert "activate" not in network.operations()


def test_radio_failure_triggers_rollback(world):
    network, proxy, lan, _ = world
    network._snapshot.wifi_enabled = False
    network.fail["wifi"] = "rfkill chặn"
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        wifi_enabled=True,
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok and report.rolled_back
    assert report.step("radio_on").status is StepStatus.FAILED


def test_deactivate_failure_triggers_rollback(world):
    network, proxy, lan, _ = world
    network.fail["deactivate"] = "không ngắt được"
    profile = new_profile(
        "X",
        bindings=[
            Binding("wlp2s0", BindingAction.DISCONNECT),
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),
        ],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok and report.rolled_back
    assert report.step("deactivate").status is StepStatus.FAILED


def test_successful_steps_marked_rolled_back(world):
    network, proxy, lan, _ = world
    network.fail["activate"] = "hỏng"
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
        proxy_id="p1",
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.step("proxy").status is StepStatus.ROLLED_BACK
    assert report.step("snapshot").status is StepStatus.ROLLED_BACK


# ── verify ───────────────────────────────────────────────────────────────────


def test_already_correct_connection_is_not_reactivated(world):
    """Áp dụng lại chính Bộ cấu hình đang chạy không được làm rớt mạng."""
    network, proxy, _lan, wifi_home = world
    profile = new_profile(
        "X", bindings=[Binding("wlp2s0", BindingAction.ACTIVATE, wifi_home.uuid)]
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok
    assert "activate" not in network.operations()
    assert "đã đúng sẵn" in report.step("activate").detail


def test_partially_correct_only_activates_what_changed(world):
    network, proxy, lan, wifi_home = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("wlp2s0", BindingAction.ACTIVATE, wifi_home.uuid),   # đã đúng
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),         # cần bật
        ],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok
    assert network.calls.count(("activate", lan.uuid)) == 1
    assert ("activate", wifi_home.uuid) not in network.calls


def test_verify_waits_for_connected_state(world):
    network, proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    report, _ = run(make_applier(network, proxy), profile)

    assert report.step("verify").status is StepStatus.OK


def test_hanging_activation_times_out_and_rolls_back(world):
    """`activate` trả về khi NM NHẬN yêu cầu, chưa phải khi kết nối xong."""
    network, proxy, lan, _ = world
    network.activation_hangs = True
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])

    applier = ProfileApplier(network, proxy, schedule=ImmediateScheduler(limit=200))
    report, _ = run(applier, profile)

    assert not report.ok and report.rolled_back
    assert report.step("verify").status is StepStatus.FAILED
    assert "Hết thời gian" in report.step("verify").detail


# ── đồng thời ────────────────────────────────────────────────────────────────


def test_second_apply_while_busy_is_rejected(world):
    network, proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])

    applier = make_applier(network, proxy)
    applier._busy = True                      # mô phỏng đang chạy
    report, _ = run(applier, profile)

    assert not report.ok
    assert "đang áp dụng" in report.step("validate").detail.lower()


def test_busy_flag_cleared_after_success(world):
    network, proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    applier = make_applier(network, proxy)
    run(applier, profile)
    assert applier.busy is False


def test_busy_flag_cleared_after_rollback(world):
    network, proxy, lan, _ = world
    network.fail["activate"] = "hỏng"
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    applier = make_applier(network, proxy)
    run(applier, profile)
    assert applier.busy is False


# ── route riêng của Bộ cấu hình (chỉ runtime) ────────────────────────────────


def route(dest="10.0.0.0", prefix=8, **kw):
    from netmgr.domain.models import Ipv4Route

    return Ipv4Route(dest, prefix, **kw)


def test_routes_applied_to_right_interface(world):
    network, proxy, lan, _ = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid,
                    routes=[route(next_hop="10.0.5.1")])
        ],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok, report.summary()
    assert ("routes", "enp3s0") in network.calls
    assert [r.cidr for r in network.runtime_routes["enp3s0"]] == ["10.0.0.0/8"]


def test_routes_applied_after_connection_is_up(world):
    """Áp route trước khi thiết bị lên là vô nghĩa."""
    network, proxy, lan, _ = world
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid, routes=[route()])],
    )
    run(make_applier(network, proxy), profile)

    ops = network.operations()
    assert ops.index("activate") < ops.index("routes")


def test_khong_co_route_rieng_thi_dua_ve_automatic(world):
    """Không route riêng nghĩa là "để DHCP lo", nên phải BẬT lại Automatic.

    Bỏ qua bước này thì thiết bị từng bị tắt Automatic sẽ giữ nguyên trạng thái
    đó, và Bộ cấu hình không còn quyết định được bảng route của mình.
    """
    network, proxy, lan, _ = world
    profile = new_profile(
        "X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)]
    )
    report, _ = run(make_applier(network, proxy), profile)
    assert report.step("routes").status is StepStatus.OK
    assert ("routes", "enp3s0") in network.calls
    assert network.last_ignore_auto is False


def test_thiet_bi_bi_ngat_thi_khong_dung_den_route(world):
    network, proxy, _lan, _ = world
    profile = new_profile(
        "X", bindings=[Binding("enp3s0", BindingAction.DISCONNECT)]
    )
    report, _ = run(make_applier(network, proxy), profile)
    assert report.step("routes").status is StepStatus.SKIPPED
    assert "routes" not in network.operations()


def test_leave_alone_khong_route_thi_khong_dung_den(world):
    """"Không đụng tới" phải đúng nghĩa không đụng tới."""
    network, proxy, _lan, _ = world
    profile = new_profile(
        "X", bindings=[Binding("enp3s0", BindingAction.LEAVE_ALONE)]
    )
    report, _ = run(make_applier(network, proxy), profile)
    assert report.step("routes").status is StepStatus.SKIPPED


def test_route_failure_rolls_back(world):
    network, proxy, lan, _ = world
    network.fail["routes"] = "reapply bị từ chối"
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid, routes=[route()])],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok and report.rolled_back
    assert report.step("routes").status is StepStatus.FAILED


def test_rollback_restores_touched_interfaces(world):
    """Bước sau route hỏng thì route đã áp phải được gỡ."""
    network, proxy, lan, _ = world
    network._snapshot.wifi_enabled = True
    network.fail["wifi"] = "rfkill"
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid, routes=[route()])],
        wifi_enabled=False,          # bước radio_off chạy SAU routes và sẽ hỏng
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.rolled_back
    assert ("restore", "enp3s0") in network.calls
    assert "enp3s0" not in network.runtime_routes


def test_routes_skipped_for_disconnected_device(world):
    """Thiết bị chưa lên thì không áp route — reapply sẽ thất bại."""
    network, proxy, _lan, wifi_home = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("enp3s0", BindingAction.LEAVE_ALONE, routes=[route()]),
            Binding("wlp2s0", BindingAction.ACTIVATE, wifi_home.uuid),
        ],
    )
    report, _ = run(make_applier(network, proxy), profile)
    assert report.ok
    assert "enp3s0" not in network.runtime_routes


def test_activate_targets_the_interface_named_by_binding(world):
    """Lỗi thật: profile không ghim interface-name mà để NM tự chọn thì nó giữ
    nguyên connection ở cổng cũ — người dùng bấm áp dụng mà không thấy gì đổi."""
    network, proxy, lan, _ = world
    profile = new_profile(
        "X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)]
    )
    run(make_applier(network, proxy), profile)

    call = next(c for c in network.calls if c[0] == "activate")
    assert call == ("activate", lan.uuid)
    # Interface phải được truyền xuống, không để None.
    assert network.last_activate_interface == "enp3s0"


def test_verify_waits_even_without_explicit_connection(world):
    """Lỗi thật: binding kiểu công tắc (uuid=None) làm verify bỏ qua, route bị
    áp lúc thiết bị còn CONNECTING nên trượt âm thầm mà vẫn báo thành công."""
    network, proxy, _lan, _ = world
    network.activation_hangs = True
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, None, routes=[route()])],
    )
    report, _ = run(ProfileApplier(network, proxy, schedule=ImmediateScheduler(200)), profile)

    assert not report.ok
    assert report.step("verify").status is StepStatus.FAILED


def test_routes_applied_when_binding_has_no_connection(world):
    network, proxy, _lan, _ = world
    profile = new_profile(
        "X",
        bindings=[Binding("enp3s0", BindingAction.ACTIVATE, None, routes=[route()])],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok, report.summary()
    assert report.step("verify").status is StepStatus.OK
    assert [r.cidr for r in network.runtime_routes["enp3s0"]] == ["10.0.0.0/8"]


def test_rollback_restores_connection_to_its_original_device(world):
    """Lỗi thật: khôi phục với device=None khiến profile không ghim interface
    bị NM gán sang cổng khác — rollback đá văng đúng thiết bị vừa lên."""
    network, proxy, lan, wifi_home = world
    network.fail["routes"] = "hỏng"
    profile = new_profile(
        "X",
        bindings=[
            Binding("wlp2s0", BindingAction.DISCONNECT),
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid, routes=[route()]),
        ],
    )
    run(make_applier(network, proxy), profile)

    restore_calls = [
        c for c in network.calls if c[0] == "activate" and c[1] == wifi_home.uuid
    ]
    assert restore_calls, "không khôi phục kết nối Wi-Fi đã ngắt"
    assert network.last_activate_interface == "wlp2s0"


# ── Automatic bật/tắt theo việc có route riêng hay không ─────────────────────


def test_co_route_rieng_thi_tat_automatic(world):
    """DHCP của công ty đẩy sẵn 10.0.0.0/8 — route đặt tay phải thắng, nên tắt
    Automatic đi thay vì để hai bên tranh nhau theo metric."""
    from netmgr.domain.models import Ipv4Route

    network, proxy, lan, _wifi = world
    profile = new_profile(
        "Có route",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid,
                    routes=[Ipv4Route("10.0.0.0", 8, next_hop="10.207.154.254")]),
        ],
    )
    report, _ = run(make_applier(network, proxy), profile)

    assert report.ok, report.summary()
    assert ("routes", "enp3s0") in network.calls
    assert network.last_ignore_auto is True


def test_automatic_la_suy_ra_khong_phai_lua_chon():
    """Không có cách nào bật Automatic mà vẫn giữ route riêng — hai chế độ này
    loại trừ nhau."""
    from netmgr.domain.models import Ipv4Route

    assert Binding("enp3s0").automatic_routes is True
    assert Binding(
        "enp3s0", routes=[Ipv4Route("10.0.0.0", 8)]
    ).automatic_routes is False


# ── Wi-Fi: chỉ cần bật radio, không bắt buộc kết nối được ────────────────────


def _wifi_profile():
    return new_profile(
        "Nội bộ + wifi",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE),
            Binding("wlp2s0", BindingAction.ACTIVATE),
        ],
    )


def test_wifi_khong_ket_noi_duoc_van_ap_dung_thanh_cong():
    """Đứng ở nơi không có mạng Wi-Fi đã lưu thì NM trả "has no connections".
    Không được để nó làm cả Bộ cấu hình thất bại khi mạng dây vẫn ổn."""
    wifi_dev = wifi_device(state=DeviceState.DISCONNECTED)
    eth_dev = eth_device(state=DeviceState.DISCONNECTED, ip=None)
    lan = eth_conn("LAN")
    snap = snapshot(devices=[eth_dev, wifi_dev], connections=[lan])
    network = FakeNetwork(snap)
    network.fail_activate["wlp2s0"] = "the device 'wlp2s0' has no connections"

    report, _ = run(make_applier(network, FakeProxyService([])), _wifi_profile())
    assert report.ok, report.summary()
    assert "Wi-Fi" in report.step("activate").detail


def test_bat_thiet_bi_wifi_thi_tu_bat_radio():
    """Không suy ra điều này thì radio vẫn tắt và kích hoạt chắc chắn thất bại."""
    wifi_dev = wifi_device(state=DeviceState.UNAVAILABLE)
    snap = snapshot(devices=[wifi_dev], connections=[], wifi_enabled=False)
    network = FakeNetwork(snap)

    profile = new_profile("W", bindings=[Binding("wlp2s0", BindingAction.ACTIVATE)])
    report, _ = run(make_applier(network, FakeProxyService([])), profile)

    assert report.ok, report.summary()
    assert ("wifi", True) in network.calls


def test_verify_khong_cho_thiet_bi_wifi():
    """Chờ Wi-Fi lên sẽ treo 30 giây rồi thất bại ở nơi không có mạng quen."""
    wifi_dev = wifi_device(state=DeviceState.DISCONNECTED)
    eth_dev = eth_device(state=DeviceState.CONNECTED)
    lan = eth_conn("LAN", active=True)
    eth_dev.active_connection_uuid = lan.uuid
    snap = snapshot(devices=[eth_dev, wifi_dev], connections=[lan])
    network = FakeNetwork(snap)
    network.fail_activate["wlp2s0"] = "the device 'wlp2s0' has no connections"

    report, _ = run(make_applier(network, FakeProxyService([])), _wifi_profile())
    assert report.step("verify").status is not StepStatus.FAILED
