# netmgr — Hướng dẫn cài đặt và sử dụng

Ứng dụng quản lý mạng và proxy cho Ubuntu, chạy thường trú dưới dạng icon ở khay
hệ thống (góc phải phía trên màn hình).

- Thiết kế chi tiết: [`phan-tich-ung-dung.md`](phan-tich-ung-dung.md)
- Tổng quan nhanh: [`../README.md`](../README.md)

---

## 1. Yêu cầu

| Thành phần | Yêu cầu | Ghi chú |
|---|---|---|
| Hệ điều hành | Ubuntu 24.04 trở lên | Phát triển và kiểm thử trên Ubuntu 26.04 |
| Desktop | GNOME (Wayland hoặc X11) | KDE/waybar cũng chạy được tray, nhưng chưa kiểm thử |
| Python | 3.12 trở lên | |
| NetworkManager | 1.40 trở lên | Ubuntu mặc định đã có |

Mọi phụ thuộc đều là **gói hệ thống**. Không cài gì qua `pip`, không cần venv.

---

## 2. Cài đặt

### Bước 1 — kiểm tra môi trường

```bash
cd /đường/dẫn/tới/network_management
./install.sh
```

Script chỉ **đọc**, không thay đổi gì. Nó liệt kê thứ còn thiếu kèm lệnh `apt`
tương ứng để bạn tự chạy — script không bao giờ tự gọi `sudo`.

### Bước 2 — cài gói còn thiếu

Nếu môi trường sạch, thường chỉ cần:

```bash
sudo apt install python3-gi gir1.2-nm-1.0 gir1.2-gtk-4.0 gir1.2-adw-1 \
                 gir1.2-secret-1 network-manager
```

Tuỳ chọn — cho nút "Chép lệnh export" trên menu tray:

```bash
sudo apt install wl-clipboard
```

### Bước 3 — bật tray icon

GNOME không có khay hệ thống sẵn; Ubuntu cung cấp qua một extension:

```bash
sudo apt install gnome-shell-ubuntu-extensions      # Ubuntu thường cài sẵn
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

Nếu extension vừa được bật mà icon chưa hiện, đăng xuất rồi đăng nhập lại.

### Bước 4 — chạy thử

```bash
PYTHONPATH=src python3 -m netmgr window
```

Cửa sổ cấu hình mở ra và icon xuất hiện ở góc phải trên. Ctrl+C ở terminal để thoát.

### Bước 5 — cài thành app bấm được trên giao diện

```bash
./install.sh install
```

Tạo ba thứ, đều nằm trong thư mục của bạn, **không cần sudo**:

| Đường dẫn | Tác dụng |
|---|---|
| `~/.local/bin/netmgr` | Lệnh `netmgr` gõ được ở terminal |
| `~/.local/share/applications/netmgr.desktop` | Mục **Quản lý mạng** trong menu ứng dụng |
| `~/.local/share/icons/.../netmgr.svg` | Biểu tượng |

Sau đó mở app bằng cách bấm **Quản lý mạng** trong danh sách ứng dụng (phím
`Super` rồi gõ "mạng"), hoặc gõ `netmgr` ở terminal. Cửa sổ cấu hình hiện ra và
tray icon tự xuất hiện kèm theo — chúng là cùng một tiến trình.

Bấm lại vào app khi nó đang chạy sẽ mở lại cửa sổ chứ không tạo tiến trình mới.

### Bước 6 — khởi động cùng máy (tuỳ chọn, mặc định TẮT)

Mặc định app **không** tự chạy khi đăng nhập: bạn mở khi cần, thoát khi không
cần. Muốn nó chạy sẵn ở tray mỗi lần đăng nhập:

```bash
./install.sh autostart        # bật
./install.sh no-autostart     # tắt
./install.sh                  # xem đang bật hay tắt
```

Khi bật, app khởi động ở chế độ nền — chỉ có tray icon, không bật cửa sổ vào mặt
bạn lúc đăng nhập.

---

## 3. Chạy

```bash
netmgr                # chạy tray (mặc định)
netmgr tray           # như trên, viết rõ
netmgr window         # chạy và mở luôn cửa sổ cấu hình
netmgr dump           # in trạng thái mạng ra terminal, không mở giao diện
netmgr --version
netmgr tray --debug   # log chi tiết
```

Chưa cài lệnh thì thay `netmgr` bằng `PYTHONPATH=src python3 -m netmgr`.

App chỉ chạy **một bản duy nhất**. Gọi `netmgr` lần nữa khi đã có bản đang chạy
sẽ đánh thức bản đó chứ không mở tiến trình mới.

---

## 4. Menu tray

Bấm **chuột trái một lần** vào icon để mở menu (không cần double-click).

```
● Viettel Digital                     ← trạng thái, không bấm được
     enp1s0 · 10.207.153.128/22
     Viettel Digital · 70% · 172.46.2.199/16
