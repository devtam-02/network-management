"""Giao diện chung cho một lớp proxy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...domain.models import OpResult, ProxyConfig, ProxyLayerId


@dataclass(slots=True)
class LayerStatus:
    """Trạng thái thực tế của một lớp — nguồn dữ liệu cho màn hình chẩn đoán.

    Mục đích: trả lời được câu "tôi tắt proxy rồi mà sao `apt` vẫn đi qua proxy?"
    bằng cách cho thấy TỪNG lớp đang là gì, thay vì một công tắc chung mập mờ.
    """

    layer: ProxyLayerId
    name: str
    available: bool
    active: bool
    summary: str
    #: Điều người dùng cần biết thêm, vd "cần mở terminal mới".
    note: str = ""


class ProxyLayer(ABC):
    id: ProxyLayerId
    name: str
    #: True nếu thay đổi có hiệu lực ngay; False nếu cần đăng nhập lại.
    applies_immediately: bool = True

    @abstractmethod
    def is_available(self) -> bool:
        """Lớp này dùng được trên máy hiện tại không."""

    @abstractmethod
    def apply(self, config: ProxyConfig, password: str | None = None) -> OpResult:
        """Ghi cấu hình xuống lớp này."""

    @abstractmethod
    def clear(self) -> OpResult:
        """Tắt proxy ở lớp này."""

    @abstractmethod
    def read(self) -> ProxyConfig | None:
        """Đọc cấu hình đang có. None nếu không đọc được."""

    def status(self) -> LayerStatus:
        if not self.is_available():
            return LayerStatus(
                self.id, self.name, available=False, active=False,
                summary="không khả dụng",
            )
        current = self.read()
        active = bool(current and current.is_enabled)
        return LayerStatus(
            self.id,
            self.name,
            available=True,
            active=active,
            summary=current.summary() if current else "không đọc được",
            note="" if self.applies_immediately else "cần mở terminal/đăng nhập mới",
        )
