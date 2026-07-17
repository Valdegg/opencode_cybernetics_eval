from dataclasses import dataclass, field
from typing import Optional
from .mapping import NameMapping


@dataclass
class MappingConfig:
    map_def: dict[str, str] = field(default_factory=dict)
    name_style: Optional[str] = None

    def build_mapping(self) -> NameMapping:
        nm = NameMapping()
        for source, target in self.map_def.items():
            nm.add_map(source, target)
        return nm
