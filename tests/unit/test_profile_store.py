"""Test lưu/đọc Bộ cấu hình ra TOML."""

from __future__ import annotations

import pytest

from netmgr.domain.models import Binding, BindingAction
from netmgr.infra.profile_store import (
    ProfileStore,
    new_profile,
    profile_from_dict,
    profile_to_dict,
)


@pytest.fixture
def store(tmp_path) -> ProfileStore:
    return ProfileStore(directory=tmp_path / "netmgr")


def sample():
    return new_profile(
        "Công ty",
        icon="🏢",
        description="Mạng LAN văn phòng",
        bindings=[
            Binding("enp3s0", BindingAction.ACTIVATE, "uuid-lan", required=True),
            Binding("wlp2s0", BindingAction.DISCONNECT),
        ],
        wifi_enabled=False,
        proxy_id="proxy-1",
    )


# ── chuyển đổi ───────────────────────────────────────────────────────────────


def test_roundtrip_preserves_everything():
    original = sample()
    restored = profile_from_dict(profile_to_dict(original))
    assert restored.id == original.id
    assert restored.name == "Công ty"
    assert restored.icon == "🏢"
    assert restored.description == "Mạng LAN văn phòng"
    assert restored.wifi_enabled is False
    assert restored.proxy_id == "proxy-1"
    assert len(restored.bindings) == 2


def test_binding_fields_roundtrip():
    restored = profile_from_dict(profile_to_dict(sample()))
    binding = restored.bindings[0]
    assert binding.device_match == "enp3s0"
    assert binding.action is BindingAction.ACTIVATE
    assert binding.connection_uuid == "uuid-lan"
    assert binding.required is True


def test_proxy_untouched_vs_disabled_are_distinct():
    """TOML không có null: "" = tắt proxy, thiếu khoá = không đụng tới."""
    untouched = profile_from_dict(profile_to_dict(new_profile("A", proxy_id=None)))
    disabled = profile_from_dict(profile_to_dict(new_profile("B", proxy_id="")))
    assert untouched.proxy_id is None and not untouched.touches_proxy
    assert disabled.proxy_id == "" and disabled.touches_proxy and disabled.disables_proxy


def test_wifi_untouched_is_none():
    restored = profile_from_dict(profile_to_dict(new_profile("A")))
    assert restored.wifi_enabled is None


def test_unknown_action_falls_back_to_leave_alone():
    """File sửa tay hoặc schema tương lai → chọn hành động an toàn nhất."""
    profile = profile_from_dict(
        {"id": "1", "name": "X", "binding": [{"device_match": "eth0", "action": "bịa"}]}
    )
    assert profile.bindings[0].action is BindingAction.LEAVE_ALONE


def test_non_dict_binding_skipped():
    profile = profile_from_dict({"id": "1", "name": "X", "binding": ["rác"]})
    assert profile.bindings == []


def test_missing_id_generated():
    assert len(profile_from_dict({"name": "X"}).id) == 36


def test_missing_icon_gets_default():
    assert profile_from_dict({"id": "1", "name": "X"}).icon == "🌐"


# ── file ─────────────────────────────────────────────────────────────────────


def test_load_missing_file(store):
    assert store.load() == []
    assert store.load_active_id() is None


def test_save_then_load(store):
    original = sample()
    assert store.save([original])
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == "Công ty"
    assert loaded[0].bindings[0].connection_uuid == "uuid-lan"


def test_active_id_roundtrip(store):
    profile = sample()
    store.save([profile], active_id=profile.id)
    assert store.load_active_id() == profile.id


def test_profiles_sorted_by_order(store):
    a = new_profile("Zulu", order=0)
    b = new_profile("Alpha", order=1)
    store.save([b, a])
    assert [p.name for p in store.load()] == ["Zulu", "Alpha"]


def test_same_order_sorted_by_name(store):
    store.save([new_profile("Zulu"), new_profile("Alpha")])
    assert [p.name for p in store.load()] == ["Alpha", "Zulu"]


def test_file_is_owner_only(store):
    store.save([sample()])
    assert oct(store.path.stat().st_mode)[-3:] == "600"
    assert oct(store.directory.stat().st_mode)[-3:] == "700"


def test_file_is_human_readable(store):
    store.save([sample()])
    content = store.path.read_text()
    assert content.startswith("#")
    assert "[[profile]]" in content
    assert 'name = "Công ty"' in content


def test_corrupt_file_does_not_crash(store):
    store.directory.mkdir(parents=True)
    store.path.write_text("không phải TOML [[[")
    assert store.load() == []


def test_no_temp_file_left_behind(store):
    store.save([sample()])
    assert list(store.directory.glob("*.tmp")) == []


def test_names_with_quotes_survive(store):
    store.save([new_profile('Bộ "đặc biệt"')])
    assert store.load()[0].name == 'Bộ "đặc biệt"'
