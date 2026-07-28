#!/usr/bin/env bash
#
# netmgr — script kiểm tra môi trường và cài đặt chạy-từ-source.
#
#   ./install.sh              kiểm tra môi trường, không thay đổi gì
#   ./install.sh install      cài lệnh `netmgr` + mục trong menu ứng dụng
#   ./install.sh autostart    BẬT khởi động cùng máy
#   ./install.sh no-autostart TẮT khởi động cùng máy
#   ./install.sh uninstall    gỡ tất cả
#
# Mặc định app KHÔNG tự khởi động: bạn mở từ menu ứng dụng khi cần, tray hiện
# theo. Muốn nó chạy sẵn mỗi lần đăng nhập thì chạy thêm `autostart`.
#
# Script này KHÔNG cần sudo. Việc cài gói hệ thống được in ra để bạn tự chạy —
# script không tự ý gọi apt.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
LAUNCHER="${BIN_DIR}/netmgr"
AUTOSTART_DIR="${HOME}/.config/autostart"
AUTOSTART_FILE="${AUTOSTART_DIR}/netmgr.desktop"
APPS_DIR="${HOME}/.local/share/applications"
APP_FILE="${APPS_DIR}/netmgr.desktop"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
ICON_FILE="${ICON_DIR}/netmgr.svg"
EXTENSION="ubuntu-appindicators@ubuntu.com"

RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; DIM=$'\e[2m'; OFF=$'\e[0m'
ok()   { printf "  %s✔%s %s\n" "$GREEN" "$OFF" "$1"; }
warn() { printf "  %s!%s %s\n" "$YELLOW" "$OFF" "$1"; }
bad()  { printf "  %s✘%s %s\n" "$RED" "$OFF" "$1"; }
note() { printf "    %s%s%s\n" "$DIM" "$1" "$OFF"; }

MISSING=()
FATAL=0

# ── kiểm tra ──────────────────────────────────────────────────────────────────

check_pkg() {           # tên_gói  mô tả  bắt_buộc(1/0)
    if dpkg -s "$1" >/dev/null 2>&1; then
        ok "$2 ($1)"
    elif [[ "$3" == "1" ]]; then
        bad "$2 ($1) — THIẾU"
        MISSING+=("$1"); FATAL=1
    else
        warn "$2 ($1) — chưa cài, không bắt buộc"
        MISSING+=("$1")
    fi
}

