# vers-unified-skills

Consolidated skills + uv-runnable scripts distilled from:

- `/tmp/vers-fleets`
- `/tmp/reef`
- `/tmp/pi-vers`

## Layout

- `skills/vers-stack-bootstrap/SKILL.md`
- `skills/vers-stack-runtime/SKILL.md`
- `skills/vers-stack-dev/SKILL.md`
- `scripts/vers_fleet.py`
- `scripts/reef_ops.py`
- `scripts/pi_vers_ops.py`

## Usage

All scripts are uv-runnable single-file scripts.

```bash
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py --help
uv run /tmp/vers-unified-skills/scripts/reef_ops.py --help
uv run /tmp/vers-unified-skills/scripts/pi_vers_ops.py --help
```

## Quickstart

```bash
# 1) build/test repos
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py test
uv run /tmp/vers-unified-skills/scripts/reef_ops.py test
uv run /tmp/vers-unified-skills/scripts/pi_vers_ops.py build

# 2) build images
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py build-root -- --private
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py build-golden -- --private

# 3) provision from commits
uv run /tmp/vers-unified-skills/scripts/vers_fleet.py provision -- --root-commit <ROOT_ID> --golden-commit <GOLDEN_ID>
```
