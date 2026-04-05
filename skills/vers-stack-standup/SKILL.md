---
name: vers-stack-standup
description: >
  Self-contained standup flow for Reef on Vers: provision from known public root/golden commits,
  build root/golden images from local reef/pi-vers sources, and generate UI magic links.
  Trigger: "stand up reef", "provision fleet", "bootstrap stack", "create golden image", "magic link".
metadata:
  author: Carter Schonwald
  version: 1
  source:
    - https://github.com/hdresearch/vers-fleets
    - https://docs.vers.sh/llms.txt
  updated: 2026-04-03
---

# Vers Stack Standup

This skill uses the local wrapper:

- `scripts/vers_stack.py`

It wraps `vers-fleets` with a self-contained operational surface.

## Fast Path (Public Commits)

Use this when you want a working Reef root fast.

```bash
cd /Users/carter/local_dev/scratch_space/verse_user_skills
uv run scripts/vers_stack.py provision-public --out-dir out --root-name root-reef
```

Known public commits (from `vers-fleets/README.md`):

- root: `5d9c6176-2e9e-4b38-8fc2-f7e0fb3507ce`
- golden: `d2fedfa3-a835-4745-9b50-0e94d347d26b`

Generate login link:

```bash
uv run scripts/vers_stack.py magic-link --deployment out/deployment.json
```

## Build Path (Custom Root/Golden)

Use this when iterating on `reef` / `pi-vers` / `punkin-pi` behavior.

### 1) Build root image

```bash
uv run scripts/vers_stack.py build-root --private --out-dir out
```

### 2) Build golden image

```bash
uv run scripts/vers_stack.py build-golden --private --out-dir out
```

### 3) Provision with explicit commits

```bash
uv run scripts/vers_stack.py provision \
  --root-commit <root-commit-id> \
  --golden-commit <golden-commit-id> \
  --out-dir out
```

### 4) Generate UI link

```bash
uv run scripts/vers_stack.py magic-link --deployment out/deployment.json
```

## Local Source Behavior

By default, the wrapper auto-wires sibling local repos when present:

- `../reef`
- `../pi-vers`

That gives you local-branch bootstrap behavior without extra flags.

Disable with:

```bash
--no-local-sources
```

Or set explicit paths/refs:

```bash
--reef-path /path/to/reef
--pi-vers-path /path/to/pi-vers
--reef-ref feat/reef-v2-orchestration
--pi-vers-ref main
--punkin-ref carter/punkin/v1_rc5
```

## v2 Branches

Current discovered v2 branches:

- `reef`: `origin/feat/reef-v2-orchestration`
- `vers-fleets`: `origin/reef-v2-orchestration`

Use refs directly during build:

```bash
uv run scripts/vers_stack.py build-root --private \
  --reef-ref feat/reef-v2-orchestration \
  --pi-vers-ref main
```

## Core User Prefs on Remote

If you want your real core prefs propagated safely to remote agents:

1. Use local `reef` source (default behavior in this wrapper)
2. Ensure your authoritative prefs are in repo-tracked agent instructions used by reef image build (e.g. `reef/AGENTS.md` and linked files)
3. Build golden from that local source

Rationale: `vers-fleets` image build scripts install/link AGENTS material into child runtime paths; using local source keeps this explicit and auditable.

## Health Checks

```bash
uv run scripts/vers_stack.py doctor
uv run scripts/vers_stack.py test
```

`doctor` reports:

- resolved `vers-fleets` repo
- detected local `reef`/`pi-vers`/`punkin-pi`
- active branches
- tool versions
