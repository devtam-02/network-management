"""Bản giả của tầng mạng và proxy — cho test luồng áp dụng Bộ cấu hình.

Đủ thật để phản ánh đúng ngữ nghĩa quan trọng: kích hoạt một connection sẽ ngắt
connection khác trên cùng device, và device chuyển sang CONNECTED.
"""

from __future__ import annotations

from netmgr.domain.models import DeviceState, OpResult, ProxyConfig


class FakeNetwork:
    def __init__(self, snapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[tuple[str, object]] = []
        #: {tên thao tác: thông báo lỗi} để mô phỏng thất bại.
        self.fail: dict[str, str] = {}
        #: True = activate chỉ "nhận yêu cầu", device không lên CONNECTED.
        self.activation_hangs = False
        #: Route đã áp ở runtime, theo interface.
        self.runtime_routes: dict[str, list] = {}
        self.last_activate_interface: str | None = None
        self.last_ignore_auto: bool | None = None
        #: uuid -> Automatic đã ghi xuống cấu hình đã lưu.
        self.stored_modes: dict[str, bool] = {}
        #: interface -> thông báo lỗi khi kích hoạt thiết bị đó.
        self.fail_activate: dict[str, str] = {}

    def snapshot(self):
        return self._snapshot

    # ── thao tác ────────────────────────────────────────────────────────────

    def set_wifi_enabled(self, enabled: bool, callback) -> None:
        self.calls.append(("wifi", enabled))
        if "wifi" in self.fail:
            callback(OpResult(False, self.fail["wifi"]))
            return
        self._snapshot.wifi_enabled = enabled
        if not enabled:
            for device in self._snapshot.wifi_devices:
                self._detach(device)
                device.state = DeviceState.UNAVAILABLE
        callback(OpResult.success())

    def activate_connection(self, uuid: str, interface=None, callback=None) -> None:
        self.calls.append(("activate", uuid))
        #: Interface mà lần activate gần nhất nhắm tới — None nghĩa là để NM chọn.
        self.last_activate_interface = interface
        # NM thất bại theo từng thiết bị, không phải toàn bộ: đứng ở nơi không có
        # Wi-Fi quen thì chỉ Wi-Fi lỗi, mạng dây vẫn lên.
        if interface in self.fail_activate:
            callback(OpResult(False, self.fail_activate[interface]))
            return
        if "activate" in self.fail:
            callback(OpResult(False, self.fail["activate"]))
            return

        conn = self._snapshot.connection_by_uuid(uuid) if uuid else None
        if uuid and conn is None:
            callback(OpResult(False, "không tìm thấy"))
            return

        device = self._snapshot.device_by_interface(
            interface or (conn.interface_name if conn else "") or ""
        )
        # uuid=None nghĩa là "bật thiết bị này lên", NetworkManager tự chọn cấu
        # hình phù hợp. Bản giả phải làm y vậy, nếu không thiết bị sẽ ở trạng
        # thái CONNECTED mà không có connection nào — điều không xảy ra thật.
        if conn is None and device is not None:
            conn = next(
                (c for c in self._snapshot.connections
                 if c.type is device.type
                 and c.interface_name in (None, "", device.interface)),
                None,
            )
        if device is not None:
            self._detach(device)
            device.active_connection_uuid = conn.uuid if conn else uuid
            device.state = (
                DeviceState.CONNECTING if self.activation_hangs else DeviceState.CONNECTED
            )
            if conn is not None:
                conn.device_interface = device.interface
        if conn is not None:
            conn.is_active = True
        callback(OpResult.success())

    def deactivate_connection(self, uuid: str, callback) -> None:
        self.calls.append(("deactivate", uuid))
        if "deactivate" in self.fail:
            callback(OpResult(False, self.fail["deactivate"]))
            return
        conn = self._snapshot.connection_by_uuid(uuid)
        if conn is not None:
            conn.is_active = False
            conn.device_interface = None
        for device in self._snapshot.devices:
            if device.active_connection_uuid == uuid:
                device.active_connection_uuid = None
                device.state = DeviceState.DISCONNECTED
        callback(OpResult.success())

    def apply_runtime_routes(
        self, interface: str, routes, ignore_auto=True, callback=None
    ) -> None:
        self.calls.append(("routes", interface))
        self.last_ignore_auto = ignore_auto
        if "routes" in self.fail:
            callback(OpResult(False, self.fail["routes"]))
            return
        self.runtime_routes[interface] = list(routes)
        callback(OpResult.success())

    def set_stored_automatic_routes(self, uuid: str, automatic: bool, callback=None) -> None:
        self.calls.append(("store_mode", uuid))
        self.stored_modes[uuid] = automatic
        if callback is not None:
            callback(OpResult.success())

    def restore_runtime(self, interface: str, callback) -> None:
        self.calls.append(("restore", interface))
        self.runtime_routes.pop(interface, None)
        callback(OpResult.success())

    def _detach(self, device) -> None:
        """Một device chỉ chạy một connection tại một thời điểm."""
        if device.active_connection_uuid is None:
            return
        previous = self._snapshot.connection_by_uuid(device.active_connection_uuid)
        if previous is not None:
            previous.is_active = False
            previous.device_interface = None
        device.active_connection_uuid = None

    # ── tiện ích cho test ───────────────────────────────────────────────────

    def operations(self) -> list[str]:
        return [name for name, _arg in self.calls]


class FakeProxyService:
    def __init__(self, configs=None) -> None:
        self._configs = {c.id: c for c in (configs or [])}
        self._active_id: str | None = None
        self.calls: list[tuple[str, object]] = []
        self.fail = False

    # ── API mà ProfileApplier dùng ──
    def get(self, config_id: str) -> ProxyConfig | None:
        return self._configs.get(config_id)

    @property
    def active(self) -> ProxyConfig | None:
        return self._configs.get(self._active_id) if self._active_id else None

    @property
    def is_enabled(self) -> bool:
        return self._active_id is not None

    def activate(self, config_id: str):
        self.calls.append(("activate", config_id))
        if self.fail:
            return _Report(False, "proxy hỏng")
        self._active_id = config_id
        return _Report(True, "ok")

    def disable(self):
        self.calls.append(("disable", None))
        if self.fail:
            return _Report(False, "proxy hỏng")
        self._active_id = None
        return _Report(True, "ok")


class _Report:
    def __init__(self, ok: bool, summary: str) -> None:
        self.ok = ok
        self._summary = summary

    def summary(self) -> str:
        return self._summary


class ImmediateScheduler:
    """Bộ hẹn giờ chạy callback ngay lập tức, tối đa `limit` lần.

    Cho phép test bước `verify` (vốn phải chờ) mà không cần GLib main loop.
    """

    def __init__(self, limit: int = 200) -> None:
        self.limit = limit
        self.ticks = 0

    def __call__(self, _interval_ms: int, callback) -> int:
        while self.ticks < self.limit:
            self.ticks += 1
            if not callback():
                return 0
        return 0
