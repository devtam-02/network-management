"""Test lớp proxy desktop với gsettings THẬT.

CHỈ ĐỌC. Cố tình không có test nào ghi: gsettings là cấu hình proxy thật của
người dùng, một test hỏng giữa chừng sẽ để lại máy ở trạng thái sai. Đường ghi
đã được phủ bởi unit test (layer giả) và bởi kịch bản e2e chạy tay có sao lưu
`dconf dump` kèm khôi phục.
"""

from __future__ import annotations

import pytest

from netmgr.domain.models import ProxyLayerId, ProxyMode

pytestmark = pytest.mark.needs_nm


@pytest.fixture(scope="module")
def layer():
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gio", "2.0")
    except ValueError:
        pytest.skip("Không có typelib Gio")

    from netmgr.infra.proxy.gsettings_layer import GSettingsLayer

    instance = GSettingsLayer()
    if not instance.is_available():
        pytest.skip("Không có schema org.gnome.system.proxy")
    return instance


def test_layer_identity(layer):
    assert layer.id is ProxyLayerId.DESKTOP
    assert layer.applies_immediately is True


def test_read_returns_config(layer):
    assert layer.read() is not None


def test_mode_is_a_known_value(layer):
    assert isinstance(layer.read().mode, ProxyMode)


def test_ignore_hosts_never_empty(layer):
    """Trả về rỗng sẽ khiến lần ghi sau xoá sạch danh sách người dùng đã gõ."""
    assert layer.read().ignore_hosts


def test_endpoints_are_well_formed(layer):
    config = layer.read()
    for scheme in ("http", "https", "ftp", "socks"):
        endpoint = getattr(config, scheme)
        if endpoint.is_set:
            assert endpoint.host
            assert 1 <= endpoint.port <= 65535


def test_status_is_consistent_with_read(layer):
    status = layer.status()
    config = layer.read()
    assert status.available is True
    assert status.active == config.is_enabled


def test_read_is_repeatable(layer):
    """Đọc hai lần phải ra cùng kết quả — không có trạng thái ẩn."""
    first, second = layer.read(), layer.read()
    assert first.mode is second.mode
    assert first.ignore_hosts == second.ignore_hosts
    assert first.http == second.http


def test_read_does_not_mutate_anything(layer):
    """Chứng minh `read()` và `status()` thực sự chỉ đọc."""
    import subprocess

    before = subprocess.run(
        ["dconf", "dump", "/system/proxy/"], capture_output=True, text=True
    ).stdout
    layer.read()
    layer.status()
    after = subprocess.run(
        ["dconf", "dump", "/system/proxy/"], capture_output=True, text=True
    ).stdout
    assert before == after
