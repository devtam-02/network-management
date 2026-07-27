"""Tray — StatusNotifierItem tự implement (§4.1).

GNOME trên Wayland không có XEmbed systray; `Gtk.StatusIcon` không dùng được.
Chuẩn duy nhất hoạt động là StatusNotifierItem (SNI) trên D-Bus, được extension
`ubuntu-appindicators` của GNOME Shell hiện thực phía host.

Ta tự implement SNI thay vì dùng `AyatanaAppIndicator3` vì thư viện đó chỉ liên
kết GTK3, mà GTK3 và GTK4 không load chung một tiến trình được. Tự làm nên app
chạy được GTK4/libadwaita trong MỘT tiến trình duy nhất, và không cần cài thêm
gói hệ thống nào.
"""
