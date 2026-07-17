from .errors import AliasConflictError


class NameMapping:
    def __init__(self):
        self._map = {}

    def add_map(self, source: str, target: str):
        self._map[source] = target

    def resolve(self, data: dict) -> dict:
        result = {}
        for key, value in data.items():
            result[self._map.get(key, key)] = value
        return result
