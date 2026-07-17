Implement alias support for the `NameMapping` class so that fields can be
resolved from multiple input key names.

Package: `src/adaptix/morphing/name_layout/`

Files to edit:
  - `mapping.py`  — `NameMapping` class
  - `component.py` — `name_mapping()` factory, `Retort` class
  - `base.py`      — `MappingConfig` dataclass

Already exists:
  - `errors.py`    — `AliasConflictError` exception

Requirements:

1. `NameMapping.add_alias(field_id, alias)` registers an alias that points
   to an existing mapped field. `field_id` must be the target of a previous
   `add_map()` call — if not, raise `AliasConflictError`. The `alias` name
   must not match any existing source key in the mapping — if it does, raise
   `AliasConflictError`.

2. `NameMapping.resolve(data)` must handle both primary keys (from `add_map`)
   and alias keys:
   - If a key is a primary source key, resolve it to its mapped target name.
   - If a key is an alias, resolve it to the canonical field name.
   - If a key is neither, pass it through unchanged.
   - **Primary keys take precedence over aliases**: when both a primary key
     and an alias would resolve to the same field, use the primary key's
     value and ignore the alias's value.

3. `name_mapping()` in `component.py` must accept an `aliases` parameter
   of type `dict[str, str | list[str]]` where each key is a field name
   and the value is either a single alias string or a list of alias strings.

4. `MappingConfig` in `base.py` must have an `aliases` field and its
   `build_mapping()` method must register all aliases onto the `NameMapping`.

5. The `Retort.from_config()` + `Retort.load()` pipeline must work with
   aliases end-to-end.

IMPORTANT: Work in /app and commit all changes when done.
