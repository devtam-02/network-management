# Phân tích ứng dụng Quản lý Mạng cho Ubuntu (Tray App)

> Tài liệu phân tích thiết kế — phiên bản 1.4
> Ngày: 2026-07-27
> Trạng thái: P0–P4 đã implement và verify trên máy thật (445 test)
>
> **Lịch sử thay đổi**
> - 1.1: bổ sung F6 — Bộ cấu hình (Profile) gom cấu hình mạng + proxy
> - 1.2: thu hẹp F3 — Wi-Fi chỉ còn quản lý connection đã lưu + xem trạng thái; bỏ quét AP và kết nối mạng mới
> - 1.3: cập nhật theo **kết quả thực nghiệm** khi implement P0/P1 — chốt kiến trúc một tiến trình, phát hiện tooltip không hoạt động trên GNOME, permission nạp bất đồng bộ, checkpoint cần xác thực
> - 1.4: P2–P4 xong. **Sửa một luật validate sai trong §3.3** (gateway nằm trong dải đích), ghi nhận cạm bẫy cú pháp `ignore-hosts` và `use-same-proxy` với SOCKS

---

## 1. Tổng quan

### 1.1 Mục tiêu

Xây dựng một ứng dụng desktop chạy thường trú dưới dạng **icon ở khay hệ thống (góc phải phía trên màn hình)** trên Ubuntu, cho phép người dùng:

| # | Nhóm chức năng | Mô tả |
|---|---|---|
| F1 | Quản lý proxy | Bật/tắt proxy nhanh, cấu hình manual/auto (PAC), quản lý nhiều profile proxy |
| F2 | Quản lý Wired connection | Liệt kê, kết nối/ngắt, xem trạng thái, sửa cấu hình |
| F3 | Quản lý Wi-Fi | Bật/tắt radio, quản lý các Wi-Fi connection đã lưu, xem trạng thái kết nối |
| F4 | Quản lý IPv4 route | Thêm / sửa / xoá route tĩnh cho từng connection |
| F5 | Automatic mode | Bật/tắt chế độ tự động (DHCP) cho IPv4, cho cả DNS và route |
| F6 | **Bộ cấu hình (Profile)** | Nhiều bộ cấu hình, mỗi bộ gồm cấu hình mạng **và** proxy riêng; chuyển đổi bằng một click |

### 1.1.1 Thuật ngữ — làm rõ từ "profile"

Từ "profile" bị dùng cho ba thứ khác nhau. Toàn bộ tài liệu này thống nhất như sau:

| Thuật ngữ trong tài liệu | Nghĩa | Tương ứng |
|---|---|---|
| **Bộ cấu hình** (Profile) | Khái niệm của app — một bối cảnh làm việc trọn vẹn: dùng connection nào + route gì + proxy nào | Khái niệm mới, do app quản lý |
| **Connection** (connection profile) | Cấu hình một kết nối mạng | `NM.RemoteConnection` |
| **Proxy config** | Cấu hình proxy (host/port/mode) | Thành phần bên trong Bộ cấu hình |

Khi tài liệu viết hoa **Bộ cấu hình** là nói tới khái niệm F6.

### 1.2 Vấn đề đang giải quyết

GNOME Settings hiện tại có các điểm bất tiện mà app này nhắm tới:

1. **Đổi proxy tốn nhiều click** — phải mở Settings → Network → Proxy → chọn mode → nhập lại host/port. Không có khái niệm "profile" để chuyển nhanh giữa proxy công ty / nhà / tắt.
2. **Proxy không đồng bộ giữa các lớp** — `gsettings` chỉ ảnh hưởng ứng dụng GNOME/GTK; terminal, `apt`, `docker`, `git` dùng cơ chế khác. Người dùng phải sửa 4-5 chỗ.
3. **Quản lý route tĩnh cực kỳ ẩn** — nằm sâu trong dialog IPv4, không xem được route đang thực sự áp dụng, không validate đầu vào.
4. **Không có cái nhìn tổng thể** — không biết route nào đến từ DHCP, route nào là tĩnh.
5. **Không chuyển được cả bối cảnh cùng lúc** — đây là vấn đề lớn nhất. Khi di chuyển từ công ty về nhà, người dùng phải thao tác tay 4-5 việc rời rạc: đổi Wi-Fi, tắt proxy, xoá/thêm route nội bộ, đổi DNS, chuyển IPv4 về DHCP. Mỗi lần quên một bước là mất thời gian debug. Cần khái niệm **Bộ cấu hình** gom tất cả lại thành một thao tác nguyên tử.

### 1.3 Đối tượng người dùng

Developer / sysadmin trên Ubuntu, thường xuyên chuyển đổi giữa nhiều môi trường mạng (VPN công ty, mạng nhà, mạng khách hàng) và cần route tĩnh tới các subnet nội bộ.

### 1.4 Phạm vi (Scope)

**Trong phạm vi (v1):**
- Quản lý proxy ở lớp desktop (gsettings) + lớp môi trường (environment.d)
- Wired connection: list / connect / disconnect / tạo / xoá
- Wi-Fi: bật/tắt radio, quản lý các connection **đã lưu**, xem trạng thái
- IPv4: automatic ↔ manual, address, gateway, DNS, route CRUD — cho cả wired lẫn Wi-Fi
- **Bộ cấu hình: CRUD, chuyển đổi thủ công, import/export, rollback khi lỗi**
- Tray icon + menu nhanh + cửa sổ cấu hình chi tiết

**Ngoài phạm vi (v1, cân nhắc v2+):**
- **Quét AP và kết nối tới mạng Wi-Fi mới** — xem 3.2.1 để biết lý do
- **Tự động chuyển Bộ cấu hình** theo ngữ cảnh (SSID, MAC gateway, subnet) — v2
- IPv6 (chỉ hiển thị read-only ở v1)
- VPN (WireGuard/OpenVPN/L2TP)
- Wi-Fi Hotspot, Wi-Fi Enterprise (802.1X)
- Bonding / Bridge / VLAN
- Đa ngôn ngữ (i18n) — v1 hardcode tiếng Việt hoặc tiếng Anh

---

## 2. Phân tích môi trường đích

Kết quả khảo sát trên máy phát triển hiện tại:

| Thành phần | Giá trị | Ý nghĩa với thiết kế |
|---|---|---|
| OS | Ubuntu 26.04 LTS (resolute) | Đủ mới, có Python 3.14 |
| Desktop | GNOME Shell 50.1, `XDG_CURRENT_DESKTOP=ubuntu:GNOME` | Phải theo chuẩn GNOME |
| Session | **Wayland** | ⚠️ Không có XEmbed system tray — xem mục 4.1 |
| NetworkManager | 1.54.3, typelib `NM-1.0` có sẵn | Dùng **libnm** qua GObject Introspection |
| Tray backend | `libayatana-appindicator3-1` + extension `ubuntu-appindicators@ubuntu.com` | Tray icon khả dụng qua StatusNotifierItem |
| Python | 3.14.4 | OK |
| PyGObject | 3.56.2 | Có `Gtk-3.0`, `Gtk-4.0`, `Adw-1` |
| Proxy hiện tại | `org.gnome.system.proxy mode = 'none'` | Schema gsettings có sẵn |

### 2.1 Ràng buộc quan trọng từ Wayland

Trên Wayland, khay hệ thống **không** hoạt động theo cơ chế X11 cũ (XEmbed). GNOME Shell mặc định cũng không có tray. Ubuntu giải quyết bằng extension `ubuntu-appindicators`, extension này implement chuẩn **StatusNotifierItem (SNI)** trên D-Bus.

Hệ quả với ứng dụng:

- ✅ Phải xuất icon qua SNI (trực tiếp hoặc qua `AyatanaAppIndicator3`), **không** dùng `Gtk.StatusIcon` (deprecated, không chạy trên Wayland).
- ⚠️ Menu của SNI được **render bởi GNOME Shell**, không phải bởi app → menu chỉ hỗ trợ item cơ bản: label, checkbox, radio, submenu, separator, icon. **Không nhúng được widget tuỳ ý** (slider, input, list phức tạp) vào menu tray.
- ⚠️ App phải xử lý được trường hợp extension bị tắt → cần fallback (thông báo hướng dẫn + mở cửa sổ chính trực tiếp).
- ⚠️ Không kiểm soát được vị trí icon (GNOME tự sắp xếp) — "góc phải phía trên" là vị trí GNOME đặt các indicator, đúng như yêu cầu.

---

## 3. Phân tích chức năng chi tiết

### 3.1 F1 — Quản lý Proxy

Đây là phần dễ bị làm sai nhất, vì **"proxy" trên Linux không phải một thứ duy nhất** mà là nhiều lớp độc lập:

| Lớp | Cơ chế | Ảnh hưởng tới | Cần quyền root? | Áp dụng ngay? |
|---|---|---|---|---|
| L1 — Desktop | `gsettings org.gnome.system.proxy` | Firefox, Chrome (GNOME mode), app GTK, `libproxy` | Không | ✅ Ngay lập tức |
| L2 — Environment | `~/.config/environment.d/*.conf`, `~/.profile` | Terminal, curl, wget, git, pip, npm | Không | ❌ Cần logout hoặc shell mới |
| L3 — APT | `/etc/apt/apt.conf.d/99proxy` | `apt`, `apt-get` | **Có** | ✅ Ngay lập tức |
| L4 — Docker | `~/.docker/config.json` (client), `/etc/systemd/system/docker.service.d/` (daemon) | Container build/pull | Client: không / Daemon: **có** | Daemon cần restart |
| L5 — NetworkManager | `proxy.method`, `proxy.pac-url` per-connection | Hạn chế, chủ yếu PAC | Có (polkit) | Khi reactivate |

> **Cạm bẫy phát hiện khi implement: cú pháp `ignore-hosts` của GNOME không phải `no_proxy`.**
>
> GNOME dùng `*.viettel.vn` và `10.*.*.*`; curl/git/pip **không hiểu** hai dạng đó.
> Copy nguyên xi sang biến môi trường sẽ khiến proxy nuốt cả traffic nội bộ — kiểu
> hỏng rất khó chẩn đoán vì trình duyệt vẫn chạy đúng, chỉ terminal là sai. Phải
> dịch: `*.viettel.vn` → `.viettel.vn`, `10.*.*.*` → `10.0.0.0/8`.
>
> **Cạm bẫy thứ hai: `use-same-proxy` KHÔNG được áp cho SOCKS.** SOCKS5 là giao
> thức khác hẳn HTTP; suy ra `all_proxy=socks5://<http-proxy>` sẽ làm hỏng mọi
> công cụ đọc `all_proxy`. `use_same_for_all` chỉ áp cho http/https/ftp.

**Quyết định thiết kế:**

- **v1** implement L1 + L2 (không cần root, bao phủ ~90% nhu cầu).
- **v2** thêm L3 + L4 qua một **polkit helper** riêng (xem mục 6.2).
- Bỏ qua L5 (giá trị thấp, phức tạp cao).
- UI phải **hiển thị rõ lớp nào đang bật**, tránh tình trạng "tôi tắt proxy rồi mà `apt` vẫn qua proxy".

