"""CLI — chủ yếu để kiểm chứng NMFacade với NetworkManager thật.

`netmgr dump` in ra đúng những gì facade đọc được. Đây là cách nhanh nhất để
phát hiện mình hiểu sai API của libnm, trước khi tin vào nó ở tầng UI.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__


def _fmt_routes(routes, indent: str) -> list[str]:
    if not routes:
        return [f"{indent}(không có)"]
    return [f"{indent}{'🔵' if r.editable else '⚪'} {r}" for r in routes]


def cmd_dump(_args: argparse.Namespace) -> int:
    from .infra.nm_facade import NMFacade, NMUnavailableError

    try:
        facade = NMFacade()
    except NMUnavailableError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1

    facade.wait_for_permissions()
    snap = facade.snapshot()

    print(f"NetworkManager {facade.nm_version}")
    print(f"  networking: {'bật' if snap.networking_enabled else 'tắt'}")
    print(f"  wifi:       {'bật' if snap.wifi_enabled else 'tắt'}"
          f" (phần cứng: {'bật' if snap.wifi_hardware_enabled else 'tắt'})")
    perm = snap.permissions
    print(f"  quyền (nạp xong: {'có' if perm.loaded else 'CHƯA — cần main loop chạy'}):")
    print(f"      sửa cấu hình hệ thống: {perm.modify_system.value}")
    print(f"      điều khiển mạng:       {perm.network_control.value}")
    print(f"      bật/tắt wifi:          {perm.enable_disable_wifi.value}")
    print(f"      checkpoint rollback:   {perm.checkpoint_rollback.value}")

    print(f"\n── Devices ({len(snap.devices)}) ──")
    for d in snap.devices:
        head = f"  {d.interface}  [{d.type.name.lower()}]  {d.state.value}"
        if d.type.name == "ETHERNET" and not d.carrier:
            head += "  (chưa cắm cáp)"
        print(head)
        if d.mac:
            print(f"      mac:      {d.mac}")
        if d.wifi:
            w = d.wifi
            print(f"      wifi:     {w.ssid}  {w.strength}% ({w.signal_bars}/4)"
                  f"  {w.band}  {w.security}  {w.bitrate_kbps // 1000} Mb/s")
        if d.speed_mbps:
            print(f"      speed:    {d.speed_mbps} Mb/s")
        if d.ip4_addresses:
            print(f"      ip:       {', '.join(str(a) for a in d.ip4_addresses)}")
        if d.ip4_gateway:
            print(f"      gateway:  {d.ip4_gateway}")
        if d.ip4_dns:
            print(f"      dns:      {', '.join(d.ip4_dns)}")
        if d.active_connection_uuid:
            conn = snap.connection_by_uuid(d.active_connection_uuid)
            print(f"      active:   {conn.display_name if conn else d.active_connection_uuid}")
        print("      routes (runtime):")
        for line in _fmt_routes(d.ip4_routes, "        "):
            print(line)

    # Chỉ hiện ethernet/wifi. lo, docker0, br-*, tailscale0... NM cũng quản lý
    # nhưng nằm ngoài phạm vi app (§1.4), hiện ra chỉ làm nhiễu.
    relevant = snap.ethernet_connections() + snap.saved_wifi_connections()
    others = len(snap.connections) - len(relevant)
    print(f"\n── Connections ({len(relevant)}, bỏ qua {others} loại khác) ──")
    for c in sorted(relevant, key=lambda x: (x.type.value, x.id)):
        mark = "●" if c.is_active else "○"
        managed = "  [managed]" if c.managed_by_app else ""
        print(f"  {mark} {c.display_name}  [{c.type.name.lower()}]{managed}")
        print(f"      uuid:        {c.uuid}")
        if c.interface_name:
            print(f"      interface:   {c.interface_name}")
        if c.ssid:
            print(f"      ssid:        {c.ssid}")
        print(f"      autoconnect: {c.autoconnect} (ưu tiên {c.autoconnect_priority})")

        ip4 = c.ipv4
        print(f"      ipv4.method: {ip4.method.value}"
              f"{'  ← Automatic' if ip4.method.is_automatic else ''}")
        if ip4.addresses:
            print(f"      addresses:   {', '.join(str(a) for a in ip4.addresses)}")
        if ip4.gateway:
            print(f"      gateway:     {ip4.gateway}")
        if ip4.dns:
            print(f"      dns:         {', '.join(ip4.dns)}")
        flags = [
            name for name, on in (
                ("ignore-auto-dns", ip4.ignore_auto_dns),
                ("ignore-auto-routes", ip4.ignore_auto_routes),
                ("never-default", ip4.never_default),
                ("may-fail=no", not ip4.may_fail),
            ) if on
        ]
        if flags:
            print(f"      flags:       {', '.join(flags)}")
        print("      routes (tĩnh):")
        for line in _fmt_routes(ip4.routes, "        "):
            print(line)

    primary = snap.primary_device()
    print(f"\nDevice chính (dùng cho icon tray): "
          f"{primary.interface if primary else '(không có)'}")

    facade.close()
    return 0


def cmd_tray(args: argparse.Namespace) -> int:
    from .app import run

    return run(debug=args.debug)


def cmd_window(args: argparse.Namespace) -> int:
    """Chạy app và mở luôn cửa sổ cấu hình.

    Tray vẫn hoạt động bình thường; lệnh này chỉ đỡ phải bấm vào menu.
    """
    from .app import run

    return run(debug=args.debug, show_window=True)


def build_parser() -> argparse.ArgumentParser:
    # Cờ dùng chung khai báo ở parser cha để `netmgr tray --debug` và
    # `netmgr --debug tray` đều chạy — gõ nhầm thứ tự là chuyện thường.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-d", "--debug", action="store_true", help="Bật log chi tiết")

    parser = argparse.ArgumentParser(
        prog="netmgr",
        description="Quản lý mạng & proxy cho Ubuntu",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"netmgr {__version__}")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "dump", parents=[common], help="In trạng thái mạng NMFacade đọc được"
    ).set_defaults(func=cmd_dump)
    sub.add_parser(
        "tray", parents=[common], help="Chạy tray icon (mặc định)"
    ).set_defaults(func=cmd_tray)
    sub.add_parser(
        "window", parents=[common], help="Chạy và mở luôn cửa sổ cấu hình"
    ).set_defaults(func=cmd_window)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    func = getattr(args, "func", cmd_tray)   # không có subcommand → chạy tray
    return func(args)
