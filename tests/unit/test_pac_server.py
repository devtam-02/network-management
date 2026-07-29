"""PAC server nội bộ — phục vụ đúng một file, chỉ trên 127.0.0.1.

Lý do tồn tại: đo được rằng Chrome/Chromium từ chối `pac_url` dạng `file://`,
nên cấu hình chỉ có file:// sẽ có tác dụng với ứng dụng GNOME mà im lặng không
có tác dụng với trình duyệt.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from netmgr.infra.pac_server import CONTENT_TYPE, PATH, PacServer

BODY = 'function FindProxyForURL(url, host) { return "DIRECT"; }\n'


@pytest.fixture
def pac(tmp_path):
    path = tmp_path / "default.pac"
    path.write_text(BODY, encoding="utf-8")
    return path


@pytest.fixture
def server(pac):
    # Cổng 0 không dùng được vì `PORT_ATTEMPTS` cộng dồn từ nó; chọn dải cao.
    s = PacServer(pac, port=38771)
    assert s.start() is not None
    yield s
    s.stop()


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.headers, response.read().decode()


def test_phuc_vu_dung_noi_dung_va_mime(server):
    status, headers, body = fetch(server.url)
    assert status == 200
    assert body == BODY
    # Sai MIME thì một số bộ giải bỏ qua file mà không báo gì.
    assert headers["Content-Type"] == CONTENT_TYPE


def test_doc_lai_file_moi_lan_goi(server, pac):
    """Sửa file PAC phải có hiệu lực ngay, không phải khởi động lại app."""
    fetch(server.url)
    pac.write_text('function FindProxyForURL(u, h) { return "PROXY x:1"; }\n')
    _s, _h, body = fetch(server.url)
    assert "PROXY x:1" in body


def test_khong_cache(server):
    _s, headers, _b = fetch(server.url)
    assert headers["Cache-Control"] == "no-store"


def test_duong_dan_khac_tra_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        fetch(f"http://127.0.0.1:{server.port}/khong-co")
    assert exc.value.code == 404


def test_chi_nghe_tren_localhost(server):
    """PAC nói ra mạng nội bộ đi đường nào — không có lý do để máy khác đọc được."""
    assert server._server.server_address[0] == "127.0.0.1"


def test_url_dung_dinh_dang(server):
    assert server.url == f"http://127.0.0.1:{server.port}{PATH}"


def test_cong_bi_chiem_thi_nhay_sang_cong_khac(pac):
    first = PacServer(pac, port=38781)
    assert first.start() is not None
    second = PacServer(pac, port=38781)
    try:
        assert second.start() is not None
        assert second.port != first.port
    finally:
        second.stop()
        first.stop()


def test_stop_roi_thi_khong_con_phuc_vu(pac):
    s = PacServer(pac, port=38791)
    url = s.start()
    fetch(url)
    s.stop()
    assert s.running is False
    with pytest.raises(Exception):
        fetch(url)


def test_thieu_file_tra_500(tmp_path):
    s = PacServer(tmp_path / "khong-ton-tai.pac", port=38801)
    url = s.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch(url)
        assert exc.value.code == 500
    finally:
        s.stop()