**Mô hình Proxy Profile:**

```
ProxyProfile
├── id: str                    # uuid
├── name: str                  # "Proxy Công ty"
├── mode: none | manual | auto
├── manual:
│   ├── http:   host, port
│   ├── https:  host, port
│   ├── ftp:    host, port
│   ├── socks:  host, port
│   ├── use_same_for_all: bool
│   ├── auth_enabled: bool
│   ├── username: str
│   └── password: str          # lưu trong Secret Service, KHÔNG lưu file
├── auto:
│   └── pac_url: str
├── ignore_hosts: list[str]    # mặc định: localhost, 127.0.0.0/8, ::1
└── layers: set[L1|L2|L3|L4]   # profile này áp dụng lên những lớp nào
```

**Ánh xạ sang gsettings (L1):**

| Trường app | Khoá gsettings |
|---|---|
| mode | `org.gnome.system.proxy mode` |
| http host/port | `org.gnome.system.proxy.http host` / `port` |
| https host/port | `org.gnome.system.proxy.https host` / `port` |
| socks host/port | `org.gnome.system.proxy.socks host` / `port` |
| ignore_hosts | `org.gnome.system.proxy ignore-hosts` |
| pac_url | `org.gnome.system.proxy autoconfig-url` |
| auth | `org.gnome.system.proxy.http use-authentication` / `authentication-user` / `authentication-password` |

**Yêu cầu chức năng:**

- FR-P1: Bật/tắt proxy bằng **một click** từ tray menu (toggle giữa profile đang chọn ↔ `none`).
- FR-P2: Chuyển nhanh giữa các profile bằng radio group trên tray menu.
- FR-P3: CRUD profile trong cửa sổ chính.
- FR-P4: Test connectivity — thử `HEAD` một URL qua proxy, báo OK / timeout / 407 (sai auth).
- FR-P5: Mật khẩu proxy lưu trong **libsecret / GNOME Keyring**, không bao giờ ghi plaintext ra file config.
- FR-P6: Với L2, sinh file `~/.config/environment.d/10-netmgr-proxy.conf` + hiển thị cảnh báo "cần mở terminal mới", kèm nút copy đoạn `export ...` để dán ngay vào shell hiện tại.

---

### 3.2 F2/F3 — Quản lý Connection (Wired + Wi-Fi)

**Khái niệm cần phân biệt rõ trong NetworkManager** (rất quan trọng, ảnh hưởng toàn bộ data model):

| Khái niệm | Ý nghĩa | Kiểu libnm |
|---|---|---|
| **Device** | Phần cứng: `enp3s0`, `wlp2s0` | `NM.Device` |
| **Connection (profile)** | Cấu hình đã lưu — file trong `/etc/NetworkManager/system-connections/` | `NM.RemoteConnection` |
| **Active Connection** | Một profile đang được áp lên một device | `NM.ActiveConnection` |
| **Access Point** | Mạng Wi-Fi quét được | `NM.AccessPoint` — v1 chỉ dùng **AP đang kết nối** để đọc tín hiệu/tần số/BSSID, không duyệt danh sách AP |

Một device có thể có nhiều profile; chỉ một profile active tại một thời điểm. UI phải phản ánh đúng: "sửa route" là sửa **profile**, không phải sửa device, và thay đổi chỉ có hiệu lực khi **reapply/reactivate**.

**Yêu cầu chức năng — Wired:**

- FR-W1: Liệt kê tất cả device ethernet + trạng thái (`connected` / `disconnected` / `unavailable` / cáp rút).
- FR-W2: Với mỗi device, liệt kê profile khả dụng; đánh dấu profile đang active.
- FR-W3: Kết nối / ngắt kết nối một profile.
- FR-W4: Hiển thị thông tin runtime: IP, netmask, gateway, DNS, MAC, tốc độ link, route table đang áp dụng.
- FR-W5: Tạo profile mới / xoá profile.

**Yêu cầu chức năng — Wi-Fi:**

- FR-F1: Bật/tắt Wi-Fi radio (`NM.Client.wireless_set_enabled`).
- FR-F2: Liệt kê các Wi-Fi connection **đã lưu** (`NM.RemoteConnection` type `802-11-wireless`), đánh dấu cái đang active.
- FR-F3: Kết nối / ngắt kết nối một connection đã lưu bằng một click.
- FR-F4: Sửa cấu hình IPv4 + route của connection đã lưu — **đây là giá trị chính của app với Wi-Fi**, giống hệt luồng của wired (mục 3.3, 3.4).
- FR-F5: Xoá connection đã lưu ("quên mạng"), có xác nhận.
- FR-F6: **Xem trạng thái** kết nối hiện tại: SSID, cường độ tín hiệu, tần số/băng tần, tốc độ link, chuẩn bảo mật, BSSID, IP/gateway/DNS, route đang áp dụng.
- FR-F7: Trạng thái và cường độ tín hiệu cập nhật realtime qua signal (`notify::strength`, `state-changed`), **không polling**.
- FR-F8: Sửa `autoconnect` và `autoconnect-priority` của từng connection đã lưu — quyết định NM tự nối mạng nào trước.

#### 3.2.1 Vì sao bỏ quét AP và kết nối mạng mới

Đây là cắt giảm phạm vi có chủ đích, không phải thiếu sót:

| Lý do | Chi tiết |
|---|---|
| **Trùng lặp với GNOME** | Menu Wi-Fi sẵn có của GNOME (cũng ở góc phải trên) đã làm việc quét và kết nối mạng mới rất tốt. Làm lại không tạo thêm giá trị |
| **Bỏ được toàn bộ secret agent** | Kết nối mạng mới đòi hỏi implement `NM.SecretAgent` để cung cấp PSK — phần phức tạp và dễ sai nhất của libnm. Bỏ đi thì app **không bao giờ phải cầm mật khẩu Wi-Fi** |
| **Bỏ được scan lifecycle** | Không phải quản lý `request_scan()`, throttle, cache AP, xử lý scan làm rớt kết nối, tốn pin |
| **Giảm bề mặt bảo mật** | Không lưu, không truyền, không log mật khẩu Wi-Fi ở bất kỳ đâu |
| **Đúng trọng tâm sản phẩm** | Giá trị của app là **route tĩnh, IPv4 và proxy theo bối cảnh** — thứ GNOME làm dở. Kết nối Wi-Fi là thứ GNOME làm tốt |

**Giả định đi kèm (cần nêu rõ trong tài liệu người dùng):** người dùng kết nối mạng Wi-Fi mới lần đầu bằng menu Wi-Fi của GNOME. Sau đó connection xuất hiện trong app và quản lý được đầy đủ. Với F6, một Bộ cấu hình chỉ bind được tới Wi-Fi connection **đã tồn tại**.

**Hệ quả UX cần xử lý:** khi người dùng vào tab Wi-Fi mà chưa có connection nào đã lưu, hiển thị trạng thái rỗng có hướng dẫn — *"Chưa có mạng Wi-Fi nào được lưu. Kết nối lần đầu bằng menu Wi-Fi của hệ thống, mạng sẽ tự xuất hiện ở đây."* kèm nút mở `gnome-control-center wifi`.

---

### 3.3 F4 — Quản lý IPv4 Route (chức năng lõi)

Đây là điểm khác biệt chính so với GNOME Settings.

**Cấu trúc một route trong NetworkManager (`ipv4.routes`):**

```
Route
├── dest: str          # bắt buộc — địa chỉ đích, vd "10.20.0.0"
├── prefix: int        # bắt buộc — 0..32, vd 16
├── next_hop: str|None # gateway, vd "192.168.1.1"; None = on-link
├── metric: int|None   # độ ưu tiên, càng nhỏ càng ưu tiên; None = mặc định
└── attributes:        # tuỳ chọn nâng cao
    ├── table: int     # routing table id (vd 100)
    ├── src: str       # preferred source address
    ├── onlink: bool
    ├── mtu: int
    ├── window / cwnd / initcwnd
    └── type: unicast|local|blackhole|unreachable|prohibit
```

**Yêu cầu chức năng:**

- FR-R1: **Xem** — bảng route của profile đang chọn, phân biệt rõ:
  - 🔵 Route **tĩnh** (do người dùng cấu hình trong profile) — sửa/xoá được
  - ⚪ Route **từ DHCP/RA** — read-only, chỉ hiển thị
  - Nguồn dữ liệu: route tĩnh lấy từ `NM.SettingIPConfig` của profile; route runtime lấy từ `NM.Device.get_ip4_config()`.
- FR-R2: **Thêm** route — form với validate đầy đủ (xem bảng dưới).
- FR-R3: **Sửa** route — cho phép sửa mọi trường.
- FR-R4: **Xoá** route — có xác nhận.
- FR-R5: **Bật/tắt** một route mà không xoá (lưu trạng thái disabled trong metadata app, khi apply thì bỏ qua) — tiện cho debug.
- FR-R6: Sắp xếp lại thứ tự / sửa metric hàng loạt.
- FR-R7: Toggle `ipv4.ignore-auto-routes` — bỏ qua toàn bộ route do DHCP đẩy về.
- FR-R8: Toggle `ipv4.never-default` — không dùng connection này làm default route.
- FR-R9: Import/Export route dưới dạng CSV hoặc text `dest/prefix next_hop metric` (dán hàng loạt từ tài liệu nội bộ).
- FR-R10: Nút **Apply** rõ ràng — thay đổi chỉ ghi vào profile khi bấm Apply, kèm tuỳ chọn "Áp dụng ngay" (reapply) hay "Áp dụng lần kết nối sau".

**Bảng validate (bắt buộc implement đủ, đây là nguồn lỗi phổ biến nhất):**

| Trường | Luật | Thông báo lỗi |
|---|---|---|
| dest | IPv4 hợp lệ, dạng dotted-quad | "Địa chỉ đích không hợp lệ" |
| prefix | 0 ≤ prefix ≤ 32 | "Prefix phải trong khoảng 0–32" |
| dest + prefix | dest phải là network address (host bits = 0) | "10.20.1.5/16 không hợp lệ, ý bạn là 10.20.0.0/16?" — kèm nút tự sửa |
| next_hop | IPv4 hợp lệ hoặc để trống | "Gateway không hợp lệ" |
| next_hop | Nằm trong dải đích **và** không thuộc subnet nào của interface | ⛔ "Gateway nằm trong dải đích và không thuộc subnet nào của interface → route vòng lặp" |
| next_hop | Nằm trong dải đích, **chưa biết** subnet của interface | ⚠ "Chỉ hợp lệ nếu gateway nằm cùng subnet với interface" |
| next_hop | Không thuộc subnet nào của interface (và không bật on-link) | ⚠ "Gateway không reachable trực tiếp — cần onlink?" |

