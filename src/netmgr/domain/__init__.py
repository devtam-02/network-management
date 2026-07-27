"""Tầng domain — thuần Python, không phụ thuộc GTK/libnm.

Ràng buộc kiến trúc (§4.3 tài liệu phân tích): module trong gói này KHÔNG được
`import gi`. Nhờ vậy validator và model unit-test được mà không cần D-Bus, và
việc đổi UI framework không đụng tới tầng này.
"""
