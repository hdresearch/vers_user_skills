# Verse Swarm — Quick Reference

**Audience:** CS-literate, no Verse domain knowledge yet.

**Author:** Carter Schonwald  
**Date:** 2026-04-03

---

## Mental Model

**Core abstraction:** VMs that snapshot/branch like git commits.

```
VM (mutable) --commit--> Commit (immutable) --branch--> new VM(s)
```

**Lieutenant:** Persistent agent session on a VM. Communicates via tmux + FIFOs over SSH.

**Swarm:** N lieutenants from same golden commit, coordinated via task queue.

---

## First-Time Setup

```bash
# API key
export VERS_API_KEY="your-api-key-here"

cd /path/to/verse_user_skills

# See what exists
uv run scripts/vers_api.py vms                # your running VMs
uv run scripts/vers_api.py commits            # your snapshots
uv run scripts/vers_api.py commits-public     # public base images
```

---

## Golden Image (do once)

### Option 1: Use Public Commit

```bash
# Find one
uv run scripts/vers_api.py commits-public | jq -r '.[] | "\(.id) - \(.description // "no desc")"'

export GOLDEN="<commit-id>"
```

### Option 2: Build Custom

```bash
# Create root VM
uv run scripts/vers_api.py vm-new --mem 4096 --disk 16384 --wait-boot
# Returns: {"vm_id": "abc..."}

export VM_ID="abc..."

# Get SSH access
uv run scripts/vers_api.py vm-ssh-key $VM_ID
# Writes key + prints SSH command

# SSH in (via TLS tunnel - weird but works)
ssh -i /tmp/vers-${VM_ID:0:12}.pem \
    -o StrictHostKeyChecking=no \
    -o "ProxyCommand=openssl s_client -connect %h:443 -servername %h -quiet 2>/dev/null" \
    root@${VM_ID}.vm.vers.sh

# Install your stack (agent, tools, runtimes)
apt update && apt install -y python3-pip nodejs git ripgrep
# ... your agent setup ...
exit

# Snapshot
uv run scripts/vers_api.py vm-commit $VM_ID
# Returns: {"id": "def..."}

export GOLDEN="def..."
```

---

## Create Swarm

```bash
# Spin up lieutenants (each is independent VM from golden commit)
uv run scripts/lt.py lt-create backend "backend + DB" $GOLDEN
uv run scripts/lt.py lt-create frontend "UI" $GOLDEN
uv run scripts/lt.py lt-create infra "deploy + CI" $GOLDEN

# Check they're alive
uv run scripts/lt.py lt-status --probe
```

**State:** `~/.vers/lieutenants.json` (atomic writes, safe on crash)

---

## Work

```bash
# Send task
uv run scripts/lt.py lt-send backend "Create FastAPI with /health endpoint"

# Monitor
uv run scripts/lt.py lt-read backend --tail 50
uv run scripts/lt.py lt-read backend --follow    # live stream, auto-reconnects

# Steer (interrupt mid-task)
uv run scripts/lt.py lt-send backend "Stop - use SQLAlchemy not Prisma" --mode steer

# Queue next (while working)
uv run scripts/lt.py lt-send backend "Add password hashing next" --mode followUp
```

---

## Coordination

### Manual (Parallel)

```bash
uv run scripts/lt.py lt-send backend "task 1"
uv run scripts/lt.py lt-send frontend "task 2"  # parallel
uv run scripts/lt.py lt-send infra "task 3"     # parallel
```

### Harness (Dependency Graph)

```json
// workflow.json
{
  "name": "build-app",
  "tasks": [
    {
      "id": "backend-setup",
      "lieutenant": "backend",
      "description": "Setup FastAPI project structure",
      "dependencies": [],
      "output_marker": "BACKEND_SETUP_COMPLETE"
    },
    {
      "id": "backend-auth",
      "lieutenant": "backend",
      "description": "Add auth endpoints with JWT",
      "dependencies": ["backend-setup"],
      "output_marker": "AUTH_READY"
    },
    {
      "id": "frontend-setup",
      "lieutenant": "frontend",
      "description": "Create React + TypeScript app",
      "dependencies": [],
      "output_marker": "FRONTEND_SETUP_COMPLETE"
    },
    {
      "id": "frontend-auth-ui",
      "lieutenant": "frontend",
      "description": "Build login/register components",
      "dependencies": ["frontend-setup", "backend-auth"],
      "output_marker": "AUTH_UI_READY"
    }
  ]
}
```

```bash
uv run examples/swarm_harness.py run workflow.json
# Executes DAG, polls for completion via output markers or status
```

---

## Cost Control

```bash
# Pause (freezes VM, no compute cost, full state preserved)
uv run scripts/lt.py lt-pause backend

# Resume
uv run scripts/lt.py lt-resume backend
```

---

## Branching / Experiments