> **Sửa lỗi đặc tả (v1.4).** Bản trước ghi luật "gateway không được nằm trong subnet đích → vòng lặp" và **luật đó sai**. Khi chạy thử trên máy phát triển, nó chặn nhầm một route đang hoạt động bình thường:
>
> ```
> interface  10.207.153.128/22        (subnet 10.207.152.0/22)
> route      10.0.0.0/8 via 10.207.154.254
> ```
>
> Gateway nằm trong `10.0.0.0/8`, nhưng cũng nằm ngay trong subnet của interface nên tới được trực tiếp — đây là cấu hình doanh nghiệp rất phổ biến. Vòng lặp thật chỉ xảy ra khi gateway nằm trong dải đích **và** không tới được bằng cách nào khác.
>
> Nguyên tắc rút ra: khi thiếu thông tin để kết luận (không biết subnet của interface), hạ xuống **cảnh báo** chứ đừng chặn. Chặn nhầm một cấu hình đúng gây bực bội hơn nhiều so với cảnh báo thừa.
| metric | 0 ≤ metric ≤ 4294967295 | "Metric ngoài phạm vi" |
| table | 0 ≤ table ≤ 4294967295 | "Table ID không hợp lệ" |
| Toàn bảng | Không trùng (dest, prefix, table) | "Route này đã tồn tại" |
| 0.0.0.0/0 | Cảnh báo — đây là default route | "Đây là default route, sẽ ảnh hưởng toàn bộ traffic. Tiếp tục?" |

---

### 3.4 F5 — Automatic mode (DHCP)

"Automatic mode" ánh xạ sang `ipv4.method` của NetworkManager:

| Chế độ UI | `ipv4.method` | Ý nghĩa |
|---|---|---|
| **Automatic (DHCP)** | `auto` | Lấy IP, gateway, DNS, route từ DHCP |
| **Automatic (chỉ địa chỉ)** | `auto` + `ignore-auto-dns=true` | IP từ DHCP, DNS tự nhập |
| **Manual** | `manual` | Tự nhập toàn bộ |
| **Link-Local** | `link-local` | 169.254.x.x |
| **Disabled** | `disabled` | Tắt IPv4 |
| **Shared** | `shared` | Chia sẻ kết nối (v2) |

**Yêu cầu chức năng:**

- FR-A1: Toggle Automatic ↔ Manual ngay trên tray menu cho connection đang active.
- FR-A2: Khi chuyển sang Manual, **tự điền sẵn** IP/gateway/DNS đang nhận từ DHCP → người dùng chỉ cần sửa. Đây là chi tiết UX quan trọng, tiết kiệm rất nhiều thời gian.
- FR-A3: Khi chuyển về Automatic, **giữ lại** cấu hình manual cũ trong metadata app để chuyển ngược lại không mất dữ liệu.
- FR-A4: Route tĩnh **được giữ nguyên** ở cả hai chế độ (`ipv4.routes` độc lập với `ipv4.method`) — cần nói rõ trong UI vì nhiều người hiểu nhầm.
- FR-A5: Sub-toggle độc lập: `ignore-auto-dns`, `ignore-auto-routes`, `never-default`, `may-fail`.

---

### 3.5 F6 — Bộ cấu hình (Profile)

Chức năng gom toàn bộ F1–F5 thành các bối cảnh có tên, chuyển đổi bằng một thao tác.

**Ví dụ thực tế:**

| Bộ cấu hình | Connection | IPv4 | Route tĩnh | Proxy |
|---|---|---|---|---|
| 🏢 **Công ty** | Wired `enp3s0` | Manual `10.0.5.20/24` | 3 route tới subnet nội bộ | Proxy Công ty (manual) |
| 🏠 **Nhà** | Wi-Fi `WiFi-Nha` | Automatic | Không | Tắt |
| 🏨 **Khách hàng A** | Wi-Fi `KH-A-Guest` | Automatic | 1 route tới `172.16.0.0/12` | PAC URL của khách hàng |
| ✈️ **Offline** | Tắt Wi-Fi, ngắt wired | — | — | Tắt |

#### 3.5.1 Vấn đề thiết kế cốt lõi: Bộ cấu hình lưu dữ liệu mạng ở đâu?

Đây là quyết định kiến trúc quan trọng nhất của F6, ảnh hưởng tới toàn bộ phần còn lại. Ba mô hình khả dĩ:

**Mô hình A — Overlay (Bộ cấu hình tự lưu cấu hình IPv4, ghi đè lên NM connection khi kích hoạt)**

```
Bộ cấu hình "Công ty" ──ghi đè──> NM connection "enp3s0"  (một connection duy nhất, bị mutate)
Bộ cấu hình "Nhà"     ──ghi đè──┘
```
- ✅ Danh sách NM connection gọn, không sinh thêm
- ❌ **Phá huỷ**: mỗi lần chuyển là ghi đè cấu hình cũ; nếu app crash giữa chừng thì connection ở trạng thái lai
- ❌ Xung đột với chỉnh sửa từ bên ngoài (GNOME Settings, `nmcli`)
- ❌ Phải tự implement snapshot/restore — làm lại việc NM đã làm sẵn

**Mô hình B — Reference thuần (Bộ cấu hình chỉ trỏ tới NM connection có sẵn)**

```
Bộ cấu hình "Công ty" ──trỏ──> NM connection "LAN Công ty"
Bộ cấu hình "Nhà"     ──trỏ──> NM connection "WiFi Nhà"
```
- ✅ Đơn giản nhất, không ghi đè gì
- ❌ Không tạo được **hai biến thể route khác nhau trên cùng một mạng vật lý** — nhu cầu rất thực tế (cùng cắm LAN công ty nhưng chế độ dev cần route qua jump host, chế độ thường thì không)

**Mô hình C — Managed connection (mỗi Bộ cấu hình sở hữu NM connection riêng do app tạo)** ⭐ Khuyến nghị

```
Bộ cấu hình "Công ty" ──sở hữu──> NM connection "netmgr:cong-ty:enp3s0"   (autoconnect=false)
Bộ cấu hình "Dev"     ──sở hữu──> NM connection "netmgr:dev:enp3s0"       (autoconnect=false)
                                   ↑ cùng interface, khác route
```

- ✅ **Đúng với thiết kế gốc của NetworkManager** — NM vốn hỗ trợ nhiều connection profile trên cùng một device và chuyển giữa chúng. Ta đang dùng đúng công cụ cho đúng việc.
- ✅ Chuyển Bộ cấu hình = `activate_connection()` — thao tác **nguyên tử, có sẵn rollback** của NM, không phải tự viết
- ✅ Không mutate cấu hình của người dùng; connection do app tạo tách biệt hoàn toàn khỏi connection có sẵn
- ✅ NM lo phần persistence, secrets, permission — app không lưu bản sao
- ⚠️ Sinh thêm connection trong danh sách của GNOME Settings → giảm thiểu bằng tiền tố tên `netmgr:` và trường `connection.metered`/comment đánh dấu
- ⚠️ Với Wi-Fi: nhiều connection cùng SSID có thể khiến NM chọn nhầm → **bắt buộc đặt `autoconnect=false`** cho mọi managed connection và chỉ kích hoạt tường minh
- ⚠️ Secret Wi-Fi bị lưu trùng ở mỗi biến thể → chấp nhận được, hoặc dùng `secret-flags=agent-owned` để chia sẻ qua keyring

**Quyết định: Mô hình C**, với cơ chế "adopt/clone":
- Khi tạo Bộ cấu hình, người dùng chọn connection nền → app **clone** nó thành managed connection của Bộ cấu hình đó.
- Cho phép **tham chiếu (không clone)** một connection có sẵn nếu Bộ cấu hình không cần biến thể riêng — tức lai Mô hình B cho trường hợp đơn giản. Trường `owned: bool` trong binding quyết định điều này.
- Xoá Bộ cấu hình → xoá luôn managed connection thuộc sở hữu; connection chỉ được tham chiếu thì giữ nguyên. Phải hỏi xác nhận và liệt kê rõ cái gì sẽ bị xoá.

#### 3.5.2 Cấu trúc một Bộ cấu hình

```
Profile
├── id: str                      # uuid
├── name: str                    # "Công ty"
├── icon: str                    # emoji hoặc tên icon
├── description: str
├── order: int                   # thứ tự hiển thị trên tray
│
├── bindings: list[Binding]      # phần mạng
│   └── Binding
│       ├── device_match: str    # tên interface, hoặc "any-wifi" / "any-ethernet"
│       ├── action: ACTIVATE | DISCONNECT | LEAVE_ALONE
│       ├── connection_uuid: str # managed hoặc tham chiếu
│       ├── owned: bool          # True = app tạo & sở hữu, xoá cùng profile
│       └── required: bool       # False = không có thiết bị này thì bỏ qua, không coi là lỗi
│
├── radio:                       # trạng thái phần cứng
│   ├── wifi_enabled: bool | None    # None = không đụng tới
│   └── (v2) wwan_enabled
│
├── proxy: ProxyProfile | ref    # phần proxy — inline hoặc trỏ tới proxy profile dùng chung
│
├── dns_override: list[str]|None # v2 — ghi đè DNS ở mức global
│
├── auto_rules: list[Rule]       # v2 — điều kiện tự kích hoạt
│   └── Rule: MATCH_SSID | MATCH_GATEWAY_MAC | MATCH_SUBNET | MATCH_ETHERNET_LINK
│
└── hooks:                       # v2 — script chạy sau khi áp dụng
    ├── post_activate: str|None
    └── pre_deactivate: str|None
```

Điểm đáng chú ý: **`action: LEAVE_ALONE`** và **`required: false`** là hai trường bắt buộc phải có ngay từ v1. Không có chúng, một Bộ cấu hình cấu hình cho laptop có dock sẽ luôn báo lỗi khi dùng laptop rời dock.

#### 3.5.3 Yêu cầu chức năng

