"""Test CRUD Bộ cấu hình và việc chụp trạng thái hiện tại."""

from __future__ import annotations

import pytest

from netmgr.domain.models import (
    Binding,
    BindingAction,
    DeviceState,
    ProxyConfig,
    ProxyMode,
)
from netmgr.infra.profile_store import ProfileStore, new_profile
from netmgr.services.profile_applier import ProfileApplier
from netmgr.services.profile_service import ProfileService

from .factories import eth_conn, eth_device, snapshot, wifi_conn, wifi_device
from .fakes import FakeNetwork, FakeProxyService, ImmediateScheduler


@pytest.fixture
def world(tmp_path):
    wifi_dev = wifi_device()
    eth_dev = eth_device(state=DeviceState.DISCONNECTED, ip=None)
    lan = eth_conn("LAN Công ty")
    home = wifi_conn("WiFi Nhà", active=True)
    wifi_dev.active_connection_uuid = home.uuid

    snap = snapshot(devices=[eth_dev, wifi_dev], connections=[lan, home])
    network = FakeNetwork(snap)
    proxy = FakeProxyService([ProxyConfig(id="p1", name="Công ty", mode=ProxyMode.MANUAL)])
    store = ProfileStore(directory=tmp_path / "netmgr")
    service = ProfileService(
        network, proxy, store,
        applier=ProfileApplier(network, proxy, schedule=ImmediateScheduler()),
    )
    return service, network, proxy, lan, home


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_starts_empty(world):
    service, *_ = world
    assert service.profiles == []
    assert service.active is None


def test_save_and_reload(world):
    service, *_ = world
    service.save(new_profile("Công ty"))
    service.reload()
    assert [p.name for p in service.profiles] == ["Công ty"]


def test_save_rejects_empty_name(world):
    service, *_ = world
    assert not service.save(new_profile("   ")).ok
    assert service.profiles == []


def test_save_updates_existing(world):
    service, *_ = world
    profile = new_profile("Cũ")
    service.save(profile)
    profile.name = "Mới"
    service.save(profile)
    assert [p.name for p in service.profiles] == ["Mới"]


def test_delete(world):
    service, *_ = world
    profile = new_profile("X")
    service.save(profile)
    assert service.delete(profile.id).ok
    assert service.profiles == []


def test_delete_unknown(world):
    service, *_ = world
    assert not service.delete("không-có").ok


def test_deleting_active_profile_clears_marker_without_touching_network(world):
    """Xoá một bối cảnh không có nghĩa là yêu cầu ngắt mạng đang dùng."""
    service, network, _proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    service.save(profile)
    service.apply(profile.id, on_done=lambda r: None)
    network.calls.clear()

    service.delete(profile.id)
    assert service.active is None
    assert network.operations() == []


def test_duplicate_gets_new_id(world):
    service, *_ = world
    original = new_profile("Gốc", bindings=[Binding("enp3s0", BindingAction.DISCONNECT)])
    service.save(original)
    clone = service.duplicate(original.id, "Bản sao")

    assert clone.id != original.id
    assert clone.name == "Bản sao"
    assert len(service.profiles) == 2


def test_duplicate_does_not_share_bindings(world):
    service, *_ = world
    original = new_profile("Gốc", bindings=[Binding("enp3s0", BindingAction.DISCONNECT)])
    service.save(original)
    clone = service.duplicate(original.id, "Bản sao")
    clone.bindings[0].device_match = "đổi rồi"

    assert original.bindings[0].device_match == "enp3s0"


def test_reorder(world):
    service, *_ = world
    for name in ("A", "B", "C"):
        service.save(new_profile(name))
    third = service.profiles[2]
    service.reorder(third.id, -2)
    assert [p.name for p in service.profiles] == ["C", "A", "B"]


def test_reorder_clamps_at_edges(world):
    service, *_ = world
    service.save(new_profile("A"))
    service.save(new_profile("B"))
    first = service.profiles[0]
    service.reorder(first.id, -5)
    assert [p.name for p in service.profiles] == ["A", "B"]


# ── phát hiện hỏng ───────────────────────────────────────────────────────────


def test_profile_with_deleted_connection_is_broken(world):
    service, *_ = world
    service.save(
        new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, "đã-xoá")])
    )
    service.reload()
    assert service.profiles[0].is_broken
    assert "đã bị xoá" in service.profiles[0].broken_reason


def test_profile_with_deleted_proxy_is_broken(world):
    service, *_ = world
    service.save(new_profile("X", proxy_id="proxy-đã-xoá"))
    service.reload()
    assert service.profiles[0].is_broken


def test_healthy_profile_not_broken(world):
    service, _network, _proxy, lan, _ = world
    service.save(
        new_profile(
            "X",
            bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)],
            proxy_id="p1",
        )
    )
    service.reload()
    assert not service.profiles[0].is_broken


def test_disable_proxy_profile_is_not_broken(world):
    """proxy_id="" nghĩa là tắt proxy, không phải trỏ tới cấu hình rỗng."""
    service, *_ = world
    service.save(new_profile("X", proxy_id=""))
    service.reload()
    assert not service.profiles[0].is_broken


# ── chụp trạng thái hiện tại (FR-PR4) ────────────────────────────────────────


