"""Test lưu/đọc cấu hình proxy ra TOML."""

from __future__ import annotations

import pytest

from netmgr.domain.models import ProxyEndpoint, ProxyLayerId, ProxyMode
from netmgr.infra.proxy_store import ProxyStore, config_from_dict, config_to_dict, new_config


@pytest.fixture
def store(tmp_path) -> ProxyStore:
    return ProxyStore(directory=tmp_path / "netmgr")


def sample():
    cfg = new_config("Proxy Công ty", mode=ProxyMode.MANUAL)
    cfg.http = ProxyEndpoint("10.0.0.8", 3128)
    cfg.ignore_hosts = ["localhost", "*.viettel.vn"]
    cfg.auth_enabled = True
    cfg.username = "tam"
    return cfg


# ── chuyển đổi ───────────────────────────────────────────────────────────────


def test_roundtrip_preserves_fields():
    original = sample()
    restored = config_from_dict(config_to_dict(original))
    assert restored.id == original.id
    assert restored.name == original.name
    assert restored.mode is original.mode
    assert restored.http == original.http
    assert restored.ignore_hosts == original.ignore_hosts
    assert restored.auth_enabled is True
    assert restored.username == "tam"


def test_unset_endpoints_are_omitted():
    data = config_to_dict(new_config("X", mode=ProxyMode.MANUAL))
    assert "http" not in data and "socks" not in data


def test_layers_roundtrip():
    cfg = new_config("X")
    cfg.layers = {ProxyLayerId.DESKTOP}
    assert config_from_dict(config_to_dict(cfg)).layers == {ProxyLayerId.DESKTOP}


def test_unknown_layer_value_ignored():
    cfg = config_from_dict({"id": "1", "name": "X", "layers": ["desktop", "quantum"]})
    assert cfg.layers == {ProxyLayerId.DESKTOP}


def test_empty_layers_falls_back_to_defaults():
    cfg = config_from_dict({"id": "1", "name": "X", "layers": []})
    assert ProxyLayerId.DESKTOP in cfg.layers


def test_invalid_mode_falls_back_to_none():
    assert config_from_dict({"id": "1", "mode": "bịa"}).mode is ProxyMode.NONE


def test_missing_id_gets_generated():
    assert len(config_from_dict({"name": "X"}).id) == 36


# ── file ─────────────────────────────────────────────────────────────────────


def test_load_missing_file_returns_empty(store):
    assert store.load() == []


def test_save_then_load(store):
    original = sample()
    assert store.save([original])
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == original.name
    assert loaded[0].http == original.http


def test_password_never_written_to_disk(store):
    """§4.7 — mật khẩu chỉ nằm trong keyring."""
    cfg = sample()
    store.save([cfg])
    content = store.path.read_text()
    assert "password" not in content.lower()
    assert "tam" in content            # username thì được, mật khẩu thì không


def test_file_is_owner_only(store):
    store.save([sample()])
    assert oct(store.path.stat().st_mode)[-3:] == "600"
    assert oct(store.directory.stat().st_mode)[-3:] == "700"


def test_file_is_human_readable(store):
    store.save([sample()])
    content = store.path.read_text()
    assert content.startswith("#")
    assert "[[proxy]]" in content
    assert 'name = "Proxy Công ty"' in content


def test_no_temp_file_left_behind(store):
    store.save([sample()])
    assert list(store.directory.glob("*.tmp")) == []


def test_corrupt_file_does_not_crash(store):
    store.directory.mkdir(parents=True)
    store.path.write_text("đây không phải TOML [[[")
    assert store.load() == []          # ghi log rồi chạy tiếp


def test_corrupt_file_can_be_overwritten(store):
    store.directory.mkdir(parents=True)
    store.path.write_text("hỏng [[[")
    assert store.save([sample()])
    assert len(store.load()) == 1


def test_non_dict_rows_skipped(store):
    store.directory.mkdir(parents=True)
    store.path.write_text('proxy = ["không phải table"]\n')
    assert store.load() == []


# ── active_id ────────────────────────────────────────────────────────────────


def test_active_id_roundtrip(store):
    cfg = sample()
    store.save([cfg], active_id=cfg.id)
    assert store.load_active_id() == cfg.id


def test_no_active_id_returns_none(store):
    store.save([sample()])
    assert store.load_active_id() is None


def test_active_id_missing_file(store):
    assert store.load_active_id() is None


# ── nhiều cấu hình ───────────────────────────────────────────────────────────


def test_multiple_configs_keep_order(store):
    names = ["Công ty", "Nhà", "Khách hàng A"]
    store.save([new_config(n) for n in names])
    assert [c.name for c in store.load()] == names


def test_save_replaces_previous_content(store):
    store.save([new_config("Cũ")])
    store.save([new_config("Mới")])
    assert [c.name for c in store.load()] == ["Mới"]


def test_names_with_quotes_survive(store):
    store.save([new_config('Proxy "đặc biệt"')])
    assert store.load()[0].name == 'Proxy "đặc biệt"'