- **FR-PR1 — CRUD**: tạo, sửa, xoá, nhân bản Bộ cấu hình. "Nhân bản" quan trọng: đa số Bộ cấu hình mới bắt đầu từ một cái đã có.
- **FR-PR2 — Chuyển đổi một click**: radio group trên tray menu; Bộ cấu hình đang active được đánh dấu rõ.
- **FR-PR3 — Áp dụng nguyên tử**: các bước phải thực hiện theo thứ tự xác định và **rollback toàn bộ nếu bất kỳ bước bắt buộc nào thất bại** (xem 3.5.4).
- **FR-PR4 — Chụp trạng thái hiện tại thành Bộ cấu hình mới** ("Save current state as profile"). Đây là cách tạo Bộ cấu hình tự nhiên nhất: người dùng cấu hình tay cho chạy được, rồi lưu lại. Ưu tiên cao.
- **FR-PR5 — Phát hiện lệch (drift)**: nếu cấu hình thực tế không còn khớp Bộ cấu hình đang active (người dùng sửa qua GNOME Settings, hoặc DHCP đổi), hiển thị badge "đã chỉnh sửa" và cho 2 lựa chọn: *Lưu thay đổi vào Bộ cấu hình* hoặc *Khôi phục về Bộ cấu hình*.
- **FR-PR6 — Import / Export**: JSON hoặc TOML, **loại bỏ toàn bộ secret**. Mục đích: một người trong team cấu hình xong, export cho cả team dùng. File export phải là plaintext đọc được và review được (đưa vào Git repo nội bộ được).
- **FR-PR7 — Xem trước (dry-run)**: trước khi áp dụng, hiển thị diff "sẽ thay đổi những gì" so với trạng thái hiện tại. Bắt buộc với Bộ cấu hình import từ ngoài.
- **FR-PR8 — Bộ cấu hình mặc định**: chỉ định một Bộ cấu hình áp dụng khi đăng nhập; hoặc "Không tự áp dụng" (mặc định an toàn).
- **FR-PR9 — Trạng thái "Không dùng Bộ cấu hình"**: người dùng phải luôn thoát được khỏi cơ chế profile và quay về quản lý thủ công.
- **FR-PR10 (v2) — Tự động chuyển**: theo SSID đang kết nối, MAC của default gateway (nhận diện mạng vật lý chính xác hơn SSID), subnet nhận được từ DHCP, hoặc trạng thái link ethernet. Cần cơ chế chống dao động (hysteresis ≥ 10s) và luôn cho phép ghim thủ công đè lên tự động.

#### 3.5.4 Luồng áp dụng Bộ cấu hình

Thứ tự thực hiện có ý nghĩa — sai thứ tự sẽ gây mất mạng giữa chừng:

```
1. Validate       Bộ cấu hình còn hợp lệ? Connection còn tồn tại? Device có mặt?
                  → Nếu binding required thất bại: dừng, báo lỗi, KHÔNG thay đổi gì
2. Checkpoint     NM.Client.checkpoint_create(devices, timeout=90, DESTROY_ALL)
                  → lưới an toàn: NM tự rollback nếu app chết giữa chừng
3. Radio          Bật Wi-Fi nếu cần (bật TRƯỚC, vì kết nối cần radio)
4. Proxy          Áp dụng proxy — làm sớm, không phụ thuộc mạng, và không gây mất kết nối
5. Deactivate     Ngắt các connection có action=DISCONNECT
6. Activate       Kích hoạt các connection theo binding, chờ tới trạng thái ACTIVATED
                  → timeout mỗi connection 30s
7. Verify         Kiểm tra: có IP? route đã áp? (tuỳ chọn: ping gateway)
8. Commit/Rollback
   ├─ Thành công → checkpoint_destroy() + thông báo + đổi icon tray
   └─ Thất bại   → checkpoint_rollback() + thông báo lỗi nêu rõ bước nào hỏng
9. Radio tắt      Tắt Wi-Fi nếu cần (làm CUỐI, sau khi mọi thứ đã xong)
```

**Điểm quan trọng:** bước 2 dùng **NM checkpoint** thay vì tự viết snapshot. NM sẽ tự động rollback khi hết timeout nếu tiến trình app chết — đây là bảo vệ mà code Python không tự làm được.

> ⚠️ **Vấn đề mới phát hiện khi đo permission (§4.6):** `org.freedesktop.NetworkManager.checkpoint-rollback` trả về **`auth`**, không phải `yes`. Nghĩa là mỗi lần tạo checkpoint sẽ **bật hộp thoại xác thực polkit**. Với một tính năng mà mục đích là "chuyển bối cảnh bằng một click", bắt nhập mật khẩu mỗi lần là không chấp nhận được.
>
> Ba hướng xử lý, cần chốt trước khi làm P5:
>
> | Hướng | Ưu | Nhược |
> |---|---|---|
> | **A. Chấp nhận prompt** | Không phải làm gì thêm; an toàn nhất | Phá trải nghiệm "một click"; người dùng sẽ tắt tính năng |
> | **B. Bỏ checkpoint, tự snapshot + restore** | Không cần quyền đặc biệt | Mất bảo vệ "app chết thì tự rollback" — chính là rủi ro R9 nghiêm trọng nhất |
> | **C. Cài polkit rule cho phép user trong nhóm `sudo`** | Giữ nguyên bảo vệ, không prompt | Cần file trong `/etc/polkit-1/rules.d/` → phải cài bằng `.deb`, và là quyết định bảo mật cần người dùng đồng ý rõ ràng |
>
> Nghiêng về **C**, có fallback sang **A** khi rule chưa được cài. Ghi vào câu hỏi mở số 11.

**UI trong lúc áp dụng:** hiển thị tiến trình từng bước (không phải spinner vô định), vì bước 6 có thể mất 10–30 giây. Có nút Huỷ → kích hoạt rollback.

#### 3.5.5 Ràng buộc và trường hợp biên

| Tình huống | Xử lý |
|---|---|
| Đang áp dụng thì người dùng bấm Bộ cấu hình khác | Khoá UI trong lúc áp dụng; xếp hàng tối đa 1 yêu cầu, huỷ yêu cầu cũ hơn |
| Bộ cấu hình tham chiếu connection đã bị xoá | Đánh dấu Bộ cấu hình "hỏng", disable nút áp dụng, chỉ rõ binding nào hỏng |
| Bộ cấu hình tham chiếu interface không có mặt | Nếu `required=false` → bỏ qua, ghi log. Nếu `required=true` → thất bại có kiểm soát |
| Mất mạng giữa chừng khi áp dụng qua SSH/VPN | Checkpoint rollback tự động cứu; cảnh báo trước nếu phát hiện session SSH đang hoạt động |
| Hai Bộ cấu hình cùng bind một interface | Hợp lệ — chỉ một cái active tại một thời điểm |
| Import Bộ cấu hình từ file không tin cậy | Bắt buộc dry-run + xác nhận; validate mọi trường; **không** chạy `hooks` từ file import trừ khi người dùng bật thủ công |
| Suspend/resume | Kiểm tra lại drift khi resume, không tự áp dụng lại (tránh bất ngờ) |

---

## 4. Phân tích kỹ thuật

### 4.1 Lựa chọn kiến trúc UI — Quyết định then chốt

Đây là ràng buộc kỹ thuật lớn nhất của dự án:

> **Không thể load `Gtk-3.0` và `Gtk-4.0` trong cùng một tiến trình Python.**
> Mà `AyatanaAppIndicator3` (cách đơn giản nhất để làm tray) lại **chỉ liên kết với GTK3**.

Ba phương án:

#### Phương án A — GTK3 + AyatanaAppIndicator3 (một tiến trình)

```
┌──────────────── netmgr (1 process) ───────────────┐
│  AyatanaAppIndicator3  →  GtkMenu (tray)          │
│  Gtk.Window (GTK3)     →  cửa sổ cấu hình         │
└───────────────────────────────────────────────────┘
```
- ✅ Đơn giản nhất, ít code, không cần IPC
- ✅ Thư viện chín muồi, tài liệu nhiều
- ❌ GTK3 đã deprecated; giao diện trông cũ so với Ubuntu 26.04
- ❌ Không dùng được libadwaita

#### Phương án B — GTK4/libadwaita + tray process riêng (hai tiến trình) ⭐ Khuyến nghị

```
┌── netmgr-tray (nhẹ) ──┐         ┌── netmgr-ui (GTK4 + Adw) ──┐
│  SNI qua D-Bus        │◄──D-Bus─►│  Cửa sổ cấu hình chính     │
│  (dbus-next / pystray)│         │  Áp dụng thay đổi           │
└───────────┬───────────┘         └──────────────┬──────────────┘
            └──────────── libnm ─────────────────┘
```
- ✅ UI hiện đại, đúng chuẩn Ubuntu 26.04
- ✅ Tray process rất nhẹ, luôn chạy; UI process chỉ khởi động khi cần → tiết kiệm RAM
- ✅ Tray crash không kéo theo UI và ngược lại
- ❌ Phức tạp hơn: cần định nghĩa D-Bus interface nội bộ, quản lý vòng đời 2 process
- ❌ Phải tự implement SNI (`org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`) hoặc dùng thư viện

#### Phương án C — Rust + GTK4 hoặc Qt

- ✅ Hiệu năng, đóng gói binary đơn
- ❌ Tốc độ phát triển chậm hơn nhiều, không tận dụng được PyGObject đã có sẵn

#### Phương án D — GTK4 + tự implement SNI, MỘT tiến trình ✅ ĐÃ CHỌN VÀ ĐÃ IMPLEMENT

Phát hiện khi bắt tay làm: gói `gir1.2-ayatanaappindicator3-0.1` **chưa được cài** trên máy (chỉ có thư viện C, không có binding Python), tức Phương án A cần `sudo apt install` thêm. Điều đó làm lộ ra một lựa chọn tốt hơn cả A lẫn B:

> Nếu **tự implement StatusNotifierItem qua `Gio.DBus`** thì không còn dính GTK3 nữa → GTK4 và tray sống chung **một tiến trình**, và không cần cài thêm gói nào.

```
┌──────────── netmgr (1 process) ────────────┐
│  tray/sni.py   org.kde.StatusNotifierItem  │  ← Gio.DBus thuần
│                com.canonical.dbusmenu      │
│  ui/           GTK4 + libadwaita           │
│  infra/        libnm                       │
└────────────────────────────────────────────┘
Phụ thuộc: python3-gi, gir1.2-nm-1.0, gir1.2-secret-1 — đều CÓ SẴN
```

| So với | Hơn ở chỗ |
|---|---|
| A (GTK3) | UI hiện đại, không dùng GTK3 deprecated, không cần cài thêm gói |
| B (2 tiến trình) | Không phải định nghĩa D-Bus nội bộ, không phải quản lý vòng đời 2 process |

Đánh đổi: ~450 dòng code D-Bus cho tray. Đã viết và verify xong (§4.1.1), nên chi phí này giờ là chi phí chìm.

**Ràng buộc vẫn giữ nguyên:** tầng core (`domain/`) tuyệt đối không import `gi` — nhờ đó 200+ unit test chạy được không cần NetworkManager, và đổi UI framework sau này vẫn rẻ.

#### 4.1.1 Kết quả thực nghiệm với extension appindicator

Ba điều dưới đây rút ra từ việc **đọc source** `ubuntu-appindicators@ubuntu.com` và chạy thử, không phải suy đoán. Chúng thay đổi thiết kế UI ở §5.1:

