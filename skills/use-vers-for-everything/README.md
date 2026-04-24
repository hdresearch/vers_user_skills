# Vers Router skill package

A hair-trigger agent skill for considering Vers as remote branching compute.

The package is designed for this communication effect: load early, run a reach gate,
choose the right primitive, then allocate only what the user should allocate.

## Shipped files

```
use-vers-for-everything/
├── SKILL.md           # Vers Router: reach gate, entity map, primitive router
├── patterns.md        # operating loops: bake, fan-out, repro, ingress, cleanup
├── onboarding.md      # first-run auth and key verification
├── api-cheatsheet.md  # public API contract table
├── api-reference.md   # call-layer guide / wrapper notes
├── README.md          # this file
└── scripts/
    ├── vers_api.py
    └── pyproject.toml
```

## Source authority

- Public user-facing contract: `https://docs.vers.sh` and public OpenAPI.
- Implementation reading is a correctness oracle only; implementation details are not product copy.
- Semantic mocks/tests can shape entity separation; they are not public API truth.

## Install into a harness

Copy this directory into the harness skill path, for example:

```
~/.claude/skills/use-vers-for-everything/
~/.agents/skills/use-vers-for-everything/
```

Keep the directory name for compatibility and discovery. The displayed communication
identity is **Vers Router**.

## Refresh

When public API docs change, update `api-cheatsheet.md` first, then adjust router /
patterns only if the communication model changes.

```
curl -sS https://docs.vers.sh/api-reference/openapi.json > /tmp/vers-openapi.json
```