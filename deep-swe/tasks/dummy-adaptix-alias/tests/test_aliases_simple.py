import pytest
from adaptix.morphing.name_layout import NameMapping
from adaptix.morphing.name_layout.errors import AliasConflictError
from adaptix.morphing.name_layout.component import name_mapping, Retort


def test_alias_resolution():
    nm = NameMapping()
    nm.add_map("primary", "field")
    nm.add_alias("field", "alt_name")
    assert nm.resolve({"alt_name": 1}) == {"field": 1}


def test_primary_key_preferred():
    nm = NameMapping()
    nm.add_map("primary", "field")
    nm.add_alias("field", "alt_name")
    assert nm.resolve({"primary": 1, "alt_name": 2}) == {"field": 1}


def test_primary_absent_alias_used():
    nm = NameMapping()
    nm.add_map("user_name", "name")
    nm.add_alias("name", "username")
    nm.add_alias("name", "nick")
    assert nm.resolve({"username": "Alice"}) == {"name": "Alice"}
    assert nm.resolve({"nick": "Bob"}) == {"name": "Bob"}


def test_alias_conflict_unknown_field():
    nm = NameMapping()
    nm.add_map("a", "x")
    with pytest.raises(AliasConflictError):
        nm.add_alias("nonexistent", "alt")


def test_alias_conflict_source_key():
    nm = NameMapping()
    nm.add_map("first_name", "name")
    with pytest.raises(AliasConflictError):
        nm.add_alias("name", "first_name")


def test_multi_alias():
    nm = NameMapping()
    nm.add_map("primary", "field")
    nm.add_alias("field", "alt1")
    nm.add_alias("field", "alt2")
    assert nm.resolve({"alt2": 1}) == {"field": 1}


def test_alias_through_retort():
    config = name_mapping(
        map={"user_name": "name", "user_age": "age"},
        aliases={"name": "username", "age": "years_old"},
    )
    retort = Retort.from_config(config)
    result = retort.load({"username": "Alice", "years_old": 30})
    assert result == {"name": "Alice", "age": 30}


def test_mixed_input():
    nm = NameMapping()
    nm.add_map("a", "x")
    nm.add_map("b", "y")
    nm.add_alias("y", "bee")
    result = nm.resolve({"a": 10, "bee": 20})
    assert result == {"x": 10, "y": 20}


def test_alias_passthrough_unmapped():
    nm = NameMapping()
    nm.add_map("a", "x")
    nm.add_alias("x", "alt_a")
    result = nm.resolve({"alt_a": 1, "unrelated": 99})
    assert result == {"x": 1, "unrelated": 99}