| # | Phát hiện | Bằng chứng | Hệ quả thiết kế |
|---|---|---|---|
| 1 | **Tooltip hoàn toàn không hoạt động trên GNOME** | `interfaces-xml/StatusNotifierItem.xml` comment out property `ToolTip` kèm ghi chú *"we don't support tooltip"*; không file `.js` nào của extension nhắc tới tooltip | Mọi thông tin (IP, cường độ sóng, trạng thái proxy) **phải nằm trong menu** dưới dạng item disabled. Vẫn export `ToolTip` vì KDE Plasma/waybar có dùng, nhưng không được đặt thông tin thiết yếu ở đó |
| 2 | **`XAyatanaLabel` CÓ hoạt động** | `appIndicator.js` liệt kê nó trong `OPTIONAL_PROPERTIES` và có getter `label` | Nhãn text cạnh icon (tên Bộ cấu hình) khả thi đúng như dự kiến |
| 3 | **Khai báo method `Activate` làm chậm click trái** | `appIndicator.js`: `supportsActivation = !!interfaceInfo.lookup_method('Activate')`; `indicatorStatusIcon.js`: nếu `supportsActivation !== false` thì chờ xem có double-click rồi mới xử lý | **Cố tình KHÔNG khai báo `Activate`** → click trái mở menu tức thì |

Ngoài ra `dbusMenu.js` honor `label`, `enabled`, `visible`, `type`, `icon-name`, `toggle-type`, `toggle-state`, `children-display` — nhưng **không** honor `accessible-desc`.

### 4.2 Lựa chọn backend NetworkManager

| Tiêu chí | `nmcli` (subprocess) | **libnm (GI)** ⭐ | D-Bus thuần |
|---|---|---|---|
| Tốc độ phát triển | Nhanh lúc đầu | Trung bình | Chậm |
| Parse kết quả | Fragile — parse text | Object có kiểu | Parse variant thủ công |
| Sự kiện realtime | ❌ Phải polling | ✅ GObject signals | ✅ Signals |
| Bất đồng bộ | ❌ Chặn UI | ✅ Async native | ✅ Async |
| Xử lý secrets | Hạn chế | ✅ Đầy đủ (v1 không cần — xem 3.2.1) | Phức tạp |
| Phụ thuộc | Cần gói `network-manager` | typelib `NM-1.0` (đã có) | Không |

**Quyết định: dùng `libnm` qua GObject Introspection.** Lý do quyết định là **signals** — không phải polling để cập nhật danh sách Wi-Fi và trạng thái kết nối.

Giữ một lớp mỏng gọi `nmcli` chỉ cho: (a) chế độ debug/dump, (b) export cấu hình dưới dạng lệnh để người dùng tái sử dụng trong script.

### 4.3 Kiến trúc phân tầng

```
┌──────────────────────────────────────────────────────────┐
│ Presentation                                             │
│   tray/        indicator, menu builder                   │
│   ui/          main window, dialogs (route, wifi, proxy) │
│   notify/      desktop notifications                     │
└────────────────────────┬─────────────────────────────────┘
                         │ chỉ gọi xuống, không gọi ngược
┌────────────────────────▼─────────────────────────────────┐
│ Application / Services                                   │
│   ProfileService      CRUD Bộ cấu hình, chọn active      │
│   ProfileApplier      luồng áp dụng 9 bước + rollback    │
│   DriftDetector       so sánh thực tế vs Bộ cấu hình     │
│   ConnectionService   liệt kê, activate, deactivate      │
│   RouteService        CRUD route + validate + apply      │
│   WifiService         scan, connect, forget              │
│   ProxyService        điều phối các ProxyLayer           │
│   AppState            nguồn sự thật duy nhất + signals   │
└────────────────────────┬─────────────────────────────────┘
┌────────────────────────▼─────────────────────────────────┐
│ Domain (thuần Python, không phụ thuộc GTK/NM)            │
│   models/    Connection, Route, ProxyProfile, Device     │
│   validators/  ipv4, route, proxy                        │
└────────────────────────┬─────────────────────────────────┘
┌────────────────────────▼─────────────────────────────────┐
│ Infrastructure (adapter)                                 │
│   nm/         NMFacade — bọc libnm, phát signal          │
│   proxy/      GSettingsLayer, EnvdLayer, AptLayer(v2)    │
│   secrets/    SecretStore — libsecret                    │
│   store/      ProfileStore — đọc/ghi TOML, migrate schema│
│   privileged/ PolkitHelper client (v2)                   │
└──────────────────────────────────────────────────────────┘
```

**Nguyên tắc bắt buộc:**
0. **Code widget không chứa logic nghiệp vụ.** Mọi quyết định "hiện chữ gì, bật/tắt nút nào" nằm ở `ui/view_models.py` — thuần Python, có test. Đây là cách duy nhất khiến phần khó kiểm chứng nhất của app (UI) trở nên kiểm chứng được: 38 test cho view-model chạy trong 0,1 giây, trong khi test widget thật cần cả compositor.
1. Tầng Domain **không import `gi`**. Nhờ vậy validator và model unit-test được mà không cần D-Bus, và migrate GTK3→GTK4 không đụng tới.
2. Mọi thao tác NM là **async với callback** — không bao giờ block GLib main loop.
3. UI **không** đọc trực tiếp từ libnm; chỉ đọc `AppState` và nghe signal của nó.
4. `NMFacade` là chỗ duy nhất biết về libnm → có thể thay bằng `FakeNMFacade` khi test.

### 4.4 Mô hình dữ liệu

```python
@dataclass(frozen=True)
class Ipv4Route:
    dest: str
    prefix: int
    next_hop: str | None = None
    metric: int | None = None
    table: int | None = None
    src: str | None = None
    onlink: bool = False
    enabled: bool = True          # metadata riêng của app
    source: RouteSource = STATIC  # STATIC | DHCP | KERNEL (read-only nếu != STATIC)

@dataclass
class Ipv4Config:
    method: Ipv4Method            # AUTO | MANUAL | LINK_LOCAL | DISABLED
    addresses: list[Ipv4Address]
    gateway: str | None
    dns: list[str]
    dns_search: list[str]
    routes: list[Ipv4Route]
    ignore_auto_dns: bool
    ignore_auto_routes: bool
    never_default: bool
    route_metric: int | None
    may_fail: bool

@dataclass
class ConnectionProfile:                  # ánh xạ 1-1 với NM.RemoteConnection
    uuid: str
    id: str
    type: ConnType                # ETHERNET | WIFI
    interface_name: str | None
    autoconnect: bool
    autoconnect_priority: int
    ipv4: Ipv4Config
    wifi: WifiSettings | None
    is_active: bool
    device: DeviceInfo | None
    managed_by_app: bool = False  # True nếu do app tạo cho một Bộ cấu hình

# ── F6 ──────────────────────────────────────────────────────

class BindingAction(Enum):
    ACTIVATE = "activate"
    DISCONNECT = "disconnect"
    LEAVE_ALONE = "leave_alone"

@dataclass
class Binding:
    device_match: str             # "enp3s0" | "any-ethernet" | "any-wifi"
    action: BindingAction
    connection_uuid: str | None
    owned: bool = False           # app sở hữu → xoá cùng Bộ cấu hình
    required: bool = False        # True → thiếu thiết bị là lỗi

@dataclass
class Profile:                    # "Bộ cấu hình" — khái niệm F6
    id: str
    name: str
    icon: str = "🌐"
    description: str = ""
    order: int = 0
    bindings: list[Binding] = field(default_factory=list)
    wifi_enabled: bool | None = None      # None = không đụng tới
    proxy: ProxyProfile | str | None = None   # inline | id tham chiếu | None
    auto_rules: list[AutoRule] = field(default_factory=list)   # v2
    is_broken: bool = False       # tính toán runtime: có binding hỏng

@dataclass
class ApplyStep:                  # phục vụ UI tiến trình + báo lỗi
    name: str
    status: PENDING | RUNNING | OK | FAILED | SKIPPED | ROLLED_BACK
    detail: str = ""
```

**Lưu trữ:** `~/.config/netmgr/profiles.toml` — định dạng text đọc được, review được, đưa vào Git nội bộ được. Secret **không** nằm trong file này (xem 4.7). File có trường `schema_version` để migrate khi cấu trúc đổi.

### 4.5 Luồng ghi cấu hình (quan trọng — dễ sai)

Ghi một thay đổi route đúng cách gồm 5 bước:

```
1. Đọc:    conn = client.get_connection_by_uuid(uuid)
2. Clone:  new = NM.SimpleConnection.new_clone(conn)     ← KHÔNG sửa trực tiếp object gốc
3. Sửa:    s_ip4 = new.get_setting_ip4_config()
           s_ip4.clear_routes(); for r in routes: s_ip4.add_route(...)
4. Verify: new.verify()                                   ← bắt lỗi TRƯỚC khi gửi đi
5. Ghi:    conn.update2(new.to_dbus(...), flags, None, cb)
6. Áp dụng: device.reapply_async(...)  hoặc  client.activate_connection_async(...)
```

**Các bẫy thường gặp:**
- Sửa trực tiếp `NM.RemoteConnection` → thay đổi không được commit hoặc bị ghi đè khi NM refresh.
- Quên bước `verify()` → lỗi D-Bus khó hiểu ở tận bước update.
- Quên `reapply` → người dùng bấm Save nhưng route không có hiệu lực, tưởng app hỏng. **UI phải nói rõ trạng thái này.**
- `reapply` **thất bại với một số thay đổi lớn** (đổi method auto↔manual) → phải fallback sang deactivate + activate, và cảnh báo người dùng là kết nối sẽ ngắt trong giây lát.
- Update2 flags: dùng `TO_DISK` để ghi vĩnh viễn, `IN_MEMORY` cho thay đổi tạm (hữu ích cho tính năng "thử route này 60 giây rồi tự rollback").

### 4.6 Quản lý quyền (Polkit)

NetworkManager kiểm soát quyền qua polkit. Các action liên quan:

| Action | Dùng cho |
|---|---|
| `...settings.modify.own` | Sửa profile do user tạo |
| `...settings.modify.system` | Sửa profile hệ thống (available to all users) |
| `...network-control` | Activate / deactivate connection |
| `...enable-disable-wifi` | Bật/tắt Wi-Fi radio |
| ~~`...wifi.scan`~~ | Quét Wi-Fi — **v1 không cần** (3.2.1) |

**⚠️ Cạm bẫy đã gặp thật: permission nạp BẤT ĐỒNG BỘ.**

Đo trên máy: ngay sau `NM.Client.new()`, **mọi** permission đều trả về `unknown`; chỉ sau khi main loop chạy vài trăm ms và signal `permission-changed` bắn thì mới thành `yes`/`no`. Nếu coi `unknown` là "không có quyền" thì **toàn bộ UI sẽ bị xám lúc khởi động** rồi mới sáng lại — vừa xấu vừa gây hiểu nhầm.

Vì vậy permission phải là **tri-state**, không phải bool:

| Trạng thái | Ý nghĩa | UI làm gì |
|---|---|---|
| `YES` | Được phép | Bình thường |
| `AUTH` | Được phép, nhưng sẽ hiện hộp thoại xác thực polkit | Bình thường, có thể gắn biểu tượng khiên |
| `NO` | Bị từ chối | **Disable** kèm giải thích |
| `UNKNOWN` | Chưa nạp xong | **Không disable** — cứ cho thao tác, thất bại sẽ báo lỗi rõ ràng |