─────────────────────────
Bộ cấu hình: 🏢 Công ty          ▸   ← chuyển cả bối cảnh mạng + proxy
Wi-Fi                            ▸   ← bật/tắt radio, chọn mạng đã lưu
Có dây                           ▸
Proxy: Tắt                       ▸   ← bật/tắt, xem trạng thái từng lớp
─────────────────────────
Cài đặt…                             ← mở cửa sổ cấu hình
Thoát
```

Icon đổi theo trạng thái mạng, và có **emblem nhỏ khi proxy đang bật** — thứ mà
GNOME không cho thấy ở đâu cả. Tên Bộ cấu hình đang dùng hiện thành nhãn text
ngay cạnh icon.

> **Lưu ý:** GNOME không hỗ trợ tooltip cho tray icon, nên mọi thông tin (IP,
> cường độ sóng, proxy) đều nằm ngay đầu menu chứ không phải trong tooltip.

---

## 5. Cửa sổ cấu hình

Mở bằng `netmgr window`, hoặc menu tray → **Cài đặt…**.

Đóng cửa sổ chỉ **ẩn** đi — app vẫn chạy ở tray. Muốn thoát hẳn thì dùng
**Thoát** trong menu tray.

### 5.1 Bộ cấu hình

Gom cấu hình mạng **và** proxy thành các bối cảnh, chuyển bằng một thao tác.

**Cách tạo dễ nhất** — cấu hình mạng bằng tay cho chạy được, rồi bấm **Chụp**:

1. Kết nối mạng, bật proxy… như bình thường
2. Trang Bộ cấu hình → **Chụp** → đặt tên (vd "Công ty")
3. Bộ cấu hình ghi lại: kết nối nào đang chạy trên thiết bị nào, radio Wi-Fi
   bật hay tắt, proxy nào đang dùng

Sau đó ✏️ để sửa, ⧉ để nhân bản (tạo biến thể), 🗑 để xoá.

**Khi áp dụng**, app chạy 9 bước theo thứ tự và hiện tiến trình từng bước:

```
✓  Kiểm tra Bộ cấu hình
✓  Ghi nhớ trạng thái hiện tại — 6 kết nối đang chạy
–  Bật Wi-Fi
✓  Áp dụng proxy
✓  Ngắt các kết nối không cần
✓  Kích hoạt kết nối — 2 kết nối đã đúng sẵn
✓  Kiểm tra kết quả
```

Ba điều đáng biết:

- **Bước nào hỏng thì khôi phục toàn bộ** về trạng thái đã ghi nhớ ở bước 2.
- **Kết nối đã đúng sẵn được bỏ qua**, không kích hoạt lại. Áp dụng lại chính
  bối cảnh đang chạy sẽ không làm rớt mạng.
- **Thiết bị vắng mặt được bỏ qua** (laptop rời dock chẳng hạn), trừ khi bạn
  đánh dấu *Bắt buộc* cho thiết bị đó.

Bộ cấu hình trỏ tới thứ đã bị xoá sẽ hiện ⚠ và không cho áp dụng.

**Không dùng Bộ cấu hình** đưa app về quản lý thủ công — chọn mục này chỉ bỏ
đánh dấu, không thay đổi cấu hình mạng đang chạy.

### 5.2 Kết nối

Danh sách các cấu hình mạng **đã lưu**, chia hai nhóm Wi-Fi và Có dây.

- Công tắc **Bật Wi-Fi** ở đầu nhóm Wi-Fi
- Nút **Kết nối / Ngắt** ở mỗi hàng
- Mũi tên `›` mở trang chi tiết

> **App không kết nối tới mạng Wi-Fi mới.** Đó là việc GNOME đã làm tốt. Kết nối
> lần đầu bằng menu Wi-Fi của hệ thống, mạng sẽ tự xuất hiện ở đây và từ đó quản
> lý được đầy đủ. Đổi lại, app không bao giờ đụng tới mật khẩu Wi-Fi của bạn.

### 5.3 Trang chi tiết — sửa IPv4 và route

**Trạng thái** hiện thông tin đang chạy: SSID, cường độ, băng tần, IP, gateway,
DNS. Các dòng có nhãn *đang chạy* là giá trị runtime, khác với cấu hình đã lưu.

**IPv4** — chuyển giữa `Tự động (DHCP)` và `Thủ công`.

> Chuyển sang Thủ công sẽ **điền sẵn** IP/gateway/DNS đang nhận từ DHCP, bạn chỉ
> việc sửa chỗ cần đổi.

**Tuỳ chọn** — bỏ qua DNS tự động, bỏ qua route tự động, không dùng làm default
route.

> Route tĩnh **được giữ nguyên ở cả hai chế độ** Tự động và Thủ công. Đây là chỗ
> nhiều người hiểu nhầm.

**Route IPv4** — 🔵 route tĩnh sửa được, ⚪ route do DHCP cấp chỉ đọc.

| Nút | Tác dụng |
|---|---|
| `+` | Thêm route |
| ✏️ | Sửa |
| 🗑 | Xoá |
| công tắc | Tắt tạm một route mà không xoá |
| 📋 | Dán nhiều route cùng lúc |
| ⧉ | Chép các route tĩnh ra clipboard |

Trong hộp thoại thêm/sửa có ô **Nhập nhanh** nhận một dòng:

```
10.20.0.0/16 via 192.168.1.1 metric 100
172.16.0.0/12 gw 10.0.0.1
10.99.0.5
```

Validate chạy ngay khi gõ. Gõ nhầm địa chỉ mạng (`10.20.1.5/16`) sẽ có nút
**Sửa giúp tôi** để tự chuyển thành `10.20.0.0/16`.

**Thay đổi chỉ ghi xuống khi bạn bấm nút.** Sửa bao nhiêu thứ cũng được rồi áp
dụng một lần:

| Nút | Ý nghĩa |
|---|---|
| **Huỷ** | Bỏ hết thay đổi, quay về trạng thái đã lưu |
| **Lưu** | Ghi xuống, có hiệu lực ở lần kết nối sau |
| **Lưu & Áp dụng** | Ghi xuống và áp dụng ngay |

Một số thay đổi lớn (đổi Tự động ↔ Thủ công) không áp dụng nóng được; app sẽ tự
kết nối lại và báo cho bạn biết — mạng chớp tắt một nhịp.

### 5.4 Định tuyến — dùng nhiều mạng cùng lúc

Trang này dành cho tình huống nhiều mạng chạy song song và bạn muốn *truy cập
địa chỉ nào thì đi ra mạng của địa chỉ đó*.

**"Địa chỉ này đi đường nào?"** — gõ một IP, app trả lời interface nào sẽ nhận và
**vì sao**:

```
10.60.101.189 → enp1s0 qua 10.207.154.254
                (khớp 10.0.0.0/8, cụ thể hơn 1 route khác)
