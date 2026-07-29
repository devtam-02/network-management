"""Lưu danh sách cấu hình proxy vào `~/.config/netmgr/proxy.toml`.

Không import `gi` — chỉ file IO + tomllib, nên test được bằng tmp_path.

File KHÔNG chứa mật khẩu (§4.7); mật khẩu nằm trong keyring, tra theo `id`.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ..domain.models import (
    DEFAULT_IGNORE_HOSTS,
    ProxyConfig,
    ProxyEndpoint,
    ProxyLayerId,
    ProxyMode,
)
from .toml_writer import dumps

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
FILENAME = "proxy.toml"
HEADER = """netmgr — cấu hình proxy.
File này KHÔNG chứa mật khẩu (mật khẩu nằm trong GNOME Keyring).
Sửa tay được, nhưng app đang chạy sẽ ghi đè khi bạn lưu từ giao diện.
"""

_SCHEMES = ("http", "https", "ftp", "socks")


def default_config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "netmgr"


# ─────────────────────────────────────────────────────────────────────────────
# Chuyển đổi
# ─────────────────────────────────────────────────────────────────────────────


#: Cấu hình proxy do app tạo sẵn. Id cố định để nhận ra qua các lần khởi động.
BUILTIN_ID = "builtin-default"
BUILTIN_NAME = "Cấu hình mặc định"


def project_pac_path() -> Path:
    """File PAC của cấu hình mặc định, nằm trong thư mục dự án.

    Đặt trong dự án (không phải ~/.config) để nó đi kèm mã nguồn: sửa file là
    thấy hiệu lực ngay, và có thể theo dõi thay đổi bằng git.
    """
    return Path(__file__).resolve().parents[3] / "data" / "pac" / "default.pac"


def builtin_config() -> ProxyConfig:
    """Cấu hình proxy mặc định: chế độ tự động (PAC), trỏ vào file trong dự án.

    LUÔN có sẵn nhưng KHÔNG tự bật — proxy vẫn tắt tới khi người dùng chọn nó.
    """
    return ProxyConfig(
        id=BUILTIN_ID,
        name=BUILTIN_NAME,
        mode=ProxyMode.AUTO,
        pac_url=project_pac_path().as_uri(),
        builtin=True,
    )


def config_to_dict(config: ProxyConfig) -> dict:
    data: dict = {
        "id": config.id,
        "name": config.name,
        "mode": config.mode.value,
        "use_same_for_all": config.use_same_for_all,
        "ignore_hosts": list(config.ignore_hosts),
        "auth_enabled": config.auth_enabled,
        "layers": sorted(layer.value for layer in config.layers),
    }
    if config.builtin:
        data["builtin"] = True
    if config.username:
        data["username"] = config.username
    if config.pac_url:
        data["pac_url"] = config.pac_url
    for scheme in _SCHEMES:
        endpoint: ProxyEndpoint = getattr(config, scheme)
        if endpoint.is_set:
            data[scheme] = {"host": endpoint.host, "port": endpoint.port}
    return data


def config_from_dict(data: dict) -> ProxyConfig:
    layers = {
        ProxyLayerId(v)
        for v in data.get("layers", [])
        if v in {layer.value for layer in ProxyLayerId}
    }
    config = ProxyConfig(
        id=str(data.get("id") or uuid4()),
        name=str(data.get("name", "")),
        mode=_parse_mode(data.get("mode")),
        use_same_for_all=bool(data.get("use_same_for_all", True)),
        ignore_hosts=[str(h) for h in data.get("ignore_hosts", DEFAULT_IGNORE_HOSTS)],
        auth_enabled=bool(data.get("auth_enabled", False)),
        username=str(data.get("username", "")),
        pac_url=str(data.get("pac_url", "")),
        layers=layers or {ProxyLayerId.DESKTOP, ProxyLayerId.ENVIRONMENT},
        builtin=bool(data.get("builtin", False)),
    )
    for scheme in _SCHEMES:
        raw = data.get(scheme)
        if isinstance(raw, dict) and raw.get("host"):
            setattr(
                config, scheme, ProxyEndpoint(str(raw["host"]), int(raw.get("port", 0)))
            )
    return config


def _parse_mode(raw) -> ProxyMode:
    try:
        return ProxyMode(raw)
    except ValueError:
        return ProxyMode.NONE


# ─────────────────────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────────────────────


class ProxyStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_config_dir()

    @property
    def path(self) -> Path:
        return self.directory / FILENAME

    def load(self) -> list[ProxyConfig]:
        # Config hỏng không được làm app không khởi động được: `_raw` ghi log
        # rồi trả dict rỗng, người dùng vẫn dùng được phần mạng.
        out: list[ProxyConfig] = []
        for row in self._raw().get("proxy", []):
            if isinstance(row, dict):
                out.append(config_from_dict(row))
        return out

    def save(self, configs: list[ProxyConfig], active_id: str | None = None) -> bool:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "active_id": active_id or "",
            "proxy": [config_to_dict(c) for c in configs],
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            # Ghi ra file tạm rồi replace (atomic trên cùng filesystem): tắt máy
            # giữa chừng không để lại config cụt.
            tmp = self.path.with_suffix(".toml.tmp")
            tmp.write_text(dumps(payload, header=HEADER), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Không ghi được %s: %s", self.path, exc)
            return False
        return True

    def load_active_id(self) -> str | None:
        return self._raw().get("active_id") or None

    def _raw(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.error("Không đọc được %s: %s", self.path, exc)
            return {}


def new_config(name: str, **kwargs) -> ProxyConfig:
    """Tạo cấu hình mới có sẵn id."""
    return replace(ProxyConfig(name=name, **kwargs), id=str(uuid4()))