def test_capture_records_active_connections(world):
    service, _network, _proxy, _lan, home = world
    profile = service.capture_current("Hiện tại")

    wifi = profile.binding_for("wlp2s0")
    assert wifi.action is BindingAction.ACTIVATE
    assert wifi.connection_uuid == home.uuid


def test_capture_records_idle_devices_as_disconnect(world):
    service, *_ = world
    profile = service.capture_current("Hiện tại")
    assert profile.binding_for("enp3s0").action is BindingAction.DISCONNECT


def test_capture_marks_devices_optional(world):
    """Thiết bị USB/dock có thể vắng mặt lần sau — không được thành lỗi."""
    service, *_ = world
    profile = service.capture_current("Hiện tại")
    assert all(b.required is False for b in profile.bindings)


def test_capture_records_wifi_radio(world):
    service, *_ = world
    assert service.capture_current("X").wifi_enabled is True


def test_capture_records_proxy_off_when_disabled(world):
    service, *_ = world
    assert service.capture_current("X").proxy_id == ""


def test_capture_records_active_proxy(world):
    service, _network, proxy, *_ = world
    proxy.activate("p1")
    assert service.capture_current("X").proxy_id == "p1"


def test_captured_profile_is_persisted(world):
    service, *_ = world
    service.capture_current("Hiện tại")
    service.reload()
    assert [p.name for p in service.profiles] == ["Hiện tại"]


def test_captured_profile_reapplies_cleanly(world):
    """Vòng tròn quan trọng nhất: chụp → áp dụng lại phải ra đúng trạng thái đó."""
    service, network, _proxy, lan, home = world
    profile = service.capture_current("Hiện tại")

    # Đổi trạng thái đi
    network.activate_connection(lan.uuid, None, lambda _r: None)
    network.deactivate_connection(home.uuid, lambda _r: None)

    result = {}
    service.apply(profile.id, on_done=lambda r: result.update(r=r))
    assert result["r"].ok, result["r"].summary()
    assert home.is_active is True


# ── áp dụng ──────────────────────────────────────────────────────────────────


def test_apply_marks_profile_active(world):
    service, _network, _proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    service.save(profile)
    service.apply(profile.id, on_done=lambda r: None)

    assert service.active is not None and service.active.id == profile.id


def test_active_survives_reload(world):
    service, _network, _proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    service.save(profile)
    service.apply(profile.id, on_done=lambda r: None)
    service.reload()
    assert service.active.id == profile.id


def test_failed_apply_does_not_mark_active(world):
    service, network, _proxy, lan, _ = world
    network.fail["activate"] = "hỏng"
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    service.save(profile)
    service.apply(profile.id, on_done=lambda r: None)

    assert service.active is None


def test_apply_unknown_profile(world):
    service, *_ = world
    result = {}
    service.apply("không-có", on_done=lambda r: result.update(r=r))
    assert result["r"].ok is False or result["r"].profile_name == ""


def test_clear_active_does_not_touch_network(world):
    service, network, _proxy, lan, _ = world
    profile = new_profile("X", bindings=[Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid)])
    service.save(profile)
    service.apply(profile.id, on_done=lambda r: None)
    network.calls.clear()

    service.clear_active()
    assert service.active is None
    assert network.operations() == []


# ── mô tả ────────────────────────────────────────────────────────────────────


def test_describe_lists_bindings_and_proxy(world):
    service, _network, _proxy, lan, _ = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE, lan.uuid),
            Binding("wlp2s0", BindingAction.DISCONNECT),
        ],
        wifi_enabled=False,
        proxy_id="p1",
    )
    text = service.describe(profile)
    assert "LAN Công ty" in text
    assert "Tắt wlp2s0" in text
    assert "Wi-Fi tắt" in text
    assert "Proxy: Công ty" in text


def test_describe_binding_kieu_cong_tac(world):
    """Binding công tắc để trống connection_uuid — vẫn phải mô tả được."""
    service, *_ = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE),
            Binding("wlp2s0", BindingAction.DISCONNECT),
        ],
    )
    assert service.describe(profile) == "Bật enp3s0 · Tắt wlp2s0"


def test_describe_dem_route_rieng(world):
    from netmgr.domain.models import Ipv4Route

    service, *_ = world
    profile = new_profile(
        "X",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE,
                    routes=[Ipv4Route("10.0.0.0", 8, next_hop="10.0.0.1")]),
        ],
    )
    assert "1 route riêng" in service.describe(profile)


def test_describe_empty_profile(world):
    service, *_ = world
    assert service.describe(new_profile("X")) == "Chưa cấu hình gì"


def test_describe_proxy_off(world):
    service, *_ = world
    assert "Proxy tắt" in service.describe(new_profile("X", proxy_id=""))


def test_candidate_connections_filtered_by_device_type(world):
    service, *_ = world
    wifi_names = [c.display_name for c in service.candidate_connections("wlp2s0")]
    eth_names = [c.display_name for c in service.candidate_connections("enp3s0")]
    assert "WiFi Nhà" in wifi_names and "LAN Công ty" not in wifi_names
    assert "LAN Công ty" in eth_names


def test_candidate_connections_unknown_device(world):
    service, *_ = world
    assert service.candidate_connections("không-có") == []
