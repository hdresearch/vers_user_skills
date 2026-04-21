---
name: use-vers-for-everything:api-reference
description: >
  Authoritative Vers platform API reference distilled from docs.vers.sh/llms-full.txt.
  Supplementary doc of the `use-vers-for-everything` skill. Covers VM lifecycle,
  commits, branching, SSH access, shell-auth, and CLI-to-API mapping. Load when
  making any Vers API call or building agent tooling against the Vers platform.
metadata:
  author: Carter Schonwald
  version: 1
  source: https://docs.vers.sh/llms-full.txt
---

# Vers Platform API Reference

Base URL: `https://api.vers.sh/api/v1`
Docs index: `https://docs.vers.sh/llms.txt`
Full docs: `https://docs.vers.sh/llms-full.txt`

## Companion CLI

`scripts/vers_api.py` (ships with this skill) is a zero-dep Python wrapper over the
endpoints documented below. Agents may shell out to it instead of hand-crafting curl:

```bash
VERS_API_KEY=... uv run skills/use-vers-for-everything/scripts/vers_api.py vms
VERS_API_KEY=... uv run skills/use-vers-for-everything/scripts/vers_api.py new-root --mem 4096 --vcpu 2
```

Use it when the ergonomics matter; fall through to raw HTTP for anything the wrapper
doesn't cover. Full endpoint table: `api-cheatsheet.md` (sibling doc).

---

## Authentication

All `/api/v1/*` endpoints require `Authorization: Bearer <VERS_API_KEY>`.

Obtain key from:
- `https://vers.sh/billing` (dashboard)
- Shell Auth flow (programmatic, see below)

---

## VM Lifecycle

| Method   | Endpoint                              | Description                     |
|----------|---------------------------------------|---------------------------------|
| `POST`   | `/vm/new_root`                        | Create a new root VM            |
| `POST`   | `/vm/from_commit`                     | Restore VM from a commit        |
| `GET`    | `/vms`                                | List all VMs                    |
| `GET`    | `/vm/{vm_id}/status`                  | Get VM status + details         |
| `PATCH`  | `/vm/{vm_id}/state`                   | Pause / resume                  |
| `PATCH`  | `/vm/{vm_id}/disk`                    | Resize disk                     |
| `DELETE` | `/vm/{vm_id}`                         | Delete VM                       |
| `GET`    | `/vm/{vm_id}/ssh_key`                 | Get SSH private key             |
| `GET`    | `/vm/{vm_id}/metadata`                | Get VM metadata                 |

### Create root VM (`POST /vm/new_root`)

```json
{
  "vm_config": {
    "mem_size_mib": 4096,
    "vcpu_count": 2,
    "fs_size_mib": 8192,
    "image_name": "default",
    "kernel_name": "default.bin"
  }
}
```

Returns: `{ "vm_id": "<uuid>" }`