Quy tắc: chỉ `NO` mới chặn UI. Và phải nghe signal `permission-changed` để cập nhật, nếu không UI sẽ kẹt ở trạng thái "chưa biết" mãi.

**Kết quả đo thực tế trên máy phát triển:**

```
sửa cấu hình hệ thống: yes      điều khiển mạng:     yes
bật/tắt wifi:          yes      checkpoint rollback: auth   ← đáng chú ý
```

**Thiết kế:**
- Lúc khởi động gọi `NM.Client.get_permission_result()` để biết user được làm gì, và **nghe `permission-changed`**.
- **Disable** các nút chắc chắn không có quyền, kèm giải thích trong menu — thay vì để người dùng bấm rồi nhận lỗi.
- Không tự chạy app bằng `sudo` (sẽ hỏng gsettings, keyring, và tray).
- Với thao tác cần root ngoài NM (`apt.conf`, docker daemon — v2): tách **một helper nhỏ chạy qua polkit**, giao tiếp qua D-Bus system bus, với action riêng và policy `auth_admin_keep`. Helper phải nhận input đã được whitelist chặt, không nhận đường dẫn tự do.

### 4.7 Bảo mật

| Rủi ro | Biện pháp |
|---|---|
| Lộ mật khẩu Wi-Fi | ✅ **Loại bỏ hoàn toàn khỏi mô hình rủi ro** — bỏ chức năng kết nối mạng mới (3.2.1) nên app không implement secret agent, không đọc, không cầm, không lưu PSK. Mật khẩu Wi-Fi nằm trọn trong NM |
| Lộ mật khẩu proxy | Lưu qua **libsecret** (GNOME Keyring), không ghi ra file config |
| Ghi secret vào log | Logger có bộ lọc redact các key `password`, `psk`, `secret`, `authentication-password` |
| Ghi file config bị lộ | `~/.config/netmgr/` mode `0700`, file `0600`; **không** ghi mật khẩu vào file |
| Privilege escalation qua helper | Helper chỉ nhận enum/giá trị đã validate, không nhận path/command; action polkit riêng biệt |
| Route độc hại (redirect traffic) | Cảnh báo rõ khi thêm `0.0.0.0/0` hoặc route trùng default gateway |
| Config môi trường bị inject | Escape kỹ giá trị khi sinh `environment.d` (không cho ký tự newline, `$`, backtick trong host/port) |
| Export Bộ cấu hình làm lộ mật khẩu | File export **loại bỏ toàn bộ secret** (mật khẩu Wi-Fi, auth proxy); thay bằng placeholder `<cần nhập khi import>`; test tự động phải quét file export tìm secret |
| Import Bộ cấu hình độc hại | Validate toàn bộ trường; bắt buộc dry-run + xác nhận; **không chạy `hooks`** từ file import trừ khi người dùng bật thủ công từng hook |
| Bộ cấu hình chuyển traffic qua proxy/route của kẻ tấn công | Dry-run luôn hiển thị rõ proxy và default route sẽ đổi thành gì |

### 4.8 Hiệu năng và tài nguyên

Đây là app chạy thường trú 24/7 nên tài nguyên là yêu cầu chức năng:

- **RAM mục tiêu:** tray process < 40 MB RSS khi idle.
- **CPU khi idle:** ~0% — bắt buộc **event-driven qua GObject signals**, cấm polling định kỳ.
- **Wi-Fi scan: app không bao giờ chủ động quét.** Vì đã bỏ chức năng kết nối mạng mới (3.2.1), app chỉ đọc AP đang kết nối để lấy tín hiệu/tần số. Lợi ích: không tốn pin, không có nguy cơ scan làm rớt kết nối đang dùng, không phải quản lý throttle/cache AP.
- **Cập nhật cường độ tín hiệu:** nghe `notify::strength` của AP đang kết nối, nhưng **throttle xuống tối đa 1 lần/2 giây** — signal này bắn rất dày và không ai cần độ phân giải cao hơn thế.
- **Lazy load:** cửa sổ chính chỉ khởi tạo khi người dùng mở lần đầu; đóng cửa sổ thì **hide** chứ không destroy (mở lại tức thì), nhưng destroy sau 5 phút không dùng để trả RAM.
- **Debounce:** signal `state-changed` của NM bắn rất dày lúc chuyển mạng → gom nhóm 200ms trước khi rebuild menu.

---

## 5. Thiết kế giao diện

### 5.1 Tray menu (góc phải phía trên)

Giữ menu **nông và ngắn** — đây là lối vào thao tác nhanh, không phải nơi cấu hình đầy đủ. Nhắc lại ràng buộc: menu do GNOME Shell render, chỉ dùng được item cơ bản.

```
🌐 Network Manager
├─ ● Đã kết nối: WiFi-CongTy          (item vô hiệu, chỉ hiển thị)
│    192.168.1.42 · Automatic · Proxy bật
├──────────────────────────────
├─ Bộ cấu hình: 🏢 Công ty ⚠         (⚠ = có lệch so với Bộ cấu hình)
│   ├─ ● 🏢 Công ty                   (radio group — đổi cả mạng lẫn proxy)
│   ├─ ○ 🏠 Nhà
│   ├─ ○ 🏨 Khách hàng A
│   ├─ ○ ✈️ Offline
│   ├─ ○ ✖ Không dùng Bộ cấu hình
│   ├──────────────────────
│   ├─ Lưu trạng thái hiện tại thành Bộ cấu hình...
│   └─ Quản lý Bộ cấu hình...
├──────────────────────────────
├─ Proxy                        ▸
│   ├─ ○ Tắt                          (radio group)
│   ├─ ● Proxy Công ty
│   ├─ ○ Proxy Dự phòng
│   ├──────────────────────
│   └─ Quản lý profile...
├─ Wi-Fi                        ▸
│   ├─ ☑ Bật Wi-Fi                    (checkbox)
│   ├──────────────────────
│   ├─ ● WiFi-CongTy      ▂▄▆█       (chỉ các connection ĐÃ LƯU)
│   ├─ ○ WiFi-Nha
│   ├─ ○ KH-A-Guest
│   ├──────────────────────
│   └─ Quản lý mạng đã lưu...
├─ Wired                        ▸
│   ├─ ● enp3s0 — LAN Công ty
│   └─ ○ enp3s0 — LAN Tĩnh
├──────────────────────────────
├─ ☑ Automatic (DHCP)                 (áp cho connection đang active)
├─ Route tĩnh (3)...                  (mở thẳng tab Routes)
├──────────────────────────────
├─ Cài đặt...
└─ Thoát
```

**Icon trạng thái** phải phản ánh tức thì (dùng symbolic icon để tự đổi màu theo theme sáng/tối):

| Trạng thái | Icon |
|---|---|
| Wi-Fi đã kết nối | `network-wireless-signal-{excellent,good,ok,weak}-symbolic` |
| Wired đã kết nối | `network-wired-symbolic` |
| Đang kết nối | `network-wireless-acquiring-symbolic` |
| Ngắt kết nối | `network-offline-symbolic` |
| **Proxy đang bật** | Overlay/emblem trên icon nền — dấu hiệu trực quan rằng proxy đang hoạt động |
| Đang áp dụng Bộ cấu hình | Icon nhấp nháy / `network-transmit-receive-symbolic`; bước hiện tại hiện trong menu |
| Bộ cấu hình bị lệch | `Status = NeedsAttention` |

