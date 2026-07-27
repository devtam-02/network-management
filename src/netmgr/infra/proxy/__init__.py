"""Các lớp proxy độc lập (§3.1).

"Proxy" trên Linux không phải một thứ duy nhất mà là nhiều lớp không liên quan
tới nhau. Mỗi lớp là một `ProxyLayer`; `ProxyService` điều phối chúng.

`GSettingsLayer` được nạp muộn: nó cần `gi`, mà `EnvdLayer` và `base` thì không.
Nạp sớm sẽ khiến unit test của lớp environment.d cũng đòi typelib GTK.
"""

from .base import LayerStatus, ProxyLayer
from .envd_layer import EnvdLayer

__all__ = ["EnvdLayer", "GSettingsLayer", "LayerStatus", "ProxyLayer"]


def __getattr__(name: str):
    if name == "GSettingsLayer":
        from .gsettings_layer import GSettingsLayer

        return GSettingsLayer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