Query param: `?wait_boot=true` — wait for SSH to accept before returning. Caveat:
fresh VMs may still drop long-lived SSH sessions (`rsync`, tarpipe, multi-step
scp`) for a short window after return even when simple commands succeed. Warm the
connection first and treat a single early transfer failure as instability, not proof
the VM is dead.

### Restore from commit (`POST /vm/from_commit`)

```json
{ "commit_id": "<uuid>" }
```

Returns: `{ "vm_id": "<uuid>" }`

### Update VM state (`PATCH /vm/{vm_id}/state`)

```json
{ "state": "Paused" }
```

Valid: `"Paused"`, `"Running"`

### Get SSH credentials (`GET /vm/{vm_id}/ssh_key`)

Response:
```json
{
  "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
  "ssh_port": 443
}
```

SSH connection: host `{vm_id}.vm.vers.sh`, port `443` (TLS), user `root`.

```bash
ssh -i /tmp/vers-{vm_id}.key \
  -o StrictHostKeyChecking=no \
  -o ProxyCommand="openssl s_client -connect %h:443 -servername %h -quiet 2>/dev/null" \
  root@{vm_id}.vm.vers.sh
```

For `rsync` / `scp`, prefer a wrapper script instead of inlining `ProxyCommand`
through `-e`; shell quoting is brittle there.
---

## Branching

| Method | Endpoint                              | Description                      |
|--------|---------------------------------------|----------------------------------|
| `POST` | `/vm/{vm_or_commit_id}/branch`        | Branch from a VM or commit (generic; server dispatches by id type; `by_vm`/`by_commit` below are explicit variants) |
| `POST` | `/vm/branch/by_commit/{commit_id}`    | Branch directly from a commit    |
| `POST` | `/vm/branch/by_vm/{vm_id}`            | Branch from VM (explicit)        |
| `POST` | `/vm/branch/by_tag/{tag_name}`        | Branch from commit tag           |

All branch endpoints accept `?count=N` (default 1) and return `NewVmsResponse`:
`{ "vms": [ { "vm_id": "<new-vm-uuid>" }, ... ] }`. Read `.vms[0].vm_id` for
the single-branch case.

---

## Commits

| Method   | Endpoint                                | Description                 |
|----------|-----------------------------------------|-----------------------------|
| `POST`   | `/vm/{vm_id}/commit`                    | Snapshot VM → commit        |
| `GET`    | `/commits`                              | List your commits           |
| `GET`    | `/commits/public`                       | List all public commits     |
| `PATCH`  | `/commits/{commit_id}`                  | Update commit metadata      |
| `DELETE` | `/commits/{commit_id}`                  | Delete commit               |
| `GET`    | `/vm/commits/{commit_id}/parents`       | Get commit parents          |

Returns from commit: `{ "commit_id": "<uuid>" }`.

Request body for `POST /vm/{vm_id}/commit` is required; `{}` is the minimum valid
JSON (all fields optional — pass `name` / `description` as you wish).
Default: commits are private until explicitly patched public (`is_public: false`).

Make public:
```bash
curl -X PATCH https://api.vers.sh/api/v1/commits/{commit_id} \
  -H "Authorization: Bearer $VERS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"is_public": true}'
```

Only `is_public` is required by the `UpdateCommitRequest` schema; `name` and
`description` are optional. Partial updates preserve unspecified fields.

---

## Commit Tags

| Method   | Endpoint                                | Description            |
|----------|-----------------------------------------|------------------------|
| `GET`    | `/commit_tags`                          | List all tags          |
| `GET`    | `/commit_tags/{tag_name}`               | Get specific tag       |
| `POST`   | `/commit_tags`                          | Create tag             |
| `PATCH`  | `/commit_tags/{tag_name}`               | Update tag             |
| `DELETE` | `/commit_tags/{tag_name}`               | Delete tag             |

---

## Shell Auth (programmatic key creation for agents / CLI)
> **Scope note.** Shell Auth lives at `https://vers.sh/api/shell-auth/*`, **outside** the
> canonical `/api/v1` orchestrator OpenAPI. Shapes below reflect `docs.vers.sh/shell-auth`
> prose docs at the time of writing. Verify before shipping code that depends on them.
>
> **Canonical operational recipe: `onboarding.md`** (sibling doc).
> The step-by-step walkthrough (state detection, route selection, key persistence,
> smoke test, hygiene) lives there. What follows below is the call-layer shape, as
> extracted from supplementary docs.


Three-step flow. Only browser interaction: clicking email link.

```
POST /api/shell-auth            → sends verification email, returns nonce
POST /api/shell-auth/verify-key → poll until { verified: true }, returns orgs
POST /api/shell-auth/api-keys   → creates API key (shown once)
```

### Step 1 — Initiate

```bash
curl -X POST https://vers.sh/api/shell-auth \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","ssh_public_key":"ssh-ed25519 AAAA..."}'
```

### Step 2 — Poll until verified

```bash
while true; do
  VERIFY=$(curl -s -X POST https://vers.sh/api/shell-auth/verify-key \
    -H "Content-Type: application/json" \
    -d '{"email":"user@example.com","ssh_public_key":"ssh-ed25519 AAAA..."}')
  [ "$(echo $VERIFY | python3 -c 'import sys,json;print(json.load(sys.stdin)["verified"])')" = "True" ] && break
  sleep 3
done
```

Response includes: `verified`, `user_id`, `key_id`, `orgs[]`

### Step 3 — Create API key

```bash
curl -X POST https://vers.sh/api/shell-auth/api-keys \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","ssh_public_key":"ssh-ed25519 AAAA...",
       "label":"my-key","org_name":"acme-corp"}'
```

Response: `{ "success": true, "api_key": "...", "api_key_id": "...", "org_id": "...", "org_name": "..." }`

Additional diagnostic:
```
POST /api/shell-auth/verify-public-key  → lookup key by public key
```

---

## CLI → API Mapping

| CLI              | API                               |
|------------------|-----------------------------------|
| `vers run`       | `POST /vm/new_root`               |
| `vers status`    | `GET /vms`                        |
| `vers branch`    | `POST /vm/{id}/branch`            |
| `vers commit`    | `POST /vm/{id}/commit`            |
| `vers run-commit`| `POST /vm/from_commit`            |
| `vers delete`    | `DELETE /vm/{id}`                 |
| `vers pause`     | `PATCH /vm/{id}/state` (Paused)   |
| `vers resume`    | `PATCH /vm/{id}/state` (Running)  |

---

## Network contract

- Public URL: `https://{vm_id}.vm.vers.sh:{port}`.
- All ports routable; no firewall config.
- TLS terminates at the Vers proxy; the VM serves plain HTTP behind it.
- Bind IPv6 (`::`). The proxy routes IPv6; `0.0.0.0` is unreachable from outside.
- WebSockets reach a VM at `wss://{vm_id}.vm.vers.sh:{port}` when the service binds `::`.
- SSH-over-TLS on port 443. Works through HTTPS-only egress.
- VM-to-VM: same public URL pattern. No private VPC.
---

## Response shapes

```json
// GET /vms
[{"vm_id": "...", "owner_id": "...", "created_at": "...", "state": "running"}]

// POST /vm/from_commit or /vm/new_root
{"vm_id": "<new-uuid>"}

// POST /vm/{id}/branch or /vm/branch/by_{vm,commit,tag,ref}/...
{"vms": [{"vm_id": "<new-uuid>"}, ...]}

// POST /vm/{id}/commit
{"commit_id": "<uuid>"}

// errors
{"error": "Error description", "success": false}
```

---

## Refreshing this reference

This skill distills `docs.vers.sh/llms-full.txt`. Refresh when an endpoint
below returns a surprising error, when the Vers changelog adds a new endpoint,
or quarterly as hygiene.

```bash
# 1. Pull current source of truth
curl -sS https://docs.vers.sh/llms-full.txt > /tmp/vers-docs.new.txt
curl -sS https://docs.vers.sh/llms.txt      > /tmp/vers-index.new.txt

# 2. Diff endpoint headings against what this file documents
grep -E '^# (Post|Get|Put|Patch|Delete) apiv1' /tmp/vers-docs.new.txt | sort -u
#   → cross-check against the tables in this skill

# 3. For any new/changed endpoint, fetch its OpenAPI stub:
#    https://docs.vers.sh/api-reference/<section>/<slug>.md
#    → shows request/response schemas the llms-full.txt omits

# 4. Update this file's tables + request/response examples
# 5. Run the eval harness (tests/vers_skills_eval_prompts.md) against this
#    skill as a regression smoke test
```

For fuller endpoint coverage than the tables above, see `api-cheatsheet.md`
(same repo): `/repositories` (+ tags, fork, visibility), `/public/repositories`,
`/vm/branch/by_ref`, `/vm/{id}/files` (PUT/GET), `/vm/{id}/exec`,
`/vm/{id}/exec/stream`, `/vm/{id}/exec/stream/attach`, `/vm/{id}/logs`, `/domains`,
`/env_vars`.

---

## Source docs

- https://docs.vers.sh/llms.txt — index of all pages
- https://docs.vers.sh/llms-full.txt — full content (~6600 lines, 163 KB)
- https://docs.vers.sh/api-reference/introduction.md
- https://docs.vers.sh/shell-auth/overview.md
- https://docs.vers.sh/vm-access.md
