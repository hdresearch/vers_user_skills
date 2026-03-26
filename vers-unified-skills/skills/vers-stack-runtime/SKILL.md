---
name: vers-stack-runtime
description: Operate reef runtime services, tests, lint, and health checks.
metadata:
  author: Carter Schonwald
  version: 1
---

# Vers Stack Runtime

Use this skill for day-2 runtime ops on the reef control plane.

## Trigger

- Start reef locally
- Run reef tests/lint
- Check health endpoint

## Commands

```bash
uv run /tmp/vers-unified-skills/scripts/reef_ops.py test
uv run /tmp/vers-unified-skills/scripts/reef_ops.py lint
uv run /tmp/vers-unified-skills/scripts/reef_ops.py start
uv run /tmp/vers-unified-skills/scripts/reef_ops.py health
```

## Notes

- Wrapper runs from `/tmp/reef`.
- `health` defaults to `http://localhost:3000/health`.
