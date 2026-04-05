# vers-unified-skills

Skills and uv-runnable scripts distilled from [vers-fleets](https://github.com/hdresearch/vers-fleets), [reef](https://github.com/hdresearch/reef), and [pi-vers](https://github.com/hdresearch/pi-vers).

No TypeScript. No pi plugin required. Just `uv run`.

---

## Scripts

All scripts are zero-dependency Python, runnable with `uv run` directly.

### `lt.py` — lieutenant lifecycle manager

The main one. Manages persistent agent sessions on Vers VMs over SSH. No pi plugin needed — it speaks the same FIFO/tmux protocol directly.

State lives at `~/.vers/lieutenants.json` (override with `VERS_LT_STATE`).

```bash
# create a lieutenant from a golden image commit
uv run scripts/lt.py lt-create infra "manage infrastructure" <commit_id>

# send a task
uv run scripts/lt.py lt-send infra "set up the postgres schema"

# steer (interrupt mid-task)
uv run scripts/lt.py lt-send infra "use raw SQL not Prisma" --mode steer

# queue next task
uv run scripts/lt.py lt-send infra "add the auth routes next" --mode followUp

# read last output
uv run scripts/lt.py lt-read infra
uv run scripts/lt.py lt-read infra --follow       # live stream, auto-reconnects
uv run scripts/lt.py lt-read infra --tail 50

# check what's running
uv run scripts/lt.py lt-status
uv run scripts/lt.py lt-status --probe            # SSH in to verify actual tmux state

# pause/resume (freezes VM, full state preserved, no compute cost while paused)
uv run scripts/lt.py lt-pause infra
uv run scripts/lt.py lt-resume infra

# destroy one or all
uv run scripts/lt.py lt-destroy infra
uv run scripts/lt.py lt-destroy "*"

# rediscover from registry after session restart
uv run scripts/lt.py lt-discover
```

**Env vars needed:**
- `VERS_API_KEY` — always
- `VERS_INFRA_URL` + `VERS_AUTH_TOKEN` — for `lt-discover` only

---

### `vers_api.py` — direct REST calls to `api.vers.sh`

Thin wrapper over the documented Vers REST API. Good for scripting fleet operations.

```bash
# list VMs
uv run scripts/vers_api.py vms

# create a VM
uv run scripts/vers_api.py vm-new --mem 4096 --disk 8192 --wait-boot

# restore from commit
uv run scripts/vers_api.py vm-from-commit <commit_id>

# snapshot
uv run scripts/vers_api.py vm-commit <vm_id>

# branch from commit or VM
uv run scripts/vers_api.py vm-branch --commit-id <id>
uv run scripts/vers_api.py vm-branch --vm-id <id>

# pause / resume
uv run scripts/vers_api.py vm-state <vm_id> Paused
uv run scripts/vers_api.py vm-state <vm_id> Running

# get SSH key
uv run scripts/vers_api.py vm-ssh-key <vm_id>

# list/manage commits
uv run scripts/vers_api.py commits
uv run scripts/vers_api.py commits-public
uv run scripts/vers_api.py commit-set-public <commit_id>
```

---

### `vers_stack.py` — self-contained standup wrapper (recommended)

Self-contained wrapper around `vers-fleets` for provisioning, image builds, and UI login links.

```bash
# inspect resolved repos + branches + tooling
uv run scripts/vers_stack.py doctor

# fast path: provision from known public commits
uv run scripts/vers_stack.py provision-public --out-dir out

# build custom images (auto-wires local sibling reef/pi-vers repos)
uv run scripts/vers_stack.py build-root --private --out-dir out
uv run scripts/vers_stack.py build-golden --private --out-dir out

# explicit commit provisioning
uv run scripts/vers_stack.py provision \
  --root-commit <root-id> \
  --golden-commit <golden-id> \
  --out-dir out

# generate reef UI magic link from deployment manifest
uv run scripts/vers_stack.py magic-link --deployment out/deployment.json
```

Repo resolution order:
1. `--vers-fleets-repo`
2. `$VERS_FLEETS_REPO`
3. sibling `../vers-fleets`
4. `/tmp/vers-fleets`

---

### `vers_fleet.py` — thin vers-fleets wrapper (legacy)

Wrapper around `bun src/cli.js` in a local vers-fleets checkout.

```bash
uv run scripts/vers_fleet.py test
uv run scripts/vers_fleet.py build-root -- --private
uv run scripts/vers_fleet.py build-golden -- --private
uv run scripts/vers_fleet.py provision -- --root-commit <id> --golden-commit <id>
```

Assumes vers-fleets checkout at `/tmp/vers-fleets`. Override: edit `REPO` in script.

---

### `reef_ops.py` — reef runtime

```bash
uv run scripts/reef_ops.py test
uv run scripts/reef_ops.py lint
uv run scripts/reef_ops.py start
uv run scripts/reef_ops.py health
uv run scripts/reef_ops.py list-services
```

Assumes reef checkout at `/tmp/reef`.

---

### `pi_vers_ops.py` — pi-vers extension layer

```bash
uv run scripts/pi_vers_ops.py build
uv run scripts/pi_vers_ops.py list-skills
uv run scripts/pi_vers_ops.py list-docs
uv run scripts/pi_vers_ops.py list-extensions
```

Assumes pi-vers checkout at `/tmp/pi-vers`.

---

## Skills

| Skill | What's in it |
|-------|-------------|
| `vers-api-reference` | Full REST API surface from `docs.vers.sh/llms-full.txt` — VM lifecycle, commits, branching, SSH, shell-auth, networking |
| `vers-stack-bootstrap` | Build-root / build-golden / provision workflows |
| `vers-stack-runtime` | Reef control plane ops |
| `vers-stack-dev` | pi-vers extension development |
| `vers-stack-standup` | Self-contained standup flow: provision-public, build-root/golden, magic-link, v2 branch refs |

---

## Stack quick-reference

```
vers-fleets   →  bootstrap only (build images, provision root VM)
reef          →  runtime control plane (spawns lieutenants, owns SQLite, serves UI)
pi-vers       →  Vers extension layer (VM tools, swarm, SSH, lieutenant RPC)
punkin-pi     →  the agent harness (pi binary, RPC mode)
```

Lieutenant communication (what lt.py implements):

```
coordinator
  │
  │  ssh "cat > /tmp/pi-rpc/in"        ← write JSON prompt
  │  ssh "tail -f /tmp/pi-rpc/out"     ← read streaming response
  ▼
agent VM
  tmux pi-keeper   sleep ∞ > /tmp/pi-rpc/in   (holds FIFO write-end open)
  tmux pi-rpc      pi --mode rpc < in >> out   (the actual agent)
```

---

## Requirements

- `uv` — for running scripts
- `ssh` + `openssl` — for lt.py VM connections
- `VERS_API_KEY` — set in env
- `bun` — for vers_fleet.py and vers_stack.py
