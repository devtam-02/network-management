"""Bật/tắt khởi động cùng máy bằng file `.desktop` trong `~/.config/autostart`.

Không import `gi` — chỉ file IO, nên test được bằng tmp_path.

Chuẩn XDG Autostart: desktop environment đọc mọi `.desktop` trong thư mục này
lúc đăng nhập. Không cần systemd, không cần quyền root.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

FILENAME = "netmgr.desktop"

#: Chờ NetworkManager và extension appindicator sẵn sàng. Thiếu độ trễ này thì
#: tray vẫn hiện (app tự đăng ký lại) nhưng icon sẽ nhấp nháy lúc đăng nhập.
STARTUP_DELAY_SECONDS = 3

_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=Quản lý mạng
Comment=Chạy nền ở khay hệ thống
Exec={exec_line}
Icon=netmgr
Terminal=false
Categories=Network;
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay={delay}
"""


def autostart_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"


def autostart_path() -> Path:
    return autostart_dir() / FILENAME


def launch_command() -> str:
    """Lệnh chạy app ở chế độ nền.

    Ưu tiên lệnh `netmgr` đã cài; chạy thẳng từ source thì dùng chính
    interpreter và đường dẫn hiện tại, để autostart không phụ thuộc vào PATH.
    """
    installed = shutil.which("netmgr")
    if installed:
        return f"{installed} tray"

    # Chạy từ source: giữ nguyên PYTHONPATH bằng cách gọi qua `env`.
    src = Path(__file__).resolve().parents[2]
    return f"env PYTHONPATH={src} {sys.executable} -m netmgr tray"


def is_enabled() -> bool:
    return autostart_path().exists()


def enable() -> bool:
    """Bật khởi động cùng máy. Trả về False nếu ghi file thất bại."""
    try:
        autostart_dir().mkdir(parents=True, exist_ok=True)
        autostart_path().write_text(
            _TEMPLATE.format(
                exec_line=launch_command(), delay=STARTUP_DELAY_SECONDS
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.error("Không bật được khởi động cùng máy: %s", exc)
        return False
    return True


def disable() -> bool:
    try:
        autostart_path().unlink(missing_ok=True)
    except OSError as exc:
        log.error("Không tắt được khởi động cùng máy: %s", exc)
        return False
    return True


def set_enabled(enabled: bool) -> bool:
    return enable() if enabled else disable()
