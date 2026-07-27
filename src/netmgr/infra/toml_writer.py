"""Ghi TOML tối giản — chỉ đủ cho schema config của app.

Python có `tomllib` để ĐỌC nhưng không có bộ ghi trong stdlib. Dự án chủ trương
không phụ thuộc gói pip nào (mọi thứ đến từ apt), nên viết bộ ghi nhỏ ở đây thay
vì kéo thêm `tomli-w`.

Chỉ hỗ trợ các kiểu app thực sự dùng: str, bool, int, float, list[str] và dict
lồng nhau (thành table). Gặp kiểu khác thì ném lỗi ngay chứ không ghi ra thứ
không parse lại được.

Lý do dùng TOML thay vì JSON: file config và file export Bộ cấu hình (FR-PR6)
phải đọc được bằng mắt và review được trong Git.
"""

from __future__ import annotations

from typing import Any

#: Ký tự phải escape trong chuỗi TOML basic (bảng ở đặc tả TOML 1.0).
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class TomlError(ValueError):
    """Dữ liệu không biểu diễn được bằng tập TOML mà module này hỗ trợ."""


def escape_string(value: str) -> str:
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            # Ký tự điều khiển khác phải dùng dạng \uXXXX.
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def format_value(value: Any) -> str:
    # bool phải kiểm TRƯỚC int: trong Python bool là con của int, nếu không thì
    # True sẽ bị ghi thành 1.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return escape_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TomlError(f"TOML không biểu diễn được {value!r}")
        return repr(value)
    if isinstance(value, (list, tuple)):
        if any(isinstance(v, dict) for v in value):
            # List chứa dict phải ghi thành [[array of table]], không phải mảng
            # inline — `_dump_table` lo việc đó, không gọi vào đây.
            raise TomlError("List chứa dict phải được ghi dạng array of tables")
        return "[" + ", ".join(format_value(v) for v in value) + "]"
    raise TomlError(f"Không hỗ trợ kiểu {type(value).__name__}")


def _is_table_array(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) > 0
        and all(isinstance(v, dict) for v in value)
    )


def _is_bare_key(key: str) -> bool:
    return bool(key) and all(c.isalnum() or c in "-_" for c in key)


def format_key(key: str) -> str:
    return key if _is_bare_key(key) else escape_string(key)


def dumps(data: dict[str, Any], *, header: str = "") -> str:
    """Serialize dict thành TOML.

    Giá trị vô hướng được ghi trước, table lồng ghi sau — nếu ghi table trước thì
    các key vô hướng đứng sau sẽ bị hiểu là thuộc table đó.
    """
    lines: list[str] = []
    if header:
        lines.extend(f"# {line}" for line in header.strip().splitlines())
        lines.append("")
    _dump_table(data, [], lines)
    return "\n".join(lines).rstrip() + "\n"


def _dump_table(data: dict[str, Any], path: list[str], lines: list[str]) -> None:
    scalars, tables, table_arrays = {}, {}, {}
    for key, value in data.items():
        if isinstance(value, dict):
            tables[key] = value
        elif _is_table_array(value):
            table_arrays[key] = value
        else:
            scalars[key] = value

    for key, value in scalars.items():
        if value is None:
            continue                      # None = không đặt; bỏ hẳn key
        lines.append(f"{format_key(key)} = {format_value(value)}")

    for key, value in tables.items():
        _open_table(f"[{_path_str([*path, key])}]", lines)
        _dump_table(value, [*path, key], lines)

    for key, rows in table_arrays.items():
        for row in rows:
            _open_table(f"[[{_path_str([*path, key])}]]", lines)
            _dump_table(row, [*path, key], lines)


def _path_str(path: list[str]) -> str:
    return ".".join(format_key(p) for p in path)


def _open_table(heading: str, lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(heading)


def dump_array_of_tables(
    name: str, rows: list[dict[str, Any]], *, header: str = ""
) -> str:
    """Ghi riêng một `[[name]]` lặp lại.

    `dumps()` đã xử lý được array of tables khi nó nằm trong document, nên hàm
    này chỉ là lối tắt cho trường hợp file chỉ có đúng một danh sách.
    """
    return dumps({name: rows} if rows else {}, header=header)
