"""Test bật/tắt khởi động cùng máy."""

from __future__ import annotations

import pytest

from netmgr.infra import autostart


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


def test_disabled_by_default(home):
    assert autostart.is_enabled() is False


def test_enable_creates_file(home):
    assert autostart.enable()
    assert autostart.is_enabled()
    assert autostart.autostart_path().name == "netmgr.desktop"


def test_enable_creates_missing_directory(home):
    assert not autostart.autostart_dir().exists()
    assert autostart.enable()
    assert autostart.autostart_dir().is_dir()


def test_disable_removes_file(home):
    autostart.enable()
    assert autostart.disable()
    assert not autostart.is_enabled()


def test_disable_is_idempotent(home):
    assert autostart.disable()
    assert autostart.disable()


def test_set_enabled_both_ways(home):
    autostart.set_enabled(True)
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_enable_twice_does_not_duplicate(home):
    autostart.enable()
    autostart.enable()
    assert len(list(autostart.autostart_dir().glob("*.desktop"))) == 1


# ── nội dung file ────────────────────────────────────────────────────────────


def test_file_runs_tray_not_window(home):
    """Khởi động cùng máy phải chạy nền, không bật cửa sổ vào mặt người dùng."""
    autostart.enable()
    content = autostart.autostart_path().read_text()
    assert "tray" in content
    assert " window" not in content


def test_file_has_startup_delay(home):
    autostart.enable()
    content = autostart.autostart_path().read_text()
    assert f"X-GNOME-Autostart-Delay={autostart.STARTUP_DELAY_SECONDS}" in content


def test_file_is_valid_desktop_entry(home):
    autostart.enable()
    content = autostart.autostart_path().read_text()
    assert content.startswith("[Desktop Entry]")
    assert "Type=Application" in content
    assert "Terminal=false" in content


def test_launch_command_prefers_installed_binary(home, monkeypatch):
    monkeypatch.setattr(autostart.shutil, "which", lambda _n: "/home/u/.local/bin/netmgr")
    assert autostart.launch_command() == "/home/u/.local/bin/netmgr tray"


def test_launch_command_falls_back_to_source(home, monkeypatch):
    """Chạy từ source thì autostart không được phụ thuộc vào PATH."""
    monkeypatch.setattr(autostart.shutil, "which", lambda _n: None)
    command = autostart.launch_command()
    assert "PYTHONPATH=" in command
    assert command.endswith("-m netmgr tray")


def test_enable_failure_reported(home, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("đĩa đầy")

    monkeypatch.setattr(autostart.Path, "write_text", boom)
    assert autostart.enable() is False
