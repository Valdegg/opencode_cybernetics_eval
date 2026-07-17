"""Minimal regression tests — verify basic name_mapping still works."""

from adaptix import name_mapping


def test_name_mapping_exists():
    nm = name_mapping()
    assert nm is not None


def test_simple_rename():
    nm = name_mapping(map={"old_name": "new_name"})


def test_unmapped_keys_preserved():
    nm = name_mapping(map={"a": "x"})
