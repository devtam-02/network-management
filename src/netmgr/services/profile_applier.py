"""Áp dụng một Bộ cấu hình — luồng 9 bước ở §3.5.4.

Đây là thao tác dễ làm mất mạng nhất trong app, nên thiết kế xoay quanh hai điều:

1. **Thứ tự bước có ý nghĩa.** Bật radio TRƯỚC khi kết nối, tắt radio SAU CÙNG,
   áp proxy sớm (không phụ thuộc mạng). Sai thứ tự sẽ tự cắt mạng giữa chừng.
2. **Luôn chụp trạng thái trước khi đụng vào gì.** Thất bại ở bước bắt buộc thì
   khôi phục lại. Bản chụp này không cần quyền đặc biệt, khác với NM checkpoint
   (xem `use_checkpoint`).

Không import `gi`: mọi thao tác mạng đi qua đối tượng `network` được tiêm vào,
nên toàn bộ luồng test được bằng bản giả.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..domain.models import (
    ApplyStep,
    BindingAction,
    ConnType,
    DeviceState,
    Profile,
    ProfileApplyReport,
    StepStatus,
)

log = logging.getLogger(__name__)

#: Chờ tối đa bao lâu cho một connection lên trạng thái CONNECTED.
ACTIVATION_TIMEOUT_MS = 30_000
#: Nhịp kiểm tra trạng thái trong lúc chờ.
POLL_INTERVAL_MS = 250

#: (khoá, nhãn hiển thị) — thứ tự chính là thứ tự thực thi.
STEP_DEFS = [
    ("validate", "Kiểm tra Bộ cấu hình"),
    ("snapshot", "Ghi nhớ trạng thái hiện tại"),
    ("radio_on", "Bật Wi-Fi"),
    ("proxy", "Áp dụng proxy"),
    ("deactivate", "Ngắt các kết nối không cần"),
    ("activate", "Kích hoạt kết nối"),
    ("verify", "Kiểm tra kết quả"),
    ("routes", "Áp route của Bộ cấu hình"),
    ("radio_off", "Tắt Wi-Fi"),
]


@dataclass(slots=True)
class _Restore:
    """Trạng thái cần khôi phục nếu áp dụng thất bại."""

    #: (uuid, interface) — phải nhớ cả thiết bị, xem `_rollback`.
    active_uuids: list[tuple[str, str | None]] = field(default_factory=list)
    wifi_enabled: bool | None = None
    proxy_id: str | None = None
    proxy_was_enabled: bool = False
    #: Interface đã bị áp route runtime — cần trả về cấu hình gốc khi rollback.
    touched_interfaces: list[str] = field(default_factory=list)


class ProfileApplier:
    def __init__(
        self,
        network,
        proxy,
        *,
        schedule=None,
        cancel_schedule=None,
        use_checkpoint: bool = False,
    ) -> None:
        """
        network: đối tượng có snapshot/set_wifi_enabled/activate/deactivate.
        proxy:   ProxyService.
        schedule: hàm (ms, callback) -> id, mặc định dùng GLib khi chạy thật.
        use_checkpoint: NM checkpoint là lưới an toàn tốt hơn bản chụp thủ công,
            nhưng trên Ubuntu nó đòi xác thực polkit (`checkpoint-rollback = auth`),
            tức mỗi lần chuyển Bộ cấu hình sẽ hiện hộp thoại mật khẩu. Mặc định
            tắt; bản chụp thủ công đã đủ cho trường hợp thường gặp.
        """
        self._network = network
        self._proxy = proxy
        self._schedule = schedule
        self._cancel_schedule = cancel_schedule
        self._use_checkpoint = use_checkpoint
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    # ── điểm vào ────────────────────────────────────────────────────────────

    def apply(
        self,
        profile: Profile,
        *,
        on_done: Callable[[ProfileApplyReport], None],
        on_progress: Callable[[ProfileApplyReport], None] | None = None,
    ) -> None:
        if self._busy:
            report = ProfileApplyReport(profile.name)
            report.steps = [
                ApplyStep("validate", "Kiểm tra Bộ cấu hình", StepStatus.FAILED,
                          "Đang áp dụng một Bộ cấu hình khác")
            ]
            on_done(report)
            return

        self._busy = True
        self._profile = profile
        self._on_done = on_done
        self._on_progress = on_progress
        self._report = ProfileApplyReport(
            profile.name, [ApplyStep(k, label) for k, label in STEP_DEFS]
        )
        self._restore = _Restore()
        self._index = 0
        self._run_next()

    # ── bộ chạy tuần tự ─────────────────────────────────────────────────────

    def _run_next(self) -> None:
        if self._index >= len(STEP_DEFS):
            self._finish()
            return

        key = STEP_DEFS[self._index][0]
        step = self._report.step(key)
        step.status = StepStatus.RUNNING
        self._emit()

        handler = getattr(self, f"_step_{key}")
        handler(self._step_done)

    def _step_done(self, ok: bool, detail: str = "", *, skipped: bool = False) -> None:
        step = self._report.step(STEP_DEFS[self._index][0])
        step.status = (
            StepStatus.SKIPPED if skipped
            else StepStatus.OK if ok
            else StepStatus.FAILED
        )
        step.detail = detail
        self._emit()

        if not ok and not skipped:
            self._rollback()
            return

        self._index += 1
        self._run_next()

    def _emit(self) -> None:
        if self._on_progress is not None:
            self._on_progress(self._report)

    def _finish(self) -> None:
        self._busy = False
        self._on_done(self._report)

    # ── các bước ────────────────────────────────────────────────────────────

    def _step_validate(self, done) -> None:
        snapshot = self._network.snapshot()
        for binding in self._profile.bindings:
            if binding.action is BindingAction.LEAVE_ALONE:
                continue

            device = self._match_device(binding, snapshot)
            if device is None:
                if binding.required:
                    done(False, f"Không tìm thấy thiết bị '{binding.device_match}'")
                    return
                continue        # không bắt buộc → bỏ qua, không phải lỗi

            if binding.action is BindingAction.ACTIVATE:
                # Không bắt buộc chọn connection: để trống nghĩa là "bật thiết
                # bị này lên", NetworkManager tự chọn cấu hình phù hợp.
                if binding.connection_uuid and \
                        snapshot.connection_by_uuid(binding.connection_uuid) is None:
                    done(False, f"Cấu hình kết nối của '{binding.device_match}' "
                                "không còn tồn tại")
                    return

        if self._profile.touches_proxy and not self._profile.disables_proxy:
            if self._proxy.get(self._profile.proxy_id) is None:
                done(False, "Cấu hình proxy không còn tồn tại")
                return
        done(True)

    def _step_snapshot(self, done) -> None:
        snapshot = self._network.snapshot()
        self._restore.active_uuids = [
            (c.uuid, c.device_interface) for c in snapshot.connections if c.is_active
        ]
        self._restore.wifi_enabled = snapshot.wifi_enabled
        active_proxy = self._proxy.active
        self._restore.proxy_id = active_proxy.id if active_proxy else None
        self._restore.proxy_was_enabled = self._proxy.is_enabled
        done(True, f"{len(self._restore.active_uuids)} kết nối đang chạy")

    def _step_radio_on(self, done) -> None:
        # Bật TRƯỚC khi kết nối: không có radio thì không kết nối Wi-Fi được.
        want = self._profile.wifi_enabled
        if want is None and self._wants_wifi_device():
            # Bật công tắc Wi-Fi trong Bộ cấu hình nghĩa là "bật Wi-Fi lên".
            # Không suy ra điều này thì radio vẫn tắt và bước kích hoạt chắc
            # chắn thất bại.
            want = True
        if want is not True:
            done(True, skipped=True)
            return
        if self._network.snapshot().wifi_enabled:
            done(True, skipped=True)
            return
        self._network.set_wifi_enabled(True, lambda r: done(r.ok, r.message))

    def _wants_wifi_device(self) -> bool:
        snapshot = self._network.snapshot()
        for binding in self._profile.bindings:
            if binding.action is not BindingAction.ACTIVATE:
                continue
            device = self._match_device(binding, snapshot)
            if device is not None and device.type is ConnType.WIFI:
                return True
        return False

    def _step_proxy(self, done) -> None:
        # Áp sớm: không phụ thuộc mạng và không gây mất kết nối, nên làm trước
        # phần dễ hỏng.
        if not self._profile.touches_proxy:
            done(True, skipped=True)
            return
        if self._profile.disables_proxy:
            report = self._proxy.disable()
        else:
            report = self._proxy.activate(self._profile.proxy_id)
        done(report.ok, report.summary())

    def _step_deactivate(self, done) -> None:
        snapshot = self._network.snapshot()
        targets = []
        for binding in self._profile.bindings:
            if binding.action is not BindingAction.DISCONNECT:
                continue
            device = self._match_device(binding, snapshot)
            if device is not None and device.active_connection_uuid:
                targets.append(device.active_connection_uuid)

        if not targets:
            done(True, skipped=True)
            return
        self._chain(
            targets,
            lambda uuid, cb: self._network.deactivate_connection(uuid, cb),
            done,
            f"Đã ngắt {len(targets)} kết nối",
        )

    def _step_activate(self, done) -> None:
        snapshot = self._network.snapshot()
        # Theo dõi theo INTERFACE chứ không theo uuid: Bộ cấu hình giờ chỉ nói
        # "bật thiết bị này", connection để NetworkManager tự chọn.
        self._pending_interfaces: list[str] = []
        to_activate: list[tuple[str | None, str]] = []
        #: Wi-Fi: chỉ cần radio bật là đủ. Còn kết nối được hay không phụ thuộc
        #: mạng đã lưu có trong tầm phủ hay không — thứ Bộ cấu hình không kiểm
        #: soát được. Bắt buộc nó thành công sẽ làm cả Bộ cấu hình thất bại với
        #: lỗi "the device has no connections" chỉ vì đang ở nơi không có Wi-Fi
        #: quen, dù mạng dây vẫn hoàn toàn ổn.
        best_effort: list[tuple[str | None, str]] = []
        already_ok = 0

        for binding in self._profile.bindings:
            if binding.action is not BindingAction.ACTIVATE:
                continue
            device = self._match_device(binding, snapshot)
            if device is None:
                continue        # đã kiểm ở bước validate: không bắt buộc

            optional = device.type is ConnType.WIFI
            if not optional:
                # Chỉ chờ ở bước verify những thiết bị BẮT BUỘC phải lên.
                self._pending_interfaces.append(device.interface)
            already = (
                device.state is DeviceState.CONNECTED
                and (
                    binding.connection_uuid is None
                    or device.active_connection_uuid == binding.connection_uuid
                )
            )
            if already:
                # Đã đúng rồi thì ĐỪNG activate lại: NM sẽ kết nối lại và làm
                # rớt mạng một nhịp hoàn toàn không cần thiết. Rất hay gặp khi
                # áp dụng lại chính Bộ cấu hình đang chạy.
                already_ok += 1
                continue
            # Kèm interface: Bộ cấu hình đã chỉ rõ thiết bị nào, không để NM
            # tự chọn rồi giữ nguyên connection ở chỗ cũ.
            (best_effort if optional else to_activate).append(
                (binding.connection_uuid, device.interface)
            )

        def activate(pair, cb) -> None:
            self._network.activate_connection(pair[0], pair[1], cb)

        def finish_required(ok: bool, detail: str = "", *, skipped: bool = False) -> None:
            if not ok:
                done(ok, detail)
                return
            # Thử Wi-Fi sau cùng và bỏ qua mọi lỗi.
            remaining = list(best_effort)
            failed: list[str] = []

            def next_one() -> None:
                if not remaining:
                    bits = [detail] if detail else []
                    if best_effort:
                        okc = len(best_effort) - len(failed)
                        bits.append(f"Wi-Fi: {okc}/{len(best_effort)} kết nối được")
                    done(True, " · ".join(bits), skipped=skipped and not best_effort)
                    return
                pair = remaining.pop(0)
                self._network.activate_connection(
                    pair[0], pair[1], lambda r: on_result(pair, r)
                )

            def on_result(pair, result) -> None:
                if not result.ok:
                    failed.append(pair[1])
                    log.info(
                        "Wi-Fi %s không kết nối được (bỏ qua): %s",
                        pair[1], result.message,
                    )
                next_one()

            next_one()

        if not to_activate:
            detail = f"{already_ok} kết nối đã đúng sẵn" if already_ok else ""
            finish_required(True, detail, skipped=not already_ok)
            return

        suffix = f" ({already_ok} đã đúng sẵn)" if already_ok else ""
        self._chain(
            to_activate, activate, finish_required,
            f"Đã yêu cầu kích hoạt {len(to_activate)} kết nối{suffix}",
        )

    def _step_verify(self, done) -> None:
        """Chờ tới khi các thiết bị thực sự lên CONNECTED.

        `activate_connection` trả về khi NM *nhận* yêu cầu, chưa phải khi kết nối
        xong. Không chờ ở đây thì bước áp route chạy lúc thiết bị còn CONNECTING
        và bị bỏ qua âm thầm — báo thành công nhưng route không được áp.
        """
        wanted = getattr(self, "_pending_interfaces", [])
        if not wanted:
            done(True, skipped=True)
            return

        def connected() -> bool:
            snapshot = self._network.snapshot()
            for iface in wanted:
                device = snapshot.device_by_interface(iface)
                if device is None:
                    continue        # thiết bị đã rút: bước validate đã cho qua
                if device.state is not DeviceState.CONNECTED:
                    return False
            return True

        self._wait_until(
            connected,
            on_ready=lambda: done(True, f"{len(wanted)} kết nối đã sẵn sàng"),
            on_timeout=lambda: done(False, "Hết thời gian chờ kết nối"),
        )

    def _step_routes(self, done) -> None:
        """Áp route riêng của Bộ cấu hình — CHỈ ở runtime.

        App không bao giờ ghi vào cấu hình đã lưu của máy. Route sống trong Bộ
        cấu hình và được `reapply` lên thiết bị đang chạy, nên tắt Bộ cấu hình
        hoặc thoát app là máy trở về đúng cấu hình gốc.

        Mọi thiết bị Bộ cấu hình bật lên đều được đặt lại, kể cả thiết bị KHÔNG
        có route riêng — thiết bị đó về Automatic. Nếu chỉ chạm vào thiết bị có
        route thì một thiết bị từng bị tắt Automatic sẽ giữ nguyên trạng thái
        đó, và Bộ cấu hình không còn quyết định được bảng route của mình.
        """
        snapshot = self._network.snapshot()
        targets: list[tuple[str, list, bool]] = []
        for binding in self._profile.routing_bindings:
            device = self._match_device(binding, snapshot)
            if device is None or device.state is not DeviceState.CONNECTED:
                continue
            targets.append(
                (device.interface, list(binding.routes), not binding.automatic_routes)
            )

        if not targets:
            done(True, skipped=True)
            return

        # Nhớ lại để rollback và để "Không dùng Bộ cấu hình" khôi phục được.
        self._restore.touched_interfaces = [iface for iface, _r, _i in targets]
        remaining = list(targets)

        def step_one() -> None:
            if not remaining:
                manual = sum(1 for _i, routes, _f in targets if routes)
                auto = len(targets) - manual
                bits = []
                if manual:
                    bits.append(f"{manual} thiết bị dùng route riêng")
                if auto:
                    bits.append(f"{auto} thiết bị về Automatic")
                done(True, ", ".join(bits))
                return
            iface, routes, ignore_auto = remaining.pop(0)
            self._network.apply_runtime_routes(iface, routes, ignore_auto, on_result)

        def on_result(result) -> None:
            if not result.ok:
                done(False, result.message)
                return
            step_one()

        step_one()

    def _step_radio_off(self, done) -> None:
        # Tắt CUỐI CÙNG: tắt sớm sẽ cắt mất kết nối Wi-Fi đang cần dùng ở các
        # bước trên.
        if self._profile.wifi_enabled is not False:
            done(True, skipped=True)
            return
        if not self._network.snapshot().wifi_enabled:
            done(True, skipped=True)
            return
        self._network.set_wifi_enabled(False, lambda r: done(r.ok, r.message))

    # ── rollback ────────────────────────────────────────────────────────────

    def _rollback(self) -> None:
        """Đưa về trạng thái đã chụp ở bước 2.

        Chạy best-effort: từng phần khôi phục độc lập, một phần hỏng không được
        chặn các phần còn lại.
        """
        log.warning("Áp dụng '%s' thất bại — đang khôi phục", self._profile.name)
        self._report.rolled_back = True
        for step in self._report.steps:
            if step.status is StepStatus.OK:
                step.status = StepStatus.ROLLED_BACK
        self._emit()

        # Trả cấu hình runtime về đúng bản đã lưu của máy trước tiên: đây là
        # thứ ảnh hưởng trực tiếp tới đường đi của gói tin.
        for iface in self._restore.touched_interfaces:
            self._network.restore_runtime(iface, lambda _r: None)

        if self._restore.proxy_was_enabled and self._restore.proxy_id:
            self._proxy.activate(self._restore.proxy_id)
        elif self._proxy.is_enabled:
            self._proxy.disable()

        if self._restore.wifi_enabled is not None:
            if self._network.snapshot().wifi_enabled != self._restore.wifi_enabled:
                self._network.set_wifi_enabled(self._restore.wifi_enabled, lambda _r: None)

        current = {c.uuid for c in self._network.snapshot().connections if c.is_active}
        for uuid, iface in self._restore.active_uuids:
            if uuid in current:
                continue
            # Phải nêu rõ interface. Một profile không ghim `interface-name`
            # (rất phổ biến với cổng USB) mà khôi phục với device=None sẽ bị NM
            # gán sang cổng khác — rollback lại đá văng đúng thiết bị vừa lên.
            self._network.activate_connection(uuid, iface, lambda _r: None)

        self._finish()

    # ── tiện ích ────────────────────────────────────────────────────────────

    @staticmethod
    def _match_device(binding, snapshot):
        return next((d for d in snapshot.devices if binding.matches(d)), None)

    def _chain(self, items: list, operation, done, success_detail: str) -> None:
        """Chạy `operation` lần lượt cho từng phần tử; dừng ngay khi có lỗi."""
        remaining = list(items)

        def step_one() -> None:
            if not remaining:
                done(True, success_detail)
                return
            operation(remaining.pop(0), on_result)

        def on_result(result) -> None:
            if not result.ok:
                done(False, result.message)
                return
            step_one()

        step_one()

    def _wait_until(self, predicate, *, on_ready, on_timeout) -> None:
        if predicate():
            on_ready()
            return
        if self._schedule is None:
            # Không có bộ hẹn giờ (test đồng bộ) thì không chờ được — coi như
            # chưa sẵn sàng, tốt hơn là im lặng báo thành công.
            on_timeout()
            return

        elapsed = {"ms": 0}

        def tick() -> bool:
            if predicate():
                on_ready()
                return False
            elapsed["ms"] += POLL_INTERVAL_MS
            if elapsed["ms"] >= ACTIVATION_TIMEOUT_MS:
                on_timeout()
                return False
            return True

        self._schedule(POLL_INTERVAL_MS, tick)