**Về tên Bộ cấu hình trên tray:** đặt nhãn là tên Bộ cấu hình đang active (rút gọn ≤ 12 ký tự) qua property `XAyatanaLabel` — đã xác nhận hoạt động (§4.1.1 #2). Người dùng nhìn thấy ngay đang ở bối cảnh nào mà không cần mở menu. Cần có tuỳ chọn tắt nhãn cho người thích panel gọn.

> ⚠️ **Không có tooltip.** Đây là ràng buộc lớn nhất với thiết kế menu (§4.1.1 #1). Menu là nơi DUY NHẤT người dùng đọc được IP, cường độ sóng, trạng thái proxy — nên các dòng đó phải nằm ngay đầu menu dưới dạng item disabled, không phải giấu trong tooltip.

**Click trái mở menu ngay** (§4.1.1 #3). Click giữa dành cho hành động phụ; click phải cũng mở menu (mặc định của GNOME).

**Chống nhiễu nội dung menu:** cường độ Wi-Fi dao động từng giây (88 → 91 → 89). Nếu hiển thị số thô thì menu "đổi" liên tục và host phải tải lại toàn bộ. Giải pháp: **làm tròn về bội số của 10**, và `set_menu()` chỉ tăng revision khi layout thực sự khác đi.

### 5.2 Cửa sổ chính

```
┌─ Network Manager ─────────────────────────────────── ─ □ × ┐
│ ┌───────────────┬────────────────────────────────────────┐ │
│ │ 🗂 Bộ cấu hình│  WiFi-CongTy                            │ │
│ │   🏢 Công ty ●│  ┌─────┬──────┬────────┬──────┬──────┐ │ │
│ │               │  │Trạng│ IPv4 │ Routes │ DNS  │ Nâng │ │ │
│ │ 🔌 Wired      │  │thái │      │        │      │ cao  │ │ │
│ │   enp3s0  ●   │  └─────┴──────┴────────┴──────┴──────┘ │ │
│ │               │                                         │ │
│ │ 📶 Wi-Fi      │  Chế độ:  ( ) Automatic (DHCP)          │ │
│ │   wlp2s0  ●   │           (•) Manual                    │ │
│ │               │                                         │ │
│ │ 🌐 Proxy      │                                         │ │
│ │ ⚙  Tuỳ chọn   │                                         │ │
│ │               │  ☐ Bỏ qua DNS tự động                   │ │
│ │               │  ☐ Bỏ qua route tự động                 │ │
│ │               │  ☐ Không dùng làm default route         │ │
│ │               │                                         │ │
│ │               │      [ Huỷ ]  [ Lưu ]  [ Lưu & Áp dụng ]│ │
│ └───────────────┴────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 Tab Routes

```
┌─ Routes (IPv4) ─────────────────────────────────────────────┐
│  ☐ Bỏ qua route tự động từ DHCP                             │
│                                                              │
│  ┌──┬──────────────────┬───────────────┬───────┬─────┬────┐ │
│  │  │ Đích             │ Gateway       │Metric │Table│Nguồn│ │
│  ├──┼──────────────────┼───────────────┼───────┼─────┼────┤ │
│  │☑ │ 10.20.0.0/16     │ 192.168.1.1   │  100  │  -  │ 🔵 │ │
│  │☑ │ 172.16.0.0/12    │ 192.168.1.254 │  200  │ 100 │ 🔵 │ │
│  │☐ │ 10.99.0.0/24     │ 192.168.1.1   │  100  │  -  │ 🔵 │ │
│  │  │ 0.0.0.0/0        │ 192.168.1.1   │  600  │  -  │ ⚪ │ │
│  │  │ 192.168.1.0/24   │ (on-link)     │  600  │  -  │ ⚪ │ │
│  └──┴──────────────────┴───────────────┴───────┴─────┴────┘ │
│   🔵 Tĩnh (sửa được)    ⚪ Từ DHCP (chỉ đọc)                 │
│                                                              │
│  [+ Thêm] [✎ Sửa] [🗑 Xoá]        [Import] [Export] [Dán...] │
│                                                              │
│  ⚠ Có thay đổi chưa áp dụng    [Huỷ bỏ] [Lưu] [Lưu & Áp dụng]│
└──────────────────────────────────────────────────────────────┘
```

Dialog thêm/sửa route validate **realtime khi gõ**, hiển thị lỗi ngay dưới ô nhập, nút OK bị disable cho tới khi hợp lệ. Có ô nhập nhanh dạng một dòng `10.20.0.0/16 via 192.168.1.1 metric 100` để dán từ tài liệu.

### 5.4 Màn hình quản lý Bộ cấu hình

```
┌─ Bộ cấu hình ───────────────────────────────────────────────┐
│  ┌────────────────────┬────────────────────────────────────┐│
│  │ 🏢 Công ty      ● ⚠│  Tên:  [Công ty            ] 🏢    ││
│  │ 🏠 Nhà             │  Mô tả:[Mạng LAN văn phòng ]       ││
│  │ 🏨 Khách hàng A    │                                    ││
│  │ ✈️ Offline          │  ── Mạng ──────────────────────    ││
│  │                    │  enp3s0   [Kết nối ▾] LAN Công ty  ││
│  │ [+] [⧉] [🗑] [↕]   │           ☑ Riêng cho Bộ cấu hình  ││
│  │                    │           ☐ Bắt buộc  [Sửa route…] ││
│  │ [Import] [Export]  │  wlp2s0   [Ngắt kết nối ▾]         ││
│  │                    │  Wi-Fi    ( )Bật (•)Tắt ( )Giữ nguyên│
│  │                    │  [+ Thêm thiết bị]                 ││
│  │                    │                                    ││
│  │                    │  ── Proxy ─────────────────────    ││
│  │                    │  (•) Dùng cấu hình riêng           ││
│  │                    │  ( ) Dùng proxy dùng chung: [ ▾ ]  ││
│  │                    │  ( ) Tắt proxy                     ││
│  │                    │  Mode:[Manual ▾] 10.0.0.8 : 3128   ││
│  │                    │                                    ││
│  │                    │  ⚠ Cấu hình thực tế đang lệch      ││
│  │                    │    [Xem khác biệt] [Lưu lại] [Khôi phục]│
│  │                    │                                    ││
│  │                    │   [Xem trước]  [Lưu]  [Áp dụng ngay]││
│  └────────────────────┴────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

**Dialog "Xem trước" (dry-run)** — hiển thị diff trước khi áp dụng:

```
┌─ Áp dụng "🏢 Công ty" — sẽ thay đổi ────────────────────┐
│  Wi-Fi           Bật    →  Tắt                          │
│  wlp2s0          WiFi-Nha  →  (ngắt kết nối)            │
│  enp3s0          (chưa nối) →  LAN Công ty              │
│                   IPv4: Automatic → Manual 10.0.5.20/24 │
│                   Route: +3 (10.20.0.0/16, …)           │
│  Proxy (desktop) Tắt    →  10.0.0.8:3128                │
│  Proxy (env)     Tắt    →  10.0.0.8:3128 (cần shell mới)│
│                                                          │
│              [Huỷ]              [Áp dụng]               │
└──────────────────────────────────────────────────────────┘
```

**Dialog tiến trình** hiển thị 9 bước ở mục 3.5.4 với trạng thái từng bước, không dùng spinner vô định. Nút Huỷ kích hoạt rollback.

---

## 6. Triển khai và vận hành

### 6.1 Cấu trúc thư mục đề xuất

```
network_management/
├── docs/
│   ├── phan-tich-ung-dung.md      ← tài liệu này
│   ├── kien-truc.md
│   └── huong-dan-su-dung.md
├── src/netmgr/
│   ├── __main__.py
│   ├── app.py                     # GApplication, single instance
│   ├── domain/
│   │   ├── models.py
│   │   └── validators.py          # thuần Python — test dễ
│   ├── infra/
│   │   ├── nm_facade.py
│   │   ├── proxy/
│   │   │   ├── base.py            # ProxyLayer interface
│   │   │   ├── gsettings_layer.py
│   │   │   ├── envd_layer.py
│   │   │   └── apt_layer.py       # v2
│   │   ├── secrets.py
│   │   └── profile_store.py       # TOML + migrate schema
│   ├── services/
│   │   ├── profile_service.py     # CRUD Bộ cấu hình
│   │   ├── profile_applier.py     # luồng 9 bước + checkpoint rollback
│   │   ├── drift_detector.py
│   │   ├── connection_service.py
│   │   ├── route_service.py
│   │   ├── wifi_service.py
│   │   ├── proxy_service.py
│   │   └── state.py
│   ├── tray/
│   │   ├── indicator.py
│   │   └── menu_builder.py
│   └── ui/
│       ├── main_window.py
│       └── dialogs/
├── tests/
│   ├── unit/                      # validators, models, proxy layers
│   └── integration/               # cần NM thật + dummy interface
├── data/
│   ├── netmgr.desktop
│   ├── netmgr-autostart.desktop
│   ├── icons/
│   └── polkit/                    # v2
├── packaging/debian/
├── pyproject.toml
└── README.md
```

### 6.2 Khởi động cùng hệ thống

- File `~/.config/autostart/netmgr.desktop` với `X-GNOME-Autostart-Delay=3` (chờ NM và Shell sẵn sàng).
- Hoặc systemd user unit `netmgr.service` với `After=graphical-session.target` — sạch hơn, có restart policy, log qua journald. **Khuyến nghị dùng systemd user unit**, autostart .desktop là phương án dự phòng.
- Single instance: dùng `Gio.Application` với flag `HANDLES_COMMAND_LINE`; instance thứ hai chỉ gửi tín hiệu "mở cửa sổ" cho instance đang chạy.

### 6.3 Đóng gói

| Cách | Ưu | Nhược |
|---|---|---|
| **`.deb`** ⭐ | Native, cài `apt install ./netmgr.deb`, khai báo dependency chuẩn | Phải build cho từng release |
| `pipx` | Nhanh cho dev | Không quản lý được dependency hệ thống (`gir1.2-*`) |
| Flatpak | Sandbox | ❌ Không phù hợp — app cần D-Bus system bus, gsettings host, polkit |
| Snap | Ubuntu-native | Interface `network-manager` hạn chế, gsettings khó |

**Quyết định: `.deb`.** Dependencies: `python3 (>= 3.12)`, `python3-gi`, `gir1.2-nm-1.0`, `gir1.2-ayatanaappindicator3-0.1`, `gir1.2-secret-1`, `network-manager`, `gnome-shell-extension-appindicator | gnome-shell-extension-ubuntu-appindicators`.

### 6.4 Kiểm thử

| Loại | Phạm vi | Cách chạy |
|---|---|---|
| Unit | Validators, models, proxy layer logic | `pytest tests/unit` — không cần NM, chạy được trong CI |
| Fake integration | Services với `FakeNMFacade` | `pytest tests/integration -m fake` |
| Applier | `ProfileApplier` với `FakeNMFacade` inject lỗi ở từng bước → kiểm tra rollback đúng | `pytest tests/integration -m applier` — **bắt buộc phủ hết 9 bước** |
| Real integration | NM thật trên dummy interface | Tạo `ip link add dummy0 type dummy` + profile test, dọn dẹp sau khi chạy |
| Manual | Tray, UI, Wayland | Checklist thủ công |

**Test integration an toàn:** tuyệt đối không đụng vào connection thật của máy dev. Tạo profile tên có prefix `__netmgr_test__`, teardown xoá theo prefix. Dùng dummy interface để không làm rớt mạng thật.

**Checklist thủ công bắt buộc:**
- [ ] Tray icon hiện đúng góc phải trên, cả theme sáng và tối
- [ ] Menu cập nhật khi rút/cắm cáp mạng
- [ ] Menu cập nhật khi bật/tắt Wi-Fi từ GNOME Settings (nguồn bên ngoài)
- [ ] Kết nối một mạng Wi-Fi mới bằng menu GNOME → connection xuất hiện ngay trong app
- [ ] Chưa có Wi-Fi connection nào → hiện trạng thái rỗng có hướng dẫn, không phải bảng trắng
- [ ] Suspend → resume: app vẫn hoạt động, không mất tray icon
- [ ] Tắt extension appindicator → app báo lỗi có ích, không crash
- [ ] Thêm route → `ip route show` xác nhận đã áp dụng
- [ ] Đổi Automatic → Manual → Automatic: không mất cấu hình
- [ ] Bật proxy → `gsettings get org.gnome.system.proxy mode` khớp
- [ ] Chuyển Bộ cấu hình: cả mạng lẫn proxy đổi đúng, nhãn tray cập nhật
- [ ] Áp dụng Bộ cấu hình có binding hỏng → rollback sạch, mạng trở về như cũ
- [ ] Kill tiến trình app giữa lúc áp dụng → NM tự rollback sau timeout
- [ ] Sửa cấu hình qua GNOME Settings → app hiện badge lệch
- [ ] Export → xoá Bộ cấu hình → import lại: khôi phục đúng (trừ mật khẩu)
- [ ] Rút cáp rồi áp dụng Bộ cấu hình có `required=false` cho enp3s0 → bỏ qua, không lỗi

---

## 7. Rủi ro

| # | Rủi ro | Mức | Giảm thiểu |
|---|---|---|---|
| R1 | Wayland/GNOME đổi cách xử lý tray ở bản sau | Cao | Trừu tượng hoá tầng tray sau một interface; luôn có đường mở cửa sổ chính không qua tray (`netmgr --show`, `.desktop` trong app grid) |
| R2 | `reapply` thất bại với thay đổi lớn → mất kết nối | Cao | Phát hiện trước loại thay đổi; cảnh báo; dùng **NM checkpoint/rollback** (`CheckpointCreate` + auto-rollback 60s) cho thay đổi rủi ro |
| R3 | Người dùng tự khoá mình khỏi mạng bằng route sai | Cao | Validate mạnh + checkpoint rollback tự động + nút "Khôi phục cấu hình trước" |
| R4 | Proxy nhiều lớp gây nhầm lẫn | Trung bình | UI hiển thị trạng thái từng lớp rõ ràng; có màn hình "Chẩn đoán proxy" liệt kê thực tế từng lớp đang là gì |
| R5 | Xung đột với nm-applet / GNOME Settings | Trung bình | Chỉ đọc/ghi qua NM (nguồn sự thật chung); lắng nghe signal để đồng bộ khi có thay đổi bên ngoài; không cache lâu |
| R6 | Không đủ quyền polkit trên máy quản lý tập trung | Trung bình | Kiểm tra permission lúc khởi động, disable UI tương ứng với thông báo rõ |
| R7 | GTK3 bị gỡ khỏi Ubuntu trong tương lai | Trung bình | Core không phụ thuộc GTK → migrate sang Phương án B khi cần |
| R8 | Rò rỉ bộ nhớ do app chạy 24/7 | Thấp | Ngắt signal handler khi destroy widget; test chạy dài 48h theo dõi RSS |
| R9 | **Áp dụng Bộ cấu hình thất bại giữa chừng → máy mất mạng hoàn toàn** | Cao | NM checkpoint bao trọn luồng áp dụng, timeout 90s tự rollback kể cả khi app chết; thứ tự bước ở 3.5.4 thiết kế để không tự cắt mạng sớm. **Lưu ý:** checkpoint cần xác thực polkit — xem câu hỏi mở 11 |
| R10 | Managed connection do app tạo sinh sôi, làm rối GNOME Settings | Trung bình | Tiền tố tên `netmgr:`; màn hình dọn dẹp liệt kê connection mồ côi; xoá Bộ cấu hình thì xoá kèm |
| R11 | Nhiều connection cùng SSID → NM tự kết nối nhầm biến thể | Trung bình | **Bắt buộc `autoconnect=false`** cho mọi managed connection; app kích hoạt tường minh |
| R14 | Người dùng mong app kết nối được mạng Wi-Fi mới, không tìm thấy chức năng | Trung bình | Nêu rõ trong README và trong trạng thái rỗng của tab Wi-Fi; đặt nút mở thẳng `gnome-control-center wifi`. Nếu phản hồi thực tế cho thấy đây là cản trở lớn thì mở lại phạm vi ở v2 (đã dự trù trong lộ trình) |
| R12 | Bộ cấu hình lệch âm thầm, người dùng tưởng đang ở bối cảnh A nhưng thực tế là B | Trung bình | DriftDetector chạy theo signal của NM; badge ⚠ trên tray và trong danh sách |
| R13 | Tự động chuyển (v2) dao động liên tục khi tín hiệu chập chờn | Trung bình | Hysteresis ≥ 10s, giới hạn số lần chuyển/phút, cho phép ghim thủ công đè lên tự động |

---

## 8. Lộ trình đề xuất

| Giai đoạn | Nội dung | Kết quả bàn giao |
|---|---|---|
| ✅ **P0 — Nền tảng** | Cấu trúc project, `NMFacade` đọc dữ liệu, domain model, validator + unit test | **XONG** — `netmgr dump` in đúng connection/route của máy thật |
| ✅ **P1 — Tray** | SNI tự implement, menu động từ trạng thái thật, icon theo trạng thái, bật/tắt Wi-Fi, connect/disconnect, autostart, single instance | **XONG** — icon đăng ký được với GNOME, 236 test pass |
| ✅ **P2 — Proxy** | `ProxyService` + `GSettingsLayer` + `EnvdLayer`, CRUD cấu hình proxy, toggle từ tray, nhập cấu hình sẵn có, chẩn đoán theo lớp | **XONG** — verify e2e với proxy thật, 382 test pass |
| ✅ **P3 — Connection** | Cửa sổ GTK4/libadwaita: list wired + Wi-Fi đã lưu, trang chi tiết, connect/disconnect, radio Wi-Fi, xoá connection, CRUD proxy, trang chẩn đoán | **XONG** — F2 + F3 đầy đủ, 422 test pass |
| ✅ **P4 — Route + Auto mode** | Route CRUD đầy đủ, validate realtime, nhập/xuất hàng loạt, bật/tắt từng route, chuyển Automatic↔Manual có điền sẵn từ DHCP, luồng ghi 5 bước + reapply/fallback | **XONG** — 21 integration test ghi thật, 445 test tổng. ✅ Chức năng lõi F1–F5 |
| ✅ **P5 — Bộ cấu hình (F6)** | `ProfileStore`, `ProfileApplier` 9 bước + rollback tự chụp, radio group trên tray, trang quản lý, "Lưu trạng thái hiện tại" | **XONG** — 540 test, verify e2e với mạng thật |
| **P6 — Bộ cấu hình nâng cao** | Dry-run diff, DriftDetector, import/export TOML, nhân bản, dọn connection mồ côi | F6 hoàn chỉnh |
| **P7 — Hoàn thiện** | Notification, chẩn đoán proxy, import/export route, đóng gói `.deb`, tài liệu | Bản phát hành 1.0 |
| **v2** | Quét AP + kết nối mạng Wi-Fi mới (cần secret agent), tự động chuyển Bộ cấu hình theo ngữ cảnh, hooks, proxy L3/L4 qua polkit helper, IPv6, VPN, migrate GTK4/libadwaita | — |

**Ghi chú về thay đổi phạm vi Wi-Fi (v1.2):** giai đoạn "Wi-Fi nâng cao" trước đây (scan, dialog mật khẩu, SSID ẩn) đã bị loại bỏ. Phần Wi-Fi còn lại gộp trọn vào **P3** vì nó dùng chung hoàn toàn luồng với wired: cùng là liệt kê connection, activate/deactivate, sửa IPv4/route. Khác biệt duy nhất là toggle radio và vài trường trạng thái chỉ-đọc. Ước tính tiết kiệm được **một giai đoạn phát triển trọn vẹn**, chủ yếu nhờ không phải implement `NM.SecretAgent`.

**Lý do đặt F6 sau P4:** Bộ cấu hình là lớp *điều phối* trên F1–F5. Làm trước khi các nguyên thuỷ (activate, set route, set proxy) đã ổn định thì sẽ phải viết lại. Nhưng cần **thiết kế** `ProfileApplier` từ P0 để các service bên dưới lộ ra API phù hợp (async, có kết quả rõ ràng, huỷ được) — nếu không, tới P5 mới phát hiện `ConnectionService` không hỗ trợ huỷ giữa chừng thì phải sửa ngược.

---

## 9. Tiêu chí nghiệm thu v1.0

**Chức năng**
- [ ] Icon hiển thị thường trú góc phải phía trên trên GNOME/Wayland
- [ ] Bật/tắt proxy trong ≤ 2 click từ tray
- [ ] Chuyển đổi ≥ 2 profile proxy, thay đổi có hiệu lực ngay với Firefox/Chrome
- [ ] Liệt kê, kết nối, ngắt kết nối cả wired lẫn Wi-Fi (connection đã lưu)
- [ ] Bật/tắt Wi-Fi radio từ tray, trạng thái đồng bộ hai chiều với GNOME
- [ ] Xem đủ trạng thái Wi-Fi: SSID, tín hiệu, băng tần, tốc độ, bảo mật, IP, gateway, DNS
- [ ] Sửa được IPv4 và route của một Wi-Fi connection đã lưu
- [ ] App **không** chứa mã nào đọc hoặc lưu mật khẩu Wi-Fi (kiểm tra bằng grep + review)
- [ ] Thêm/sửa/xoá route IPv4, kết quả khớp với `ip route show`
- [ ] Toggle Automatic ↔ Manual, chuyển qua lại không mất dữ liệu
- [ ] Toàn bộ luật validate ở mục 3.3 được thực thi
- [ ] Tạo được ≥ 3 Bộ cấu hình với cấu hình mạng **và** proxy khác nhau
- [ ] Chuyển Bộ cấu hình trong ≤ 2 click từ tray, hoàn tất < 15 giây
- [ ] "Lưu trạng thái hiện tại thành Bộ cấu hình" tạo ra Bộ cấu hình áp dụng lại được đúng
- [ ] Áp dụng thất bại → rollback đưa mạng về đúng trạng thái trước đó
- [ ] Dry-run liệt kê đúng mọi thay đổi sẽ xảy ra
- [ ] Export/import round-trip giữ nguyên cấu hình, không chứa secret
- [ ] Phát hiện và báo lệch khi cấu hình bị sửa từ bên ngoài

**Phi chức năng**
- [ ] RAM idle < 40 MB (tray) — đo bằng `ps -o rss`
- [ ] CPU idle ~0% trong 10 phút không thao tác
- [ ] Khởi động (từ launch tới icon hiện) < 2 giây
- [ ] Không crash sau 48 giờ chạy liên tục, RSS không tăng quá 10%
- [ ] Không có mật khẩu nào xuất hiện trong log hay file config
- [ ] Hoạt động đúng sau suspend/resume và sau khi đổi mạng

---

## 10. Câu hỏi cần chốt trước khi code

1. ~~**UI framework**~~ — ✅ **đã chốt: Phương án D** (GTK4 + tự implement SNI, một tiến trình). Đã implement và verify.
2. **Ngôn ngữ giao diện** — tiếng Việt, tiếng Anh, hay chuẩn bị i18n ngay từ đầu?
3. **Phạm vi proxy v1** — chỉ L1 (gsettings), hay bắt buộc có luôn L2 (environment.d)?
4. **Đối tượng phân phối** — chỉ dùng cá nhân (chạy từ source là đủ), hay cần đóng gói `.deb` phát cho team?
5. **IPv6** — ẩn hoàn toàn ở v1, hay hiển thị read-only?
6. **Checkpoint rollback** — có làm ngay ở P4 không? (Khuyến nghị: có — đây là lưới an toàn quan trọng nhất khi cho phép sửa route, và là điều kiện bắt buộc để F6 an toàn.)
7. **Mô hình lưu trữ của Bộ cấu hình** — chốt Mô hình C (managed connection) như khuyến nghị ở 3.5.1, hay chấp nhận Mô hình B đơn giản hơn nhưng không tạo được hai biến thể route trên cùng một mạng?
8. **Bộ cấu hình có quản lý cả trạng thái radio (bật/tắt Wi-Fi) không?** Nếu có thì cần kịch bản "Offline"; nếu không thì phạm vi F6 gọn hơn đáng kể.
9. **Tự động chuyển theo ngữ cảnh** — để hẳn v2, hay cần ngay ở v1? (Khuyến nghị: v2 — chuyển thủ công đã giải quyết 90% nhu cầu, còn tự động chuyển sai thì gây bực bội hơn là tiện.)
10. **Export dùng để chia sẻ trong team hay chỉ backup cá nhân?** Nếu chia sẻ team thì cần chuẩn hoá schema, versioning và tài liệu định dạng ngay từ đầu.
11. ~~**Checkpoint cần xác thực polkit**~~ — ✅ **đã chốt khi làm P5: hướng B+C kết hợp.** Mặc định **không dùng** NM checkpoint (tránh hộp thoại mật khẩu mỗi lần chuyển bối cảnh); thay vào đó `ProfileApplier` **tự chụp trạng thái** ở bước 2 và khôi phục khi thất bại — không cần quyền gì. Cờ `use_checkpoint` để bật lưới an toàn mạnh hơn cho ai chấp nhận prompt; polkit rule sẽ đóng gói kèm `.deb` ở P7. Đánh đổi đã biết: bản chụp thủ công **không** bảo vệ được trường hợp app bị kill giữa chừng, còn checkpoint thì có.
