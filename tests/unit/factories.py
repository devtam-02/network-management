"""Hàm dựng dữ liệu giả cho test.

`NetworkSnapshot` nằm ở tầng domain nên dựng được hoàn toàn bằng Python thuần —
test cho tray không cần NetworkManager, cũng không cần typelib NM.
"""

from __future__ import annotations

import uuid as uuid_mod

from netmgr.domain.models import (
    ConnectionProfile,
    ConnType,
    DeviceInfo,
    DeviceState,
    Ipv4Address,
    Ipv4Config,
    Ipv4Method,
    NetworkSnapshot,
    Permissions,
    PermissionState,
    WifiStatus,
)

ALL_ALLOWED = Permissions(
    modify_system=PermissionState.YES,
    modify_own=PermissionState.YES,
    network_control=PermissionState.YES,
    enable_disable_wifi=PermissionState.YES,
    checkpoint_rollback=PermissionState.AUTH,
)


def new_uuid() -> str:
    return str(uuid_mod.uuid4())


def wifi_device(
    interface: str = "wlp2s0",
    *,
    state: DeviceState = DeviceState.CONNECTED,
    ssid: str = "WiFi-CongTy",
    strength: int = 85,
    ip: str | None = "192.168.1.42",
    active_uuid: str | None = None,
) -> DeviceInfo:
    return DeviceInfo(
        interface=interface,
        type=ConnType.WIFI,
        state=state,
        mac="AA:BB:CC:DD:EE:FF",
        ip4_addresses=[Ipv4Address(ip, 24)] if ip else [],
        ip4_gateway="192.168.1.1" if ip else None,
        wifi=WifiStatus(
            ssid=ssid, strength=strength, frequency_mhz=5180, security="WPA2"
        ),
        active_connection_uuid=active_uuid,
    )


def eth_device(
    interface: str = "enp3s0",
    *,
    state: DeviceState = DeviceState.CONNECTED,
    carrier: bool = True,
    ip: str | None = "10.0.5.20",
    active_uuid: str | None = None,
) -> DeviceInfo:
    return DeviceInfo(
        interface=interface,
        type=ConnType.ETHERNET,
        state=state,
        mac="11:22:33:44:55:66",
        ip4_addresses=[Ipv4Address(ip, 24)] if ip else [],
        ip4_gateway="10.0.5.1" if ip else None,
        speed_mbps=1000,
        carrier=carrier,
        active_connection_uuid=active_uuid,
    )


def wifi_conn(
    name: str = "WiFi-CongTy",
    *,
    uuid: str | None = None,
    active: bool = False,
    interface: str | None = "wlp2s0",
    method: Ipv4Method = Ipv4Method.AUTO,
) -> ConnectionProfile:
    return ConnectionProfile(
        uuid=uuid or new_uuid(),
        id=name,
        type=ConnType.WIFI,
        interface_name=interface,
        ipv4=Ipv4Config(method=method),
        ssid=name,
        is_active=active,
        device_interface=interface if active else None,
    )


def eth_conn(
    name: str = "LAN Công ty",
    *,
    uuid: str | None = None,
    active: bool = False,
    interface: str | None = "enp3s0",
    method: Ipv4Method = Ipv4Method.AUTO,
) -> ConnectionProfile:
    return ConnectionProfile(
        uuid=uuid or new_uuid(),
        id=name,
        type=ConnType.ETHERNET,
        interface_name=interface,
        ipv4=Ipv4Config(method=method),
        is_active=active,
        device_interface=interface if active else None,
    )


def snapshot(
    *,
    devices=None,
    connections=None,
    networking_enabled: bool = True,
    wifi_enabled: bool = True,
    wifi_hardware_enabled: bool = True,
    permissions: Permissions | None = None,
) -> NetworkSnapshot:
    return NetworkSnapshot(
        devices=list(devices or []),
        connections=list(connections or []),
        networking_enabled=networking_enabled,
        wifi_enabled=wifi_enabled,
        wifi_hardware_enabled=wifi_hardware_enabled,
        permissions=permissions or ALL_ALLOWED,
    )