do_check() {
    echo "Môi trường"
    local version
    version="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
    if python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null; then
        ok "Python ${version}"
    else
        bad "Python ${version:-?} — cần >= 3.12"; FATAL=1
    fi

    if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
        ok "Desktop: ${XDG_CURRENT_DESKTOP} (${XDG_SESSION_TYPE:-?})"
    else
        warn "Desktop: ${XDG_CURRENT_DESKTOP:-không rõ} — app viết cho GNOME"
        note "Tray dùng chuẩn StatusNotifierItem, KDE/waybar cũng chạy được"
    fi

    echo
    echo "Gói bắt buộc"
    check_pkg python3-gi        "Binding Python cho GObject" 1
    check_pkg gir1.2-nm-1.0     "NetworkManager"             1
    check_pkg gir1.2-gtk-4.0    "GTK 4"                      1
    check_pkg gir1.2-adw-1      "libadwaita"                 1
    check_pkg gir1.2-secret-1   "GNOME Keyring"              1
    check_pkg network-manager   "NetworkManager (dịch vụ)"   1

    echo
    echo "Gói tuỳ chọn"
    check_pkg wl-clipboard "Chép lệnh export từ menu tray" 0
    note "Thiếu gói này thì nút 'Chép' trong cửa sổ vẫn chạy, chỉ nút trên tray là không"

    echo
    echo "Tray icon"
    if [[ ! -d "/usr/share/gnome-shell/extensions/${EXTENSION}" ]]; then
        bad "Chưa có extension ${EXTENSION}"
        note "sudo apt install gnome-shell-ubuntu-extensions"
        FATAL=1
    elif gnome-extensions list --enabled 2>/dev/null | grep -qx "$EXTENSION"; then
        ok "Extension appindicator đang bật"
    else
        warn "Extension appindicator đã cài nhưng CHƯA BẬT"
        note "gnome-extensions enable ${EXTENSION}"
    fi

    echo
    echo "NetworkManager"
    if systemctl is-active --quiet NetworkManager; then
        ok "Dịch vụ đang chạy ($(nmcli --version 2>/dev/null | grep -o '[0-9.]*$'))"
    else
        bad "Dịch vụ NetworkManager không chạy"; FATAL=1
    fi

    echo
    echo "Đã cài chưa"
    [[ -x "$LAUNCHER" ]] && ok "Lệnh: netmgr"        || note "Chưa cài lệnh netmgr"
    [[ -f "$APP_FILE" ]] && ok "Có trong menu ứng dụng" \
                         || note "Chưa có trong menu ứng dụng"
    [[ -f "$AUTOSTART_FILE" ]] && ok "Khởi động cùng máy: BẬT" \
                               || note "Khởi động cùng máy: tắt (mặc định)"

    echo
    if (( FATAL )); then
        bad "Còn thiếu gói bắt buộc. Chạy lệnh sau rồi kiểm tra lại:"
        printf "\n      sudo apt install %s\n\n" "${MISSING[*]}"
        return 1
    fi
    if (( ${#MISSING[@]} )); then
        warn "Có thể cài thêm (không bắt buộc):"
        printf "\n      sudo apt install %s\n\n" "${MISSING[*]}"
    fi
    ok "Môi trường sẵn sàng."
    echo
    echo "  Chạy thử: PYTHONPATH=${ROOT}/src python3 -m netmgr window"
    echo "  Cài đặt:  ${BASH_SOURCE[0]} install"
    return 0
}

# ── cài ───────────────────────────────────────────────────────────────────────

write_icon() {
    mkdir -p "$ICON_DIR"
    cat > "$ICON_FILE" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="29" fill="#3584e4"/>
  <g fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round">
    <path d="M14 40a26 26 0 0 1 36 0"/>
    <path d="M21 47a17 17 0 0 1 22 0"/>
  </g>
  <circle cx="32" cy="53" r="4.2" fill="#fff"/>
</svg>
EOF
}

do_install() {
    do_check || { echo "Dừng lại vì môi trường chưa đủ."; return 1; }

    mkdir -p "$BIN_DIR" "$APPS_DIR"

    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Sinh bởi install.sh — chạy netmgr thẳng từ source tại ${ROOT}
exec env PYTHONPATH="${ROOT}/src" python3 -m netmgr "\$@"
EOF
    chmod +x "$LAUNCHER"
    ok "Đã tạo lệnh netmgr"

    write_icon
    ok "Đã cài biểu tượng"

    # Mở bằng `window`: bấm vào app trong menu thì phải thấy cửa sổ hiện ra.
    # Tray tự xuất hiện kèm theo vì cùng một tiến trình.
    cat > "$APP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Quản lý mạng
Name[en]=Network Manager
Comment=Quản lý proxy, kết nối, route IPv4 và Bộ cấu hình
Comment[en]=Manage proxy, connections, IPv4 routes and profiles
Exec=${LAUNCHER} window
Icon=netmgr
Terminal=false
Categories=Network;
Keywords=mạng;proxy;wifi;route;network;
StartupNotify=true
StartupWMClass=io.github.netmgr
EOF
    ok "Đã thêm vào menu ứng dụng"

    update-desktop-database "$APPS_DIR" >/dev/null 2>&1
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1

    if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
        warn "${BIN_DIR} không nằm trong PATH"
        note 'Thêm vào ~/.bashrc: export PATH="$HOME/.local/bin:$PATH"'
    fi

    echo
    ok "Xong."
    echo "  Mở từ menu ứng dụng:  tìm \"Quản lý mạng\""
    echo "  Hoặc từ terminal:     netmgr"
    echo
    if [[ -f "$AUTOSTART_FILE" ]]; then
        note "Khởi động cùng máy đang BẬT — tắt bằng: ${BASH_SOURCE[0]} no-autostart"
    else
        note "App KHÔNG tự chạy khi đăng nhập. Muốn bật:"
        note "    ${BASH_SOURCE[0]} autostart"
    fi
}

# ── khởi động cùng máy ────────────────────────────────────────────────────────

do_autostart() {
    if [[ ! -x "$LAUNCHER" ]]; then
        bad "Chưa cài. Chạy `${BASH_SOURCE[0]} install` trước."
        return 1
    fi
    mkdir -p "$AUTOSTART_DIR"
    # Autostart dùng `tray` chứ không phải `window`: chạy nền lúc đăng nhập,
    # không bật cửa sổ vào mặt người dùng.
    cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Quản lý mạng
Comment=Chạy nền ở khay hệ thống
Exec=${LAUNCHER} tray
Icon=netmgr
Terminal=false
Categories=Network;
X-GNOME-Autostart-enabled=true
# Chờ NetworkManager và extension appindicator sẵn sàng; thiếu độ trễ này thì
# icon vẫn hiện (app tự đăng ký lại) nhưng sẽ nhấp nháy lúc đăng nhập.
X-GNOME-Autostart-Delay=3
EOF
    ok "Đã BẬT khởi động cùng máy"
    note "Tắt bằng: ${BASH_SOURCE[0]} no-autostart"
}

do_no_autostart() {
    if [[ -f "$AUTOSTART_FILE" ]]; then
        rm -f "$AUTOSTART_FILE"
        ok "Đã TẮT khởi động cùng máy"
    else
        note "Khởi động cùng máy vốn đã tắt"
    fi
}

# ── gỡ ────────────────────────────────────────────────────────────────────────

do_uninstall() {
    local removed=0
    for f in "$LAUNCHER" "$AUTOSTART_FILE" "$APP_FILE" "$ICON_FILE"; do
        [[ -e "$f" ]] && rm -f "$f" && ok "Đã xoá $f" && removed=1
    done
    # Bản cũ có tạo systemd user unit; dọn nốt nếu còn.
    local legacy="${HOME}/.config/systemd/user/netmgr.service"
    if [[ -e "$legacy" ]]; then
        systemctl --user disable --now netmgr 2>/dev/null
        rm -f "$legacy" && ok "Đã xoá $legacy" && removed=1
    fi
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1
    (( removed )) || note "Không có gì để gỡ"
    echo
    note "Cấu hình của bạn KHÔNG bị xoá: ~/.config/netmgr/"
    note "Xoá luôn thì: rm -rf ~/.config/netmgr"
    note "File proxy môi trường: ~/.config/environment.d/10-netmgr-proxy.conf"
}

case "${1:-check}" in
    check)        do_check ;;
    install)      do_install ;;
    autostart)    do_autostart ;;
    no-autostart) do_no_autostart ;;
    uninstall)    do_uninstall ;;
    *) echo "Dùng: $0 [check|install|autostart|no-autostart|uninstall]"; exit 2 ;;
esac