```

App mô phỏng đúng cách kernel chọn: duyệt `ip rule` theo priority, trong bảng
tương ứng thì prefix dài nhất thắng, hoà thì metric nhỏ hơn thắng. Kết quả đã
được đối chiếu khớp 17/17 với `ip route get` trên máy thật.

**Nhận xét** chỉ ra những điều dễ bỏ sót:

- dải nào đã tách khỏi đường mặc định
- nhiều default route cùng metric (kết quả không đoán trước được)
- bảng định tuyến phụ được tra **trước** bảng chính — thường do VPN cài

**Bảng route** hiển thị theo từng interface, gồm cả `docker0`, bridge và VPN.

**Luật định tuyến của hệ thống** liệt kê `ip rule` thật, kể cả luật do phần mềm
khác cài mà NetworkManager không quản lý.

#### Ba núm để điều khiển

| Núm | Ở đâu | Dùng khi |
|---|---|---|
| **Route tĩnh** | Chi tiết → Route IPv4 | "Dải 10.20/16 đi qua LAN công ty" |
| **Metric mặc định** | Chi tiết → IPv4 | Chọn mạng nào là đường chính, mạng nào dự phòng |
| **Luật định tuyến** | Chi tiết → Luật định tuyến | Định tuyến theo **nguồn** thay vì đích |

#### Đừng quên DNS

Route đúng chưa đủ. Nếu tên nội bộ vẫn hỏi DNS của mạng công cộng thì bạn nhận
về IP sai và route đúng cũng vô nghĩa.

Chi tiết → **Định tuyến DNS**:

- **Search domain** — thêm `~viettelmoney.vn` để mọi truy vấn `*.viettelmoney.vn`
  đi tới DNS của kết nối này. Tiền tố `~` nghĩa là *chỉ định tuyến*, không nối
  vào tên ngắn.
- **Độ ưu tiên DNS** — số nhỏ hơn thắng; số âm nghĩa là độc quyền cho các domain
  đã khai.

> ⚠️ **Bẫy:** bật "Bỏ qua DNS tự động" sẽ bỏ luôn search domain do DHCP cấp. Nếu
> mạng của bạn đang dựa vào domain đó, phải khai lại bằng tay ở ô trên. App có
> cảnh báo ngay tại chỗ khi rơi vào tình huống này.

### 5.5 Proxy

"Proxy" trên Linux không phải một công tắc mà là **nhiều lớp độc lập**:

| Lớp | Ảnh hưởng tới | Có hiệu lực |
|---|---|---|
| Ứng dụng desktop | Firefox, Chrome, app GTK | Ngay lập tức |
| Biến môi trường | terminal, `curl`, `git`, `pip`, `npm` | Session đăng nhập mới |

Mỗi cấu hình chọn được áp lên lớp nào. Lớp không chọn sẽ bị **tắt** khi bật cấu
hình đó, chứ không bỏ qua.

**Đã có proxy cấu hình sẵn?** Dùng **Nhập cấu hình proxy đang có của hệ thống** —
nó giữ nguyên danh sách bỏ qua bạn đã gõ (`*.viettel.vn`, `10.*.*.*`…) và tự dịch
sang cú pháp `no_proxy` mà `curl`/`git` hiểu. Không dịch thì proxy sẽ nuốt cả
traffic nội bộ, một kiểu hỏng rất khó chẩn đoán vì trình duyệt vẫn chạy đúng.

**Terminal đang mở không nhận proxy mới.** `environment.d` chỉ được đọc lúc khởi
tạo session. Bấm **Chép** để lấy đoạn `export …` dán vào terminal hiện tại.

Mật khẩu proxy lưu trong **GNOME Keyring**, không bao giờ ghi vào file cấu hình.
Khi bật proxy có xác thực, một bản sao được đặt vào dconf vì đó là chỗ duy nhất
trình duyệt đọc được; bản sao đó bị xoá khi tắt proxy.

### 5.6 Chẩn đoán

Trả lời câu hỏi kinh điển *"tôi tắt proxy rồi mà sao `apt` vẫn đi qua proxy?"* —
trang này cho thấy **từng lớp** đang ở trạng thái nào, cùng với các quyền polkit
mà NetworkManager cấp cho app.

---

## 6. Một số tình huống

### Thêm route tới subnet nội bộ

Kết nối → `›` ở connection đang dùng → **Route IPv4** → `+` → ô Nhập nhanh:

```
10.20.0.0/16 via 192.168.1.1 metric 100
```

→ **Lưu** → **Lưu & Áp dụng**. Kiểm tra bằng `ip route show`.

### Đặt IP tĩnh

Trang chi tiết → **IPv4** → Chế độ = `Thủ công`. Các ô đã được điền sẵn giá trị
DHCP đang dùng — sửa lại địa chỉ rồi **Lưu & Áp dụng**.

### Chuyển nhanh giữa công ty và nhà

1. Ở công ty, cấu hình mạng + proxy cho chạy được → **Chụp** → "Công ty"
2. Về nhà, cấu hình lại → **Chụp** → "Nhà"
3. Từ đó chỉ cần menu tray → Bộ cấu hình → chọn

### Tạm tắt một route để thử

Trang chi tiết → gạt **công tắc** ở hàng route → **Lưu & Áp dụng**. Route vẫn còn
trong danh sách, chỉ không được ghi xuống NetworkManager. Bật lại bất cứ lúc nào.

---

## 7. Xử lý sự cố

### Icon không hiện ở góc phải trên

```bash
gnome-extensions list --enabled | grep appindicator
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

