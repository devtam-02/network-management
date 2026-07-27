"""Test bộ ghi TOML — mọi thứ ghi ra phải parse lại được bằng `tomllib`."""

from __future__ import annotations

import tomllib

import pytest

from netmgr.infra.toml_writer import (
    TomlError,
    dump_array_of_tables,
    dumps,
    escape_string,
    format_value,
)


def roundtrip(data: dict) -> dict:
    return tomllib.loads(dumps(data))


# ── kiểu vô hướng ────────────────────────────────────────────────────────────


def test_bool_not_written_as_int():
    """bool là con của int trong Python — kiểm sai thứ tự thì True thành 1."""
    assert format_value(True) == "true"
    assert format_value(False) == "false"


def test_int_and_float():
    assert format_value(3128) == "3128"
    assert format_value(1.5) == "1.5"


def test_list_of_strings():
    assert format_value(["a", "b"]) == '["a", "b"]'


def test_empty_list():
    assert format_value([]) == "[]"


def test_unsupported_type_raises():
    with pytest.raises(TomlError):
        format_value({1, 2})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_float_raises(value):
    with pytest.raises(TomlError):
        format_value(value)


# ── escape chuỗi ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("đơn giản", '"đơn giản"'),
        ('có "nháy"', '"có \\"nháy\\""'),
        ("gạch\\chéo", '"gạch\\\\chéo"'),
        ("dòng\nmới", '"dòng\\nmới"'),
        ("tab\tở đây", '"tab\\tở đây"'),
    ],
)
def test_escape_string(raw, expected):
    assert escape_string(raw) == expected


def test_control_characters_use_unicode_escape():
    assert escape_string("\x01") == '"\\u0001"'


def test_escaped_strings_roundtrip():
    data = {"k": 'nháy " gạch \\ dòng \n tab \t'}
    assert roundtrip(data) == data


# ── key ──────────────────────────────────────────────────────────────────────


def test_bare_key_unquoted():
    assert "pac_url = " in dumps({"pac_url": "http://x"})


def test_key_with_dot_is_quoted():
    data = {"a.b": "x"}
    assert roundtrip(data) == data


def test_key_with_space_is_quoted():
    data = {"có dấu cách": "x"}
    assert roundtrip(data) == data


# ── table ────────────────────────────────────────────────────────────────────


def test_nested_table_roundtrip():
    data = {"mode": "manual", "http": {"host": "10.0.0.8", "port": 3128}}
    assert roundtrip(data) == data


def test_scalars_written_before_tables():
    """Key vô hướng đứng sau một [table] sẽ bị hiểu là thuộc table đó."""
    out = dumps({"http": {"host": "x"}, "mode": "manual"})
    assert out.index("mode =") < out.index("[http]")


def test_deeply_nested_tables():
    data = {"a": {"b": {"c": {"d": "sâu"}}}}
    assert roundtrip(data) == data


def test_none_values_are_omitted():
    out = dumps({"host": "x", "port": None})
    assert "port" not in out
    assert tomllib.loads(out) == {"host": "x"}


def test_header_becomes_comment():
    out = dumps({"a": 1}, header="Đừng sửa tay")
    assert out.startswith("# Đừng sửa tay")
    assert tomllib.loads(out) == {"a": 1}


def test_multiline_header():
    out = dumps({"a": 1}, header="dòng 1\ndòng 2")
    assert "# dòng 1" in out and "# dòng 2" in out


# ── array of tables ──────────────────────────────────────────────────────────


def test_array_of_tables_roundtrip():
    rows = [
        {"id": "1", "name": "Công ty", "port": 3128},
        {"id": "2", "name": "Nhà", "port": 8080},
    ]
    parsed = tomllib.loads(dump_array_of_tables("proxy", rows))
    assert parsed["proxy"] == rows


def test_array_of_tables_with_nested_table():
    rows = [{"id": "1", "http": {"host": "10.0.0.8", "port": 3128}}]
    parsed = tomllib.loads(dump_array_of_tables("proxy", rows))
    assert parsed["proxy"][0]["http"]["host"] == "10.0.0.8"


def test_empty_array_of_tables():
    assert tomllib.loads(dump_array_of_tables("proxy", [])) == {}


# ── dữ liệu thật của app ─────────────────────────────────────────────────────


def test_realistic_proxy_config_roundtrip():
    data = {
        "schema_version": 1,
        "proxy": [
            {
                "id": "abc-123",
                "name": 'Proxy "Công ty"',
                "mode": "manual",
                "use_same_for_all": True,
                "ignore_hosts": ["localhost", "127.0.0.0/8", "*.viettel.vn"],
                "auth_enabled": False,
                "http": {"host": "10.0.0.8", "port": 3128},
            }
        ],
    }
    assert tomllib.loads(dumps(data)) == data
