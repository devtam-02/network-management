"""Lưu mật khẩu proxy trong GNOME Keyring qua libsecret (§4.7).

Nguyên tắc: mật khẩu KHÔNG bao giờ nằm trong file config của app, và không bao
giờ đi vào file export. Nguồn lưu trữ bền duy nhất là keyring.

Ngoại lệ đã biết và có chủ đích: khi áp dụng một profile có xác thực, mật khẩu
phải được ghi vào dconf (`org.gnome.system.proxy.http authentication-password`)
vì đó là chỗ duy nhất trình duyệt đọc. Bản sao đó bị xoá khi tắt proxy.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SCHEMA_NAME = "io.github.netmgr.ProxyPassword"
_ATTR_PROFILE = "profile_id"


def _load_secret():
    """Import libsecret muộn.

    Nếu import ở đầu module thì `NullSecretStore` — bản thay thế dùng cho test —
    cũng sẽ đòi typelib Secret, làm unit test không chạy được ở nơi không có nó.
    """
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import GLib, Secret

    return GLib, Secret


class SecretStore:
    """Bọc libsecret. Mọi lỗi nuốt thành None/False — thiếu mật khẩu không được
    làm sập app, chỉ khiến proxy có xác thực không dùng được."""

    def __init__(self) -> None:
        self._glib, self._secret = _load_secret()
        self._schema = self._secret.Schema.new(
            SCHEMA_NAME,
            self._secret.SchemaFlags.NONE,
            {_ATTR_PROFILE: self._secret.SchemaAttributeType.STRING},
        )

    def save_password(self, profile_id: str, password: str) -> bool:
        try:
            return self._secret.password_store_sync(
                self._schema,
                {_ATTR_PROFILE: profile_id},
                self._secret.COLLECTION_DEFAULT,
                f"netmgr — mật khẩu proxy ({profile_id})",
                password,
                None,
            )
        except self._glib.Error as exc:
            log.warning("Không lưu được mật khẩu proxy: %s", exc.message)
            return False

    def load_password(self, profile_id: str) -> str | None:
        try:
            return self._secret.password_lookup_sync(
                self._schema, {_ATTR_PROFILE: profile_id}, None
            )
        except self._glib.Error as exc:
            log.warning("Không đọc được mật khẩu proxy: %s", exc.message)
            return None

    def delete_password(self, profile_id: str) -> bool:
        try:
            return self._secret.password_clear_sync(
                self._schema, {_ATTR_PROFILE: profile_id}, None
            )
        except self._glib.Error as exc:
            log.warning("Không xoá được mật khẩu proxy: %s", exc.message)
            return False


class NullSecretStore:
    """Bản thay thế cho test và cho môi trường không có keyring."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def save_password(self, profile_id: str, password: str) -> bool:
        self._data[profile_id] = password
        return True

    def load_password(self, profile_id: str) -> str | None:
        return self._data.get(profile_id)

    def delete_password(self, profile_id: str) -> bool:
        return self._data.pop(profile_id, None) is not None