Bật rồi vẫn không thấy, kiểm tra app đã đăng ký được chưa:

```bash
busctl --user get-property org.kde.StatusNotifierWatcher /StatusNotifierWatcher \
  org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
```

Phải thấy một mục `org.kde.StatusNotifierItem-<pid>-1`. Chạy `netmgr tray --debug`
để xem log chi tiết.

Sau khi `gnome-shell` khởi động lại, app tự đăng ký lại — không cần làm gì.

### Nút bị xám, không bấm được

Trang **Chẩn đoán** → mục Quyền. Nếu thấy `no` thì polkit đang chặn. Trạng thái
`unknown` chỉ nghĩa là NetworkManager chưa trả lời xong — chờ một giây.

### Sửa route xong mà `ip route show` không đổi

Bạn bấm **Lưu** thay vì **Lưu & Áp dụng**. Chỉ Lưu thì thay đổi có hiệu lực ở lần
kết nối sau.

### Terminal không đi qua proxy

`environment.d` chỉ được đọc lúc đăng nhập. Hoặc mở terminal sau khi đăng nhập
lại, hoặc dùng nút **Chép** rồi dán đoạn `export …` vào terminal đang mở.

### `apt` vẫn đi qua proxy sau khi tắt

Lớp APT (`/etc/apt/apt.conf.d/`) chưa được app quản lý — nó cần quyền root và
nằm trong kế hoạch phiên bản sau. Kiểm tra bằng tay:

```bash
grep -r Proxy /etc/apt/apt.conf.d/ 2>/dev/null
```

### App không khởi động

```bash
netmgr dump      # nếu lệnh này chạy được thì NetworkManager ổn
netmgr tray --debug
```

---

## 8. File và thư mục

| Đường dẫn | Nội dung |
|---|---|
| `~/.config/netmgr/proxy.toml` | Cấu hình proxy (**không** chứa mật khẩu) |
| `~/.config/netmgr/profiles.toml` | Bộ cấu hình |
| `~/.config/environment.d/10-netmgr-proxy.conf` | Biến môi trường proxy do app sinh |
| GNOME Keyring | Mật khẩu proxy |

Hai file `.toml` là text đọc được, sửa tay được, đưa vào Git nội bộ được. App
đang chạy sẽ ghi đè khi bạn lưu từ giao diện.

App **không** lưu bản sao cấu hình mạng — mọi connection đều nằm trong
NetworkManager như bình thường, `nmcli` vẫn thấy đầy đủ.

---

## 9. Gỡ cài đặt

```bash
./install.sh uninstall
```

Xoá lệnh, autostart và systemd unit. Cấu hình của bạn **không** bị xoá. Muốn xoá
sạch:

```bash
rm -rf ~/.config/netmgr
rm -f ~/.config/environment.d/10-netmgr-proxy.conf
```

Cấu hình mạng trong NetworkManager không bị ảnh hưởng.

---

## 10. Những gì app cố tình KHÔNG làm

Nêu rõ để bạn khỏi mất công tìm:

| Không có | Lý do |
|---|---|
| Quét và kết nối mạng Wi-Fi mới | GNOME làm tốt rồi; bỏ đi thì app không bao giờ chạm vào mật khẩu Wi-Fi |
| IPv6 | Phiên bản sau |
| VPN | Phiên bản sau |
| Proxy cho `apt` và Docker | Cần quyền root, phiên bản sau |
| Chạy bằng `sudo` | Sẽ hỏng gsettings, keyring và tray. App được thiết kế chạy bằng quyền người dùng thường |
