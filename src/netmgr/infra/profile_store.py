"""Lưu Bộ cấu hình vào `~/.config/netmgr/profiles.toml`.

Không import `gi` — chỉ file IO + tomllib, nên test được bằng tmp_path.

File có `schema_version` để migrate về sau, và được ghi qua file tạm rồi replace
để tắt máy giữa chừng không để lại config cụt.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from uuid import uuid4

from ..domain.models import Binding, BindingAction, Profile
from .proxy_store import default_config_dir
from .toml_writer import dumps

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
FILENAME = "profiles.toml"
HEADER = """netmgr — Bộ cấu hình (mạng + proxy theo bối cảnh).
File này KHÔNG chứa mật khẩu.
Sửa tay được, nhưng app đang chạy sẽ ghi đè khi bạn lưu từ giao diện.
"""


def binding_to_dict(binding: Binding) -> dict:
    data: dict = {
        "device_match": binding.device_match,
        "action": binding.action.value,
        "owned": binding.owned,
        "required": binding.required,
    }
    if binding.connection_uuid:
        data["connection_uuid"] = binding.connection_uuid
    return data


def binding_from_dict(data: dict) -> Binding:
    try:
        action = BindingAction(data.get("action"))
    except ValueError:
        # Giá trị lạ (file sửa tay, hoặc schema tương lai) → chọn cái an toàn
        # nhất: không đụng vào thiết bị.
        action = BindingAction.LEAVE_ALONE
    return Binding(
        device_match=str(data.get("device_match", "")),
        action=action,
        connection_uuid=data.get("connection_uuid") or None,
        owned=bool(data.get("owned", False)),
        required=bool(data.get("required", False)),
    )


def profile_to_dict(profile: Profile) -> dict:
    data: dict = {
        "id": profile.id,
        "name": profile.name,
        "icon": profile.icon,
        "order": profile.order,
    }
    if profile.description:
        data["description"] = profile.description
    if profile.wifi_enabled is not None:
        data["wifi_enabled"] = profile.wifi_enabled
    if profile.proxy_id is not None:
        # Chuỗi rỗng nghĩa là "tắt proxy" — khác hẳn với không có khoá, nghĩa là
        # "không đụng tới proxy". TOML không có null nên phải phân biệt bằng
        # sự tồn tại của khoá.
        data["proxy_id"] = profile.proxy_id
    if profile.bindings:
        data["binding"] = [binding_to_dict(b) for b in profile.bindings]
    return data


def profile_from_dict(data: dict) -> Profile:
    raw_bindings = data.get("binding", [])
    return Profile(
        id=str(data.get("id") or uuid4()),
        name=str(data.get("name", "")),
        icon=str(data.get("icon") or "🌐"),
        description=str(data.get("description", "")),
        order=int(data.get("order", 0)),
        bindings=[binding_from_dict(b) for b in raw_bindings if isinstance(b, dict)],
        wifi_enabled=data.get("wifi_enabled"),
        proxy_id=data["proxy_id"] if "proxy_id" in data else None,
    )


class ProfileStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_config_dir()

    @property
    def path(self) -> Path:
        return self.directory / FILENAME

    def _raw(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            # File hỏng không được chặn app khởi động — người dùng vẫn phải
            # quản lý được mạng.
            log.error("Không đọc được %s: %s", self.path, exc)
            return {}

    def load(self) -> list[Profile]:
        profiles = [
            profile_from_dict(row)
            for row in self._raw().get("profile", [])
            if isinstance(row, dict)
        ]
        profiles.sort(key=lambda p: (p.order, p.name.lower()))
        return profiles

    def load_active_id(self) -> str | None:
        return self._raw().get("active_id") or None

    def save(self, profiles: list[Profile], active_id: str | None = None) -> bool:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "active_id": active_id or "",
            "profile": [profile_to_dict(p) for p in profiles],
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            tmp = self.path.with_suffix(".toml.tmp")
            tmp.write_text(dumps(payload, header=HEADER), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Không ghi được %s: %s", self.path, exc)
            return False
        return True


def new_profile(name: str, **kwargs) -> Profile:
    return Profile(id=str(uuid4()), name=name, **kwargs)
