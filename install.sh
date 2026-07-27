#!/usr/bin/env bash
#
# netmgr — script kiểm tra môi trường và cài đặt chạy-từ-source.
#
#   ./install.sh              kiểm tra môi trường, không thay đổi gì
#   ./install.sh install      cài autostart + lệnh `netmgr` vào ~/.local/bin
#   ./install.sh uninstall    gỡ những thứ trên
#
# Script này KHÔNG cần sudo. Việc cài gói hệ thống được in ra để bạn tự chạy —
# script không tự ý gọi apt.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
LAUNCHER="${BIN_DIR}/netmgr"
AUTOSTART_DIR="${HOME}/.config/autostart"
AUTOSTART_FILE="${AUTOSTART_DIR}/netmgr.desktop"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/netmgr.service"
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
    [[ -x "$LAUNCHER" ]]       && ok "Lệnh: $LAUNCHER"       || note "Chưa cài lệnh netmgr"
    [[ -f "$AUTOSTART_FILE" ]] && ok "Autostart: $AUTOSTART_FILE" \
                               || note "Chưa bật khởi động cùng hệ thống"

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
    echo "  Chạy thử:      PYTHONPATH=${ROOT}/src python3 -m netmgr window"
    echo "  Cài thường trú: ${BASH_SOURCE[0]} install"
    return 0
}

# ── cài ───────────────────────────────────────────────────────────────────────

do_install() {
    do_check || { echo "Dừng lại vì môi trường chưa đủ."; return 1; }

    mkdir -p "$BIN_DIR" "$AUTOSTART_DIR" "$UNIT_DIR"

    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Sinh bởi install.sh — chạy netmgr thẳng từ source tại ${ROOT}
exec env PYTHONPATH="${ROOT}/src" python3 -m netmgr "\$@"
EOF
    chmod +x "$LAUNCHER"
    ok "Đã tạo $LAUNCHER"

    cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Quản lý mạng
Comment=Quản lý proxy, kết nối và route IPv4 từ khay hệ thống
Exec=${LAUNCHER} tray
Icon=network-wired-symbolic
Terminal=false
Categories=Network;
X-GNOME-Autostart-enabled=true
# Chờ NetworkManager và extension appindicator sẵn sàng; thiếu độ trễ này thì
# icon vẫn hiện (app tự đăng ký lại) nhưng sẽ nhấp nháy lúc đăng nhập.
X-GNOME-Autostart-Delay=3
EOF
    ok "Đã bật khởi động cùng hệ thống"

    cat > "$UNIT_FILE" <<EOF
[Unit]
Description=netmgr — quản lý mạng & proxy
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=exec
ExecStart=${LAUNCHER} tray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
    ok "Đã tạo systemd user unit (chưa bật)"
    note "Dùng systemd thay cho autostart thì: systemctl --user enable --now netmgr"
    note "Khi đó nhớ xoá ${AUTOSTART_FILE} để khỏi chạy hai lần"

    if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
        warn "${BIN_DIR} không nằm trong PATH"
        note 'Thêm vào ~/.bashrc: export PATH="$HOME/.local/bin:$PATH"'
    fi

    echo
    ok "Xong. Chạy ngay: netmgr tray &"
}

# ── gỡ ────────────────────────────────────────────────────────────────────────

do_uninstall() {
    systemctl --user disable --now netmgr 2>/dev/null
    local removed=0
    for f in "$LAUNCHER" "$AUTOSTART_FILE" "$UNIT_FILE"; do
        [[ -e "$f" ]] && rm -f "$f" && ok "Đã xoá $f" && removed=1
    done
    (( removed )) || note "Không có gì để gỡ"
    echo
    note "Cấu hình của bạn KHÔNG bị xoá: ~/.config/netmgr/"
    note "Xoá luôn thì: rm -rf ~/.config/netmgr"
    note "File proxy môi trường: ~/.config/environment.d/10-netmgr-proxy.conf"
}

case "${1:-check}" in
    check)     do_check ;;
    install)   do_install ;;
    uninstall) do_uninstall ;;
    *) echo "Dùng: $0 [check|install|uninstall]"; exit 2 ;;
esac
