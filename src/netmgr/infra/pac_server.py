"""Phục vụ file PAC qua http://127.0.0.1 — vì `file://` không dùng được với trình duyệt.

Đo được trên máy: bộ giải của GLib (`GProxyResolverGnome`, thứ các ứng dụng GNOME
dùng) đọc được cả `file://` lẫn `http://`. Nhưng Chrome/Chromium từ chối PAC dạng
`file://` vì lý do bảo mật, nên cấu hình chỉ có `file://` sẽ "chạy" ở chỗ này mà
im lặng không có tác dụng ở chỗ khác — kiểu lỗi tệ nhất.

Phục vụ qua HTTP giải quyết cả hai. Chỉ nghe trên 127.0.0.1: PAC nói ra mạng nội
bộ đi đường nào, không có lý do gì để máy khác đọc được.

Đọc lại file trên mỗi request thay vì nạp sẵn vào bộ nhớ, để sửa file PAC là có
hiệu lực ngay mà không phải khởi động lại app.
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

#: Cổng mặc định. Phải ỔN ĐỊNH giữa các lần chạy vì `pac_url` được lưu xuống
#: gsettings: cổng nhảy mỗi lần khởi động thì cấu hình cũ trỏ vào chỗ không có gì.
DEFAULT_PORT = 21777
#: Cổng bị chiếm thì thử vài cổng kế tiếp trước khi bỏ.
PORT_ATTEMPTS = 8

#: MIME type chuẩn của PAC. Sai kiểu này thì một số bộ giải bỏ qua file.
CONTENT_TYPE = "application/x-ns-proxy-autoconfig"

#: Tên đường dẫn phục vụ. Cố định để `pac_url` không phụ thuộc tên file trên đĩa.
PATH = "/proxy.pac"


class _Handler(BaseHTTPRequestHandler):
    #: Gán khi tạo server.
    pac_path: Path = Path()

    def do_GET(self) -> None:      # noqa: N802 (tên do BaseHTTPRequestHandler quy định)
        if self.path.split("?", 1)[0] != PATH:
            self.send_error(404)
            return
        try:
            body = self.pac_path.read_bytes()
        except OSError as exc:
            log.error("Không đọc được %s: %s", self.pac_path, exc)
            self.send_error(500)
            return

        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        # Không cho cache: sửa PAC xong phải thấy ngay.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Mặc định BaseHTTPRequestHandler in ra stderr mỗi request; ồn vô ích.
        log.debug("pac-server: " + fmt, *args)


class PacServer:
    """Server nhỏ chỉ phục vụ đúng một file, trên 127.0.0.1."""

    def __init__(self, pac_path: Path | str, port: int = DEFAULT_PORT) -> None:
        self.pac_path = Path(pac_path)
        self._wanted_port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int | None:
        return self._server.server_address[1] if self._server else None

    @property
    def url(self) -> str | None:
        port = self.port
        return f"http://127.0.0.1:{port}{PATH}" if port else None

    def start(self) -> str | None:
        """Chạy server trong thread nền. Trả về URL, hoặc `None` nếu không được."""
        if self._server is not None:
            return self.url

        handler = type("PacHandler", (_Handler,), {"pac_path": self.pac_path})
        last_error: OSError | None = None
        for offset in range(PORT_ATTEMPTS):
            port = self._wanted_port + offset
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
                break
            except OSError as exc:
                last_error = exc
        if self._server is None:
            log.error(
                "Không mở được cổng nào cho PAC server (%d..%d): %s",
                self._wanted_port, self._wanted_port + PORT_ATTEMPTS - 1, last_error,
            )
            return None

        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="netmgr-pac", daemon=True
        )
        self._thread.start()
        log.info("PAC server: %s -> %s", self.url, self.pac_path)
        return self.url

    def stop(self) -> None:
        log.info("[DEBUG] Stopping PAC server")
        if self._server is None:
            log.info("[DEBUG] PAC server is none")
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
