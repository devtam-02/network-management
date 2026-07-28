# netmgr — Quản lý mạng & proxy cho Ubuntu

Ứng dụng chạy thường trú dưới dạng **icon ở khay hệ thống (góc phải phía trên)**, cho phép
quản lý proxy, kết nối có dây/Wi-Fi, và route tĩnh IPv4.

- 📖 **[Hướng dẫn cài đặt và sử dụng](docs/huong-dan-su-dung.md)** — bắt đầu từ đây
- 📐 [Phân tích thiết kế](docs/phan-tich-ung-dung.md) — quyết định kiến trúc và lý do

## Cài nhanh

```bash
./install.sh            # kiểm tra môi trường, không thay đổi gì
./install.sh install    # cài lệnh netmgr + autostart (không cần sudo)
```

## Trạng thái

| Giai đoạn | Nội dung | Trạng thái |
|---|---|---|
| P0 | Domain model, validator, `NMFacade` đọc NetworkManager | ✅ Xong |
| P1 | Tray icon (SNI), menu động, bật/tắt Wi-Fi, connect/disconnect | ✅ Xong |
| P2 | Proxy: gsettings + environment.d, profile, nhập cấu hình sẵn có | ✅ Xong |
| P3 | Cửa sổ cấu hình GTK4: kết nối, chi tiết, proxy CRUD, chẩn đoán | ✅ Xong |
| P4 | Route IPv4 CRUD + chuyển Automatic/Manual | ✅ Xong — **chức năng lõi F1–F5 đủ** |
| P5 | Bộ cấu hình: CRUD, chụp trạng thái, áp dụng có rollback | ✅ Xong |
| — | **Mạng nhiều đường**: trang Định tuyến, split-DNS, policy routing | ✅ Xong |
| P6 | Bộ cấu hình nâng cao: dry-run, phát hiện lệch, import/export | ⬜ Tiếp theo |
| P7 | Đóng gói `.deb` | ⬜ |

## Bộ cấu hình

Gom cấu hình mạng **và** proxy thành các bối cảnh chuyển bằng một click:

| Bộ cấu hình | Kết nối | Wi-Fi | Proxy |
|---|---|---|---|
| 🏢 Công ty | LAN có dây | tắt | Proxy công ty |
| 🏠 Nhà | Wi-Fi nhà | bật | tắt |
| ✈️ Offline | ngắt hết | tắt | tắt |

Cách tạo dễ nhất: cấu hình mạng bằng tay cho chạy được, rồi **Lưu trạng thái hiện
tại thành Bộ cấu hình**.

Áp dụng chạy theo 9 bước có thứ tự (bật radio trước, tắt radio sau cùng, proxy
sớm vì không phụ thuộc mạng). Trước khi đụng vào gì, app **chụp lại trạng thái**;
bước nào hỏng thì khôi phục toàn bộ. Kết nối đã đúng sẵn được bỏ qua thay vì kích
hoạt lại — áp dụng lại chính bối cảnh đang chạy không làm rớt mạng.

## Sửa route IPv4

Mở cửa sổ → Kết nối → bấm mũi tên ở một connection → nhóm **Route IPv4**.

- Thêm/sửa/xoá, hoặc **bật/tắt tạm** một route mà không xoá
- **Nhập nhanh một dòng**: `10.20.0.0/16 via 192.168.1.1 metric 100`
- **Dán hàng loạt** nhiều dòng từ tài liệu mạng nội bộ, và **chép** ngược lại
- Validate ngay khi gõ; lỗi "không phải địa chỉ mạng" có nút *Sửa giúp tôi*
- Route tĩnh (🔵) sửa được, route do DHCP cấp (⚪) chỉ đọc

Thay đổi chỉ ghi xuống khi bấm **Lưu** hoặc **Lưu & Áp dụng** — sửa nhiều thứ rồi
áp dụng một lần. "Lưu" thôi thì có hiệu lực ở lần kết nối sau.

