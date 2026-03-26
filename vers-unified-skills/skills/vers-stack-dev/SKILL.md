---
name: vers-stack-dev
description: Build and inspect pi-vers extension layer and bundled docs/skills.
metadata:
  author: Carter Schonwald
  version: 1
---

# Vers Stack Dev

Use this skill for pi-vers extension-layer development.

## Trigger

- Build pi-vers
- Inspect docs/skills inventory
- Run lightweight diagnostics

## Commands

```bash
uv run /tmp/vers-unified-skills/scripts/pi_vers_ops.py build
uv run /tmp/vers-unified-skills/scripts/pi_vers_ops.py list-skills
uv run /tmp/vers-unified-skills/scripts/pi_vers_ops.py list-docs
```

## Notes

- Wrapper runs from `/tmp/pi-vers`.
- Helps keep extension-layer work separate from reef control-plane work.
