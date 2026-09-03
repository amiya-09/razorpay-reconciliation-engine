import pytest

from reconciliation.loaders.base import blank_to_none, parse_optional_bool, read_rows


def test_read_rows_rejects_unsupported_file_type(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("not csv or json")
    with pytest.raises(ValueError, match="Unsupported file type"):
        list(read_rows(path))


def test_parse_optional_bool_none_and_blank_return_none():
    assert parse_optional_bool(None) is None
    assert parse_optional_bool("") is None


def test_parse_optional_bool_rejects_unparseable_value():
    with pytest.raises(ValueError, match="Cannot parse boolean"):
        parse_optional_bool("maybe")


def test_blank_to_none_passes_through_non_blank_values():
    assert blank_to_none("x") == "x"
    assert blank_to_none(None) is None
