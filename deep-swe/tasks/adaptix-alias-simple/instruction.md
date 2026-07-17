Add alias support to `name_mapping` so fields can resolve from alternative input keys.

`name_mapping` gains a load-only `aliases` field (field ID to string or strings, first-wins-per-field). Loading resolves from the primary key with ordered alias fallback. Multi-key conflicts raise `ExtraFieldsLoadError`.

Explicit aliases equal to their own primary key error at creation. Cross-field collisions with other primary keys or other aliases also error at creation.

IMPORTANT: Please work on this in a new branch from main and commit everything when you are done.
