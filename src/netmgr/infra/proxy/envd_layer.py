"""Lớp L2 — biến môi trường qua `~/.config/environment.d/`.

Đây là lớp mà terminal, `curl`, `git`, `pip`, `npm` thực sự đọc. gsettings (L1)
không ảnh hưởng gì tới chúng.

Hạn chế cố hữu: systemd chỉ đọc `environment.d` lúc khởi tạo session, nên thay
đổi **không có hiệu lực với shell đang mở**. Vì vậy lớp này cung cấp thêm
`export_snippet()` để người dùng dán ngay vào terminal hiện tại.

Không import `gi` — chỉ đọc/ghi file, nên test được hoàn toàn bằng tmp_path.
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path

from ...domain.models import OpResult, ProxyConfig, ProxyEndpoint, ProxyLayerId, ProxyMode
from .base import ProxyLayer

#: Đặt tiền tố số để thứ tự nạp xác định; tên có "netmgr" để người dùng biết
#: file này do app sinh ra và có thể xoá.
FILENAME = "10-netmgr-proxy.conf"

HEADER = """# File này do netmgr sinh ra tự động — SỬA TAY SẼ BỊ GHI ĐÈ.
# Chỉ có hiệu lực với session đăng nhập MỚI.
"""

_SCHEME_VARS = {
    "http": ["http_proxy", "HTTP_PROXY"],
    "https": ["https_proxy", "HTTPS_PROXY"],
    "ftp": ["ftp_proxy", "FTP_PROXY"],
    "socks": ["all_proxy", "ALL_PROXY"],
}
_NO_PROXY_VARS = ["no_proxy", "NO_PROXY"]

_ALL_VARS = [v for names in _SCHEME_VARS.values() for v in names] + _NO_PROXY_VARS

#: `10.*.*.*` → prefix 8, `192.168.*.*` → 16, `10.1.2.*` → 24
_WILDCARD_OCTETS = re.compile(r"^(\d{1,3})(?:\.(\d{1,3}|\*)){3}$")


def ignore_host_to_no_proxy(entry: str) -> str | None:
    """Chuyển một mục `ignore-hosts` của GNOME sang cú pháp `no_proxy`.

    GNOME dùng cú pháp riêng (`*.example.com`, `10.*.*.*`) mà curl/git không
    hiểu. Không chuyển thì proxy sẽ nuốt cả traffic nội bộ — kiểu hỏng rất khó
    chẩn đoán vì trình duyệt vẫn chạy đúng còn terminal thì không.

    Trả về None nếu mục đó không biểu diễn được.
    """
    entry = (entry or "").strip()
    if not entry:
        return None

    # `*.example.com` → `.example.com` (curl coi đó là khớp mọi subdomain)
    if entry.startswith("*."):
        return entry[1:]

    # CIDR và IP thuần giữ nguyên
    try:
        ipaddress.ip_network(entry, strict=False)
        return entry
    except ValueError:
        pass

    # `10.*.*.*` → `10.0.0.0/8`
    if "*" in entry and _WILDCARD_OCTETS.match(entry):
        octets = entry.split(".")
        fixed = [o for o in octets if o != "*"]
        if len(fixed) < 4 and all(o.isdigit() for o in fixed):
            prefix = len(fixed) * 8
            filled = fixed + ["0"] * (4 - len(fixed))
            try:
                network = ipaddress.ip_network(f"{'.'.join(filled)}/{prefix}", strict=False)
            except ValueError:
                return None
            return str(network)

    if "*" in entry:
        return None                # dạng wildcard khác: không dịch được

    return entry                   # hostname thuần


def build_no_proxy(ignore_hosts: list[str]) -> str:
    out: list[str] = []
    for entry in ignore_hosts:
        converted = ignore_host_to_no_proxy(entry)
        if converted and converted not in out:
            out.append(converted)
    return ",".join(out)


def _proxy_url(endpoint: ProxyEndpoint, scheme: str = "http") -> str:
    return f"{scheme}://{endpoint.host}:{endpoint.port}"


def build_env(config: ProxyConfig) -> dict[str, str]:
    """Biến ProxyConfig thành các biến môi trường cần đặt.

    Cố tình KHÔNG nhúng user:password vào URL: nó sẽ lộ ra trong `ps`, trong log
    của shell và trong biến môi trường của mọi tiến trình con.
    """
    if config.mode is not ProxyMode.MANUAL:
        # Chế độ PAC không có tương đương ở biến môi trường — không lớp nào
        # ngoài trình duyệt hiểu file PAC.
        return {}

    env: dict[str, str] = {}
    for scheme, names in _SCHEME_VARS.items():
        endpoint = config.effective(scheme)
        if not endpoint.is_set:
            continue
        url = _proxy_url(endpoint, "socks5" if scheme == "socks" else "http")
        for name in names:
            env[name] = url

    no_proxy = build_no_proxy(config.ignore_hosts)
    if no_proxy:
        for name in _NO_PROXY_VARS:
            env[name] = no_proxy
    return env


class EnvdLayer(ProxyLayer):
    id = ProxyLayerId.ENVIRONMENT
    name = "Biến môi trường (terminal, curl, git)"
    applies_immediately = False

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "environment.d"
        )

    @property
    def path(self) -> Path:
        return self.directory / FILENAME

    def is_available(self) -> bool:
        # Chỉ cần ghi được vào thư mục config của user.
        return True

    def apply(self, config: ProxyConfig, password: str | None = None) -> OpResult:
        env = build_env(config)
        if not env:
            return self.clear()

        lines = [HEADER.rstrip(), ""]
        lines += [f"{key}={value}" for key, value in env.items()]
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.path.chmod(0o600)
        except OSError as exc:
            return OpResult.failure(exc, f"Ghi {self.path}")
        return OpResult.success("Cần mở terminal mới để có hiệu lực")

    def clear(self) -> OpResult:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            return OpResult.failure(exc, f"Xoá {self.path}")
        return OpResult.success()

    def read(self) -> ProxyConfig | None:
        if not self.path.exists():
            return ProxyConfig(name="environment.d", mode=ProxyMode.NONE)

        try:
            content = self.path.read_text(encoding="utf-8")
        except OSError:
            return None

        values: dict[str, str] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()

        config = ProxyConfig(name="environment.d", mode=ProxyMode.NONE)
        http = values.get("http_proxy") or values.get("HTTP_PROXY")
        if http:
            endpoint = _parse_proxy_url(http)
            if endpoint is not None:
                config.mode = ProxyMode.MANUAL
                config.http = endpoint
        no_proxy = values.get("no_proxy") or values.get("NO_PROXY")
        if no_proxy:
            config.ignore_hosts = [h for h in no_proxy.split(",") if h]
        return config

    def export_snippet(self, config: ProxyConfig) -> str:
        """Đoạn lệnh dán vào shell ĐANG MỞ để có hiệu lực ngay.

        Bù cho hạn chế của environment.d — không có cái này thì người dùng phải
        đăng xuất/đăng nhập lại chỉ để `curl` đi qua proxy.
        """
        env = build_env(config)
        if not env:
            return "unset " + " ".join(_ALL_VARS)
        return "\n".join(f"export {key}={value}" for key, value in env.items())


def _parse_proxy_url(url: str) -> ProxyEndpoint | None:
    match = re.match(r"^(?:\w+://)?(?:[^@/]*@)?([^:/]+):(\d+)/?$", url.strip())
    if not match:
        return None
    return ProxyEndpoint(match.group(1), int(match.group(2)))
