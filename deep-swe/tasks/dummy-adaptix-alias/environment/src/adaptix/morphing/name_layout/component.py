from .base import MappingConfig
from .mapping import NameMapping


def name_mapping(
    map: dict[str, str] | None = None,
    name_style: str | None = None,
) -> MappingConfig:
    return MappingConfig(
        map_def=map or {},
        name_style=name_style,
    )


class Retort:
    def __init__(self, mapping: NameMapping):
        self._mapping = mapping

    @classmethod
    def from_config(cls, config: MappingConfig) -> "Retort":
        return cls(config.build_mapping())

    def load(self, data: dict) -> dict:
        return self._mapping.resolve(data)
