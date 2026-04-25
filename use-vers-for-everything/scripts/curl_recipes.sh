#!/usr/bin/env bash
#
# vers.sh API recipes (no python, no SDK, just curl + jq).
#
# Setup:
#   export VERS_API_KEY=...
#   export V=https://api.vers.sh/api/v1
#   H="Authorization: Bearer $VERS_API_KEY"
#
# All commands here are copy-pasteable individually. They each include the
# full setup line at the top so you can grab one and go.

# ============================================================
# 1. List your VMs (filtered to the ones you own)
# ============================================================
# Note: GET /vms returns vms across the whole org you can see. Filter by
# owner_id matching your api key's uuid prefix.
curl -sS -H "Authorization: Bearer $VERS_API_KEY" \
  "https://api.vers.sh/api/v1/vms" \
  | jq --arg me "${VERS_API_KEY:0:36}" '[.[] | select(.owner_id == $me)]'

# ============================================================
# 2. Create a fresh VM (cold boot, wait until ready)
# ============================================================
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/vm/new_root?wait_boot=true" \
  -d '{"vm_config":{"mem_size_mib":<MiB>,"vcpu_count":<N>,"fs_size_mib":<MiB>}}'

# ============================================================
# 3. Get a VM's metadata (including IPv6 - list_vms does NOT include this)
# ============================================================
VM=...                             # paste vm_id here
curl -sS -H "Authorization: Bearer $VERS_API_KEY" \
  "https://api.vers.sh/api/v1/vm/$VM/metadata"

# ============================================================
# 4. Run a command in a VM (synchronous, returns exit_code/stdout/stderr)
# ============================================================
VM=...
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/vm/$VM/exec" \
  -d '{"command":["uname","-a"]}'

# With env + working_dir:
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/vm/$VM/exec" \
  -d '{"command":["sh","-c","echo $FOO"],"env":{"FOO":"bar"},"working_dir":"/tmp"}'

# ============================================================
# 5. Snapshot a VM (creates a commit; preserves filesystem AND memory)
# ============================================================
VM=...
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  "https://api.vers.sh/api/v1/vm/$VM/commit"

# ============================================================
# 6. Branch from a commit (CRIU-style restore; new VM is instantly running)
# ============================================================
COMMIT=...
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  "https://api.vers.sh/api/v1/vm/branch/by_commit/$COMMIT"
# To fan out to N VMs in parallel, add ?count=N. CoW makes this cheap.

# ============================================================
# 7. Branch from a published image (the canonical phase-2 op)
# ============================================================
REPO=ubuntu  # your repo name
TAG=24.04
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  "https://api.vers.sh/api/v1/vm/branch/by_ref/$REPO/$TAG"

# ============================================================
# 8. Tag a commit (publish for reuse)
# ============================================================
REPO=my-repo
COMMIT=...
TAG=v1
# create the repo first if it doesn't exist:
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/repositories" \
  -d "{\"name\":\"$REPO\",\"description\":\"\"}"
# then tag:
curl -sS -X POST -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/repositories/$REPO/tags" \
  -d "{\"tag_name\":\"$TAG\",\"commit_id\":\"$COMMIT\"}"

# ============================================================
# 9. Pause / resume a VM (preserves complete state, frees compute)
# ============================================================
VM=...
# pause:
curl -sS -X PATCH -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/vm/$VM/state" \
  -d '{"state":"Paused"}'   # NOTE: capitalized for writes (lowercase for reads)
# resume:
curl -sS -X PATCH -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/vm/$VM/state" \
  -d '{"state":"Running"}'

# ============================================================
# 10. Get SSH credentials for a VM (treat as secret)
# ============================================================
VM=...
curl -sS -H "Authorization: Bearer $VERS_API_KEY" \
  "https://api.vers.sh/api/v1/vm/$VM/ssh_key"

# ============================================================
# 11. Set boot-time env vars (written to /etc/environment in NEW vms)
# ============================================================
# Note: these affect new vms only. Existing vms are not retroactively updated.
curl -sS -X PUT -H "Authorization: Bearer $VERS_API_KEY" \
  -H "content-type: application/json" \
  "https://api.vers.sh/api/v1/env_vars" \
  -d '{"vars":{"OPENAI_API_KEY":"sk-...","ANTHROPIC_API_KEY":"sk-ant-..."},"replace":false}'

# ============================================================
# 12. Explicit user-authorized VM termination
# ============================================================
VM=...
curl -sS -X DELETE -H "Authorization: Bearer $VERS_API_KEY" \
  "https://api.vers.sh/api/v1/vm/$VM"
# Note: returns 200 + body, NOT 204. This is outside the autonomous loop.
# Also note: deleting a non-existent uuid returns 403 not 404.
