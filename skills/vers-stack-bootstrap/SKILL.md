---
name: vers-stack-bootstrap
description: Bootstrap Vers root + golden image + provision flow using wrappers around vers-fleets.
metadata:
  author: Carter Schonwald
  version: 1
---

# Vers Stack Bootstrap

Use this skill to bootstrap the stack from scratch.

## Trigger

- Build root image
- Build golden image
- Provision fleet from commit IDs

## Commands

```bash
# sanity
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py test

# build root image
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py build-root -- --private

# build golden image
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py build-golden -- --private

# provision
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py provision -- --root-commit <ROOT_ID> --golden-commit <GOLDEN_ID>
```

## Notes

- Wrapper runs `bun src/cli.js` inside `/tmp/vers-fleets`.
- Extra args after `--` are passed through unchanged.
