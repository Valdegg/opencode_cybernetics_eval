import pytest
from adaptix.morphing.name_layout import NameMapping


def test_name_mapping_exists():
    assert NameMapping is not None


def test_simple_rename():
    nm = NameMapping()
    nm.add_map("user_name", "name")
    assert nm.resolve({"user_name": "Alice"}) == {"name": "Alice"}


def test_multiple_mappings():
    nm = NameMapping()
    nm.add_map("a", "x")
    nm.add_map("b", "y")
    assert nm.resolve({"a": 1, "b": 2}) == {"x": 1, "y": 2}


def test_unmapped_keys_preserved():
    nm = NameMapping()
    nm.add_map("a", "x")
    assert nm.resolve({"a": 1, "other": 2}) == {"x": 1, "other": 2}


def test_empty_input():
    nm = NameMapping()
    assert nm.resolve({}) == {}


def test_empty_mapping():
    nm = NameMapping()
    assert nm.resolve({"a": 1}) == {"a": 1}


def test_add_map_overwrite():
    nm = NameMapping()
    nm.add_map("a", "x")
    nm.add_map("a", "y")
    assert nm.resolve({"a": 1}) == {"y": 1}
