"""Lớp L1 — proxy cấp desktop qua `org.gnome.system.proxy`.

Đây là thứ Firefox, Chrome (chế độ dùng cài đặt hệ thống) và các ứng dụng GTK
đọc qua `libproxy`/`GProxyResolver`. Thay đổi có hiệu lực ngay, không cần khởi
động lại ứng dụng.

Schema đã xác minh trên Ubuntu 26.04:
    org.gnome.system.proxy        mode, ignore-hosts, autoconfig-url, use-same-proxy
    org.gnome.system.proxy.http   host, port, enabled, use-authentication,
                                  authentication-user, authentication-password
    org.gnome.system.proxy.https  host, port
    org.gnome.system.proxy.ftp    host, port
    org.gnome.system.proxy.socks  host, port
"""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from ...domain.models import (
    DEFAULT_IGNORE_HOSTS,
    OpResult,
    ProxyConfig,
    ProxyEndpoint,
    ProxyLayerId,
    ProxyMode,
)
from .base import ProxyLayer

log = logging.getLogger(__name__)

SCHEMA = "org.gnome.system.proxy"
CHILD_SCHEMAS = {
    "http": f"{SCHEMA}.http",
    "https": f"{SCHEMA}.https",
    "ftp": f"{SCHEMA}.ftp",
    "socks": f"{SCHEMA}.socks",
}

_MODE_TO_GSETTINGS = {
    ProxyMode.NONE: "none",
    ProxyMode.MANUAL: "manual",
    ProxyMode.AUTO: "auto",
}
_GSETTINGS_TO_MODE = {v: k for k, v in _MODE_TO_GSETTINGS.items()}


def _schema_exists(schema_id: str) -> bool:
    source = Gio.SettingsSchemaSource.get_default()
    return source is not None and source.lookup(schema_id, True) is not None


class GSettingsLayer(ProxyLayer):
    id = ProxyLayerId.DESKTOP
    name = "Ứng dụng desktop (trình duyệt, app GTK)"
    applies_immediately = True

    def __init__(self) -> None:
        self._root: Gio.Settings | None = None
        self._children: dict[str, Gio.Settings] = {}

    def _settings(self) -> Gio.Settings | None:
        if self._root is None and _schema_exists(SCHEMA):
            self._root = Gio.Settings.new(SCHEMA)
            for scheme, schema_id in CHILD_SCHEMAS.items():
                if _schema_exists(schema_id):
                    self._children[scheme] = Gio.Settings.new(schema_id)
        return self._root

    def is_available(self) -> bool:
        return self._settings() is not None

    # ── ghi ─────────────────────────────────────────────────────────────────

    def apply(self, config: ProxyConfig, password: str | None = None) -> OpResult:
        root = self._settings()
        if root is None:
            return OpResult(False, f"Không tìm thấy schema {SCHEMA}")

        try:
            if config.mode is ProxyMode.MANUAL:
                self._apply_manual(config, password)
            elif config.mode is ProxyMode.AUTO:
                root.set_string("autoconfig-url", config.pac_url)
            else:
                self._clear_endpoints()

            root.set_strv("ignore-hosts", list(config.ignore_hosts))
            root.set_boolean("use-same-proxy", config.use_same_for_all)
            # Đặt mode CUỐI CÙNG: nó là công tắc kích hoạt, đặt trước thì sẽ có
            # khoảnh khắc proxy bật với host/port của lần cấu hình trước.
            root.set_string("mode", _MODE_TO_GSETTINGS[config.mode])
            Gio.Settings.sync()
        except GLib.Error as exc:
            return OpResult.failure(exc, "Ghi cấu hình proxy desktop")
        return OpResult.success()

    def _apply_manual(self, config: ProxyConfig, password: str | None) -> None:
        for scheme, settings in self._children.items():
            endpoint = config.effective(scheme)
            settings.set_string("host", endpoint.host if endpoint.is_set else "")
            settings.set_int("port", endpoint.port if endpoint.is_set else 0)

        http = self._children.get("http")
        if http is None:
            return
        http.set_boolean("enabled", config.effective("http").is_set)
        http.set_boolean("use-authentication", config.auth_enabled)
        http.set_string("authentication-user", config.username if config.auth_enabled else "")
        # Mật khẩu bắt buộc phải nằm ở đây thì trình duyệt mới dùng được — GNOME
        # đọc nó từ dconf. Nguồn lưu trữ bền vẫn là libsecret; dconf chỉ giữ bản
        # sao trong lúc profile đang bật, và bị xoá khi tắt proxy (`clear`).
        http.set_string(
            "authentication-password", password if (config.auth_enabled and password) else ""
        )

    def _clear_endpoints(self) -> None:
        for settings in self._children.values():
            settings.set_string("host", "")
            settings.set_int("port", 0)
        http = self._children.get("http")
        if http is not None:
            http.set_boolean("enabled", False)
            http.set_boolean("use-authentication", False)
            http.set_string("authentication-user", "")
            http.set_string("authentication-password", "")

    def clear(self) -> OpResult:
        root = self._settings()
        if root is None:
            return OpResult(False, f"Không tìm thấy schema {SCHEMA}")
        try:
            root.set_string("mode", "none")
            root.set_string("autoconfig-url", "")
            self._clear_endpoints()
            Gio.Settings.sync()
        except GLib.Error as exc:
            return OpResult.failure(exc, "Tắt proxy desktop")
        return OpResult.success()

    # ── đọc ─────────────────────────────────────────────────────────────────

    def read(self) -> ProxyConfig | None:
        root = self._settings()
        if root is None:
            return None

        mode = _GSETTINGS_TO_MODE.get(root.get_string("mode"), ProxyMode.NONE)
        config = ProxyConfig(
            name="Cấu hình hiện tại của hệ thống",
            mode=mode,
            pac_url=root.get_string("autoconfig-url"),
            ignore_hosts=list(root.get_strv("ignore-hosts")) or list(DEFAULT_IGNORE_HOSTS),
            use_same_for_all=root.get_boolean("use-same-proxy"),
        )

        for scheme, settings in self._children.items():
            host = settings.get_string("host")
            port = settings.get_int("port")
            if host and port:
                setattr(config, scheme, ProxyEndpoint(host, port))

        http = self._children.get("http")
        if http is not None:
            config.auth_enabled = http.get_boolean("use-authentication")
            config.username = http.get_string("authentication-user")
        return config

    def read_password(self) -> str:
        """Đọc mật khẩu đang nằm trong dconf — chỉ dùng khi import cấu hình sẵn có."""
        http = self._children.get("http") if self._settings() else None
        return http.get_string("authentication-password") if http else ""