```bash
# Current state
STATE=$(cat ~/.vers/lieutenants.json)
BACKEND_VM=$(echo $STATE | jq -r '.lieutenants.backend.vmId')

# Snapshot
uv run scripts/vers_api.py vm-commit $BACKEND_VM
# Returns commit ID

# Branch for experiment
uv run scripts/lt.py lt-create backend-alt "try Django" <new-commit-id>
uv run scripts/lt.py lt-send backend-alt "Rewrite with Django"

# Compare, keep winner, destroy loser
```

---

## Cleanup

```bash
uv run scripts/lt.py lt-destroy backend
uv run scripts/lt.py lt-destroy "*"              # nuke all
```

---

## Architecture

```
You (coordinator)
    ↓
lt.py (SSH + tmux/FIFO protocol)
    ↓
VM (lieutenant)
    ├─ tmux 'pi-keeper': sleep ∞ > /tmp/pi-rpc/in    (holds FIFO write-end open)
    └─ tmux 'pi-rpc': pi --mode rpc < in >> out      (agent process)
```

**Wire format:** Newline-delimited JSON over FIFOs.

**RPC messages:**
```json
{"type": "prompt", "id": "task-0001", "message": "do thing", "mode": "prompt"}
{"type": "steer", "id": "task-0002", "message": "stop, do different thing", "mode": "steer"}
```

---

## Try It

```bash
# List public images
uv run scripts/vers_api.py commits-public

# Pick one or build custom
export GOLDEN="<commit-id>"

# Launch
./examples/quickstart.sh $GOLDEN

# Send task
uv run scripts/lt.py lt-send backend "echo 'HELLO' && sleep 5 && echo 'DONE'"
uv run scripts/lt.py lt-read backend --follow
```

---

## Command Reference

### VM Management (vers_api.py)

| Command | Purpose |
|---------|---------|
| `vms` | List all your VMs |
| `commits` | List your snapshots |
| `commits-public` | List public base images |
| `vm-new --mem 4096 --disk 16384 --wait-boot` | Create new VM |
| `vm-from-commit <commit-id>` | Restore VM from snapshot |
| `vm-commit <vm-id>` | Snapshot VM |
| `vm-branch --commit-id <id>` | Branch new VM from snapshot |
| `vm-state <vm-id> Paused` | Pause VM |
| `vm-state <vm-id> Running` | Resume VM |
| `vm-delete <vm-id>` | Delete VM |
| `vm-ssh-key <vm-id>` | Get SSH key |

### Lieutenant Management (lt.py)

| Command | Purpose |
|---------|---------|
| `lt-create <name> <role> <commit-id>` | Create lieutenant from golden commit |
| `lt-send <name> "<message>"` | Send task |
| `lt-send <name> "<message>" --mode steer` | Interrupt and redirect |
| `lt-send <name> "<message>" --mode followUp` | Queue next task |
| `lt-read <name>` | Read recent output |
| `lt-read <name> --follow` | Live stream (auto-reconnects) |
| `lt-read <name> --tail 100` | Last 100 lines |
| `lt-status` | List all lieutenants |
| `lt-status --probe` | SSH in to verify actual state |
| `lt-pause <name>` | Pause lieutenant VM |
| `lt-resume <name>` | Resume lieutenant VM |
| `lt-destroy <name>` | Kill tmux + delete VM |
| `lt-destroy "*"` | Destroy all lieutenants |

### Harness (swarm_harness.py)

| Command | Purpose |
|---------|---------|
| `dashboard` | Show swarm status |
| `run <workflow.json>` | Execute workflow DAG |
| `checkpoint` | Snapshot all lieutenants |

---

## Troubleshooting

### SSH Connection Issues

```bash
# Refresh SSH key
rm -f /tmp/vers-ssh-keys/<vm-id>.pem
uv run scripts/vers_api.py vm-ssh-key <vm-id>
```

### Lieutenant Not Responding

```bash
# Check actual tmux state
uv run scripts/lt.py lt-status --probe

# If session is dead, destroy and recreate
uv run scripts/lt.py lt-destroy <name>
uv run scripts/lt.py lt-create <name> "<role>" $GOLDEN_COMMIT_ID
```

### Task Stuck

```bash
# Check output
uv run scripts/lt.py lt-read <name> --tail 200

# Steer if needed
uv run scripts/lt.py lt-send <name> "Stop - try different approach" --mode steer
```

---

## Files

- **State:** `~/.vers/lieutenants.json`
- **SSH Keys:** `/tmp/vers-ssh-keys/<vm-id>.pem`
- **Checkpoints:** `~/.vers/checkpoints/<timestamp>/`
- **Scripts:** `scripts/{lt.py, vers_api.py, vers_fleet.py}`
- **Examples:** `examples/{swarm_harness.py, workflow_example.json, quickstart.sh}`

---

## Deeper Docs

- **Tutorial:** `verse_swarm_setup.md` (in scratch_space)
- **Examples:** `examples/README.md`
- **Skills:** `skills/*/SKILL.md`
- **Verse API:** https://docs.vers.sh/llms.txt
