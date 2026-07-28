"""Ứng dụng — ghép NMFacade, ProxyService, tray SNI và cửa sổ GTK4.

Một tiến trình duy nhất: SNI tự implement trên Gio.DBus nên không dính GTK3, và
cửa sổ cấu hình dùng GTK4/libadwaita ngay trong tiến trình này.

Lớp `NetmgrApp` cũng đóng vai controller cho UI — cửa sổ chỉ gọi các method ở
đây chứ không tự chạm vào facade hay service.
"""

from __future__ import annotations

import logging
import signal

import gi

gi.require_version("NM", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

try:
    # PyGObject mới tách hàm này ra namespace riêng; `GLib.unix_signal_add`
    # vẫn chạy nhưng đã deprecated.
    from gi.repository import GLibUnix

    _unix_signal_add = GLibUnix.signal_add_full
except (ImportError, ValueError):  # pragma: no cover — bản PyGObject cũ
    _unix_signal_add = GLib.unix_signal_add

from . import APP_ID  # noqa: E402
from .infra.nm_facade import NMFacade, NMUnavailableError, OpResult  # noqa: E402
from .infra.proxy import EnvdLayer, GSettingsLayer  # noqa: E402
from .infra.profile_store import ProfileStore  # noqa: E402
from .infra.proxy_store import ProxyStore  # noqa: E402
from .services.profile_applier import ProfileApplier  # noqa: E402
from .services.profile_service import ProfileService  # noqa: E402
from .services.proxy_service import ApplyReport, ProxyService  # noqa: E402
from .tray.menu_builder import MenuActions, build_menu  # noqa: E402
from .tray.menu_model import Menu  # noqa: E402
from .tray.sni import StatusNotifierItem  # noqa: E402
from .tray.status import compute_status  # noqa: E402

log = logging.getLogger(__name__)

#: Nếu sau ngần này mà chưa đăng ký được với watcher thì gần như chắc chắn
#: extension appindicator đang tắt (R1).
WATCHER_CHECK_DELAY_MS = 3000


class NetmgrApp(Adw.Application):
    def __init__(self, show_window: bool = False) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._facade: NMFacade | None = None
        self._tray: StatusNotifierItem | None = None
        self._proxy: ProxyService | None = None
        self._profiles: ProfileService | None = None
        self._window = None
        self._show_window_on_start = show_window
        self._first_activate = True
        #: Interface đang kết nối ở lần refresh trước; None = chưa biết.
        self._connected_ifaces: set[str] | None = None
        self._apply_progress = None

    # ── vòng đời ────────────────────────────────────────────────────────────

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)

        self._facade = NMFacade()
        log.info("Đã kết nối NetworkManager %s", self._facade.nm_version)

        self._proxy = ProxyService(
            layers=[GSettingsLayer(), EnvdLayer()],
            store=ProxyStore(),
            secrets=_make_secret_store(),
        )
        log.info("Đã nạp %d cấu hình proxy", len(self._proxy.configs))

        self._profiles = ProfileService(
            self._facade,
            self._proxy,
            ProfileStore(),
            applier=ProfileApplier(
                self._facade,
                self._proxy,
                schedule=GLib.timeout_add,
                cancel_schedule=GLib.source_remove,
            ),
        )
        log.info("Đã nạp %d Bộ cấu hình", len(self._profiles.profiles))

        self._tray = StatusNotifierItem(
            APP_ID,
            title="Quản lý mạng",
            on_secondary_activate=self.open_window,
            on_about_to_show=self.refresh,
        )
        self._tray.start()

        self._facade.subscribe(self.refresh)
        self.refresh()

        # Lần thoát trước đã `restore_all()`, nên Bộ cấu hình được nạp lại từ đĩa
        # chỉ còn là cái nhãn: runtime đã về nguyên gốc. Áp lại route để trạng
        # thái hiện ra khớp với thực tế.
        if self._profiles.active is not None:
            log.info("Áp lại route của Bộ cấu hình đang dùng sau khi khởi động")
            self._profiles.reassert_routes()

        GLib.timeout_add(WATCHER_CHECK_DELAY_MS, self._check_watcher)

    def do_activate(self) -> None:
        # `hold()` giữ app sống khi không có cửa sổ nào — tray là giao diện
        # chính, đóng cửa sổ không được làm thoát app.
        self.hold()

        if self._first_activate:
            self._first_activate = False
            # Khởi động nền (autostart, `netmgr tray`): chỉ hiện tray.
            if not self._show_window_on_start:
                return

        # Mọi lần kích hoạt SAU ĐÓ đều mở cửa sổ. Đây là lúc người dùng bấm vào
        # app trong menu ứng dụng hoặc gõ `netmgr` lần nữa: tiến trình thứ hai
        # chỉ đánh thức tiến trình đang chạy, nên nếu không mở cửa sổ ở đây thì
        # bấm vào icon app sẽ không có gì xảy ra.
        self.open_window()

    def do_shutdown(self) -> None:
        # Route của Bộ cấu hình chỉ sống ở runtime; trả máy về cấu hình gốc
        # trước khi thoát để không để lại dấu vết nào.
        if self._profiles is not None:
            self._profiles.restore_all()
        if self._tray is not None:
            self._tray.close()
        if self._facade is not None:
            self._facade.close()
        Adw.Application.do_shutdown(self)

    def _check_watcher(self) -> bool:
        if self._tray is not None and not self._tray.is_registered:
            log.warning(
                "Chưa đăng ký được tray icon. Nhiều khả năng extension "
                "appindicator đang tắt — bật lại bằng:\n"
                "    gnome-extensions enable ubuntu-appindicators@ubuntu.com"
            )
        return GLib.SOURCE_REMOVE

    # ── cửa sổ ──────────────────────────────────────────────────────────────

    def open_window(self) -> None:
        if self._window is None:
            from .ui.main_window import MainWindow

            self._window = MainWindow(self, self)
            # Đóng cửa sổ chỉ ẩn đi: mở lại tức thì, và không kéo theo thoát app.
            self._window.connect("close-request", self._on_window_close)
        self._window.present()

    def _on_window_close(self, window) -> bool:
        window.set_visible(False)
        return True                     # chặn destroy

    # ── cập nhật hiển thị ───────────────────────────────────────────────────

    def refresh(self) -> None:
        """Đọc lại trạng thái và dựng lại icon, menu và cửa sổ (nếu đang mở)."""
        if self._facade is None or self._tray is None:
            return

        snapshot = self._facade.snapshot()
        self._reassert_if_reconnected(snapshot)
        active_profile = self._profiles.active if self._profiles else None
        status = compute_status(
            snapshot,
            proxy_summary=self._proxy.summary(),
            profile_label=active_profile.label if active_profile else None,
        )

        self._tray.set_status(status)
        self._tray.set_menu(
            Menu(
                build_menu(
                    snapshot, status, self._actions(),
                    proxy=self._proxy, profiles=self._profiles,
                ),
                self._tray.id_allocator,
            )
        )
        if self._window is not None and self._window.get_visible():
            self._window.refresh()

    def _actions(self) -> MenuActions:
        return MenuActions(
            set_wifi_enabled=self.set_wifi_enabled,
            activate_connection=self.activate_connection,
            deactivate_connection=self.deactivate_connection,
            open_wifi_settings=self.open_wifi_settings,
            quit=self._quit,
            open_main_window=self.open_window,
            activate_proxy=self.activate_proxy,
            disable_proxy=self.disable_proxy,
            import_current_proxy=self.import_current_proxy,
            copy_proxy_snippet=self._copy_proxy_snippet,
            manage_proxy=self.open_proxy_page,
            apply_profile=self.apply_profile,
            clear_profile=self.clear_profile,
            capture_profile=self.capture_profile_prompt,
            manage_profiles=self.open_profiles_page,
        )

    # ── controller: Bộ cấu hình ─────────────────────────────────────────────

    @property
    def profiles(self) -> ProfileService:
        return self._profiles

    def apply_profile(self, profile_id: str) -> None:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return
        self._toast(f"Đang áp dụng '{profile.name}'…")
        self.refresh()          # menu khoá lại ngay, không chờ xong mới khoá
        self._profiles.apply(
            profile_id, on_done=self._on_profile_applied, on_progress=self._on_apply_progress
        )

    def _on_apply_progress(self, report) -> None:
        self._apply_progress = report
        if self._window is not None and self._window.get_visible():
            self._window.update_apply_progress(report)

    def _on_profile_applied(self, report) -> None:
        self._apply_progress = None
        self._toast(report.summary())
        if self._window is not None:
            self._window.update_apply_progress(None)
        self.refresh()

    def clear_profile(self) -> None:
        result = self._profiles.clear_active()
        self._toast(result.message or "Đã bỏ Bộ cấu hình")
        self.refresh()

    def capture_profile_prompt(self) -> None:
        """Từ tray thì không có chỗ nhập tên — mở cửa sổ để hỏi."""
        self.open_profiles_page()
        if self._window is not None:
            self._window.prompt_capture_profile()

    def open_proxy_page(self) -> None:
        self.open_window()
        if self._window is not None:
            self._window.select_page("proxy")

    def open_profiles_page(self) -> None:
        self.open_window()
        if self._window is not None:
            self._window.select_page("profiles")

    # ── controller: mạng ────────────────────────────────────────────────────

    def snapshot(self):
        return self._facade.snapshot()

    @property
    def proxy(self) -> ProxyService:
        return self._proxy

    def set_wifi_enabled(self, enabled: bool) -> None:
        self._facade.set_wifi_enabled(enabled, self._on_op_done)

    def activate_connection(self, uuid: str) -> None:
        self._facade.activate_connection(uuid, None, self._on_op_done)

    def deactivate_connection(self, uuid: str) -> None:
        self._facade.deactivate_connection(uuid, self._on_op_done)

    def _on_op_done(self, result: OpResult) -> None:
        """Thất bại phải nói ra — nuốt im lặng là kiểu hỏng khó chịu nhất."""
        if result.ok:
            self.refresh()
            return

        log.error(result.message)
        if self._window is not None and self._window.get_visible():
            self._toast(result.message)
        else:
            notification = Gio.Notification.new("Thao tác mạng thất bại")
            notification.set_body(result.message)
            notification.set_priority(Gio.NotificationPriority.HIGH)
            self.send_notification("netmgr-op-failed", notification)
        self.refresh()

    def _reassert_if_reconnected(self, snapshot) -> None:
        """Thiết bị vừa kết nối lại thì áp lại route của Bộ cấu hình cho nó.

        NetworkManager dựng lại cấu hình từ đĩa mỗi lần thiết bị lên, nên route
        runtime của Bộ cấu hình biến mất — rút rồi cắm lại cáp là mất. Chỉ áp cho
        thiết bị VỪA chuyển sang connected, chứ không áp mỗi lần refresh: sự kiện
        của NetworkManager rất dồn dập, còn `apply_runtime_routes` lại sinh ra
        sự kiện mới, nên áp bừa sẽ thành vòng lặp.
        """
        if self._profiles is None:
            return

        connected = {d.interface for d in snapshot.devices if d.is_connected}
        if self._connected_ifaces is None:
            self._connected_ifaces = connected      # lần đầu: xem mục do_startup
            return

        fresh = sorted(connected - self._connected_ifaces)
        self._connected_ifaces = connected
        if fresh:
            log.info("Thiết bị vừa kết nối lại: %s", ", ".join(fresh))
            self._profiles.reassert_routes(fresh)

    def read_runtime_ipv4(self, interface: str, callback) -> None:
        """Cấu hình IPv4 đang chạy trên thiết bị — dùng cho trang Định tuyến."""
        self._facade.read_runtime_ipv4(interface, callback)

    def open_wifi_settings(self) -> None:
        """Kết nối mạng Wi-Fi mới là việc của GNOME (§3.2.1) — mở thẳng tới đó."""
        try:
            Gio.Subprocess.new(["gnome-control-center", "wifi"], Gio.SubprocessFlags.NONE)
        except GLib.Error as exc:
            log.error("Không mở được cài đặt Wi-Fi: %s", exc.message)
            self._toast("Không mở được cài đặt Wi-Fi của hệ thống")

    # ── controller: proxy ───────────────────────────────────────────────────

    def activate_proxy(self, config_id: str) -> None:
        self._report_proxy(self._proxy.activate(config_id))

    def disable_proxy(self) -> None:
        self._report_proxy(self._proxy.disable())

    def save_proxy(self, config, password: str | None) -> None:
        result = self._proxy.save(config, password)
        if not result.ok:
            self._toast(result.message)
            return
        self._toast(f"Đã lưu '{config.name}'")
        # Đang bật chính cấu hình vừa sửa thì áp lại ngay, nếu không người dùng
        # tưởng đã đổi mà thực tế vẫn chạy giá trị cũ.
        if self._proxy.active is not None and self._proxy.active.id == config.id:
            self._proxy.activate(config.id)
        self.refresh()

    def delete_proxy(self, config_id: str) -> None:
        result = self._proxy.delete(config_id)
        self._toast("Đã xoá cấu hình proxy" if result.ok else result.message)
        self.refresh()

    def proxy_password(self, config_id: str) -> str | None:
        return self._proxy.get_password(config_id)

    def import_current_proxy(self) -> None:
        imported = self._proxy.import_current()
        if imported is None:
            self._toast("Không đọc được cấu hình proxy của hệ thống")
        else:
            self._toast(
                f"Đã nhập '{imported.name}' — giữ {len(imported.ignore_hosts)} mục bỏ qua"
            )
        self.refresh()

    def _copy_proxy_snippet(self) -> None:
        """Từ tray (không có cửa sổ) thì phải nhờ wl-copy."""
        snippet = self._proxy.export_snippet()
        if not snippet:
            self._toast("Proxy đang tắt — không có gì để chép")
            return
        try:
            proc = Gio.Subprocess.new(["wl-copy"], Gio.SubprocessFlags.STDIN_PIPE)
            proc.communicate_utf8(snippet, None)
            self._toast("Đã chép lệnh export")
        except GLib.Error as exc:
            log.warning("Không chép được vào clipboard (%s). Đoạn lệnh:\n%s",
                        exc.message, snippet)
            self._toast("Chưa cài wl-copy — đoạn lệnh đã in ra log")

    def _report_proxy(self, report: ApplyReport) -> None:
        if report.ok:
            log.info("Proxy '%s': %s", report.config_name, report.summary())
            self._toast(f"Proxy: {report.config_name}")
        else:
            # Thành công một phần vẫn phải báo: người dùng cần biết lớp nào hỏng.
            self._toast(report.summary())
        self.refresh()

    # ── thông báo ───────────────────────────────────────────────────────────

    def _toast(self, text: str) -> None:
        """Ưu tiên toast trong cửa sổ; không có cửa sổ thì dùng notification."""
        if self._window is not None and self._window.get_visible():
            self._window.toast(text)
            return
        notification = Gio.Notification.new("Quản lý mạng")
        notification.set_body(text)
        self.send_notification("netmgr-info", notification)

    def quit_app(self) -> None:
        """Thoát hẳn — dùng từ nút trong cửa sổ Tuỳ chọn."""
        self._quit()

    def _quit(self) -> None:
        self.release()
        self.quit()


def _make_secret_store():
    """Keyring nếu có; nếu không thì bản in-memory.

    Thiếu keyring chỉ làm proxy CÓ XÁC THỰC không dùng được — không đáng để app
    từ chối khởi động.
    """
    try:
        from .infra.secrets import SecretStore

        return SecretStore()
    except (ImportError, ValueError) as exc:
        from .infra.secrets import NullSecretStore

        log.warning("Không dùng được GNOME Keyring (%s) — mật khẩu proxy sẽ không "
                    "được lưu giữa các lần chạy", exc)
        return NullSecretStore()


def run(debug: bool = False, show_window: bool = False) -> int:
    try:
        app = NetmgrApp(show_window=show_window)
    except NMUnavailableError as exc:
        log.error("%s", exc)
        return 1

    # Ctrl+C khi chạy từ terminal phải thoát sạch, không để lại bus name treo.
    _unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, _on_sigint, app)
    _unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _on_sigint, app)

    if debug:
        log.debug("Chạy ở chế độ debug")
    return app.run(None)


def _on_sigint(app: NetmgrApp) -> bool:
    log.info("Nhận tín hiệu dừng, đang thoát")
    app.release()
    app.quit()
    return GLib.SOURCE_REMOVE
