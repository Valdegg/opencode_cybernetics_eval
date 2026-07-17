"""Alias integration tests — verify alias support is implemented."""

from dataclasses import dataclass

import pytest

from adaptix import Retort, name_mapping
from adaptix.load_error import ExtraFieldsLoadError


@dataclass
class SimpleModel:
    user_name: str
    age: int


def test_alias_resolution():
    retort = Retort(
        recipe=[name_mapping(map={"user_name": "name", "age": "age"},
                             aliases={"name": "username", "age": "years_old"})]
    )
    data = {"username": "Alice", "years_old": 30}
    result = retort.load(data, SimpleModel)
    assert result.user_name == "Alice"
    assert result.age == 30


def test_primary_key_preferred():
    retort = Retort(
        recipe=[name_mapping(map={"user_name": "name"},
                             aliases={"name": "username"})]
    )
    data = {"user_name": "primary", "username": "alias"}
    result = retort.load(data, SimpleModel)
    assert result.user_name == "primary"


def test_alias_conflict_error():
    with pytest.raises(ExtraFieldsLoadError):
        retort = Retort(
            recipe=[name_mapping(map={"user_name": "name"},
                                 aliases={"name": "user_name"})]
        )
        retort.load({"user_name": "test", "name": "alias"}, SimpleModel)
