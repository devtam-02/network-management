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


def test_binding_without_connection_fails_validation(world):
    network, proxy, _lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, None)])
    report, _ = run(make_applier(network, proxy), profile)

    assert not report.ok
    assert "chưa chọn cấu hình" in report.step("validate").detail


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