## Proxy hoạt động thế nào

"Proxy" trên Linux là nhiều lớp độc lập, không phải một công tắc. App quản lý:

| Lớp | Ảnh hưởng tới | Có hiệu lực |
|---|---|---|
| `gsettings` | Firefox, Chrome, app GTK | Ngay lập tức |
| `~/.config/environment.d/` | terminal, curl, git, pip, npm | Session đăng nhập mới |

Menu tray hiển thị trạng thái **từng lớp** để trả lời được câu "tôi tắt proxy rồi
mà sao `apt` vẫn đi qua proxy?".

Hai tính năng đáng chú ý:

- **Nhập cấu hình proxy đang có của hệ thống** — giữ nguyên danh sách `ignore-hosts`
  bạn đã gõ (`*.viettel.vn`, `10.*.*.*`…), tự dịch sang cú pháp `no_proxy` mà
  curl/git hiểu.
- **Chép lệnh export** — `environment.d` chỉ có hiệu lực với session mới, nên có
  nút chép sẵn đoạn `export ...` để dán vào terminal đang mở.

Mật khẩu proxy lưu trong GNOME Keyring, **không bao giờ** ghi vào file config.

## Yêu cầu

Tất cả đều là gói hệ thống, **không cài qua pip**:

```bash
sudo apt install python3-gi gir1.2-nm-1.0 gir1.2-secret-1 network-manager
```

Tray icon cần extension appindicator (Ubuntu cài sẵn):

```bash
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

## Chạy

```bash
# Xem trạng thái mạng mà app đọc được — không cần tray, tiện để debug
PYTHONPATH=src python3 -m netmgr dump

# Chạy tray icon
PYTHONPATH=src python3 -m netmgr tray

# Chạy và mở luôn cửa sổ cấu hình
PYTHONPATH=src python3 -m netmgr window

# Kèm log chi tiết
PYTHONPATH=src python3 -m netmgr tray --debug
```

Cửa sổ cấu hình cũng mở được từ menu tray → **Cài đặt…**. Đóng cửa sổ chỉ ẩn đi,
app vẫn chạy ở tray.

## Khởi động cùng hệ thống

```bash
cp data/netmgr-autostart.desktop ~/.config/autostart/
```

## Test

```bash
python3 -m pytest tests/ -q          # tất cả
python3 -m pytest tests/unit -q      # chỉ unit, không cần NetworkManager
```

Test integration chỉ **đọc** trạng thái, hoặc ghi lại đúng giá trị đang có — không làm
thay đổi cấu hình mạng của máy.

## Kiến trúc

```
src/netmgr/
├── domain/      Model + validator — THUẦN PYTHON, không import gi
├── infra/       NMFacade (libnm), các ProxyLayer, lưu trữ, keyring
├── services/    ProxyService — điều phối nhiều lớp
├── tray/        StatusNotifierItem tự implement trên Gio.DBus
├── ui/          GTK4 + libadwaita; view_models.py là phần thuần & test được
├── app.py       Adw.Application, single-instance, kiêm controller cho UI
└── cli.py       Điểm vào dòng lệnh
```

Hai ràng buộc quan trọng:

1. **`domain/` không được import `gi`** — nhờ đó phần lớn test chạy được mà không
   cần NetworkManager.
2. **Code widget không chứa logic nghiệp vụ.** Mọi quyết định "hiện chữ gì, bật/tắt
   nút nào" nằm trong `ui/view_models.py` — thuần Python và có test. Nếu sắp viết
   một câu `if` về nghiệp vụ trong file widget thì nó thuộc về view-model.

Tray **không** dùng `AyatanaAppIndicator3` (chỉ có binding GTK3, mà GTK3 không sống chung
tiến trình với GTK4). Tự implement SNI nên app chạy được GTK4/libadwaita trong một tiến
trình và không cần cài thêm gói nào — xem §4.1 của tài liệu phân tích.
