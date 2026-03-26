---
name: vers-api-reference
description: >
  Authoritative Vers platform API reference distilled from docs.vers.sh/llms-full.txt.
  Covers VM lifecycle, commits, branching, SSH access, shell-auth, and CLI-to-API mapping.
  Use when making any Vers API call, writing pi extensions, or implementing fleet automation.
metadata:
  author: Carter Schonwald
  version: 1
  source: https://docs.vers.sh/llms-full.txt
  retrieved: 2026-03-26
---

# Vers Platform API Reference

Base URL: `https://api.vers.sh/api/v1`
Docs index: `https://docs.vers.sh/llms.txt`
Full docs: `https://docs.vers.sh/llms-full.txt`

## Authentication

All endpoints require `Authorization: Bearer <VERS_API_KEY>` except `/health`.

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

Query param: `?wait_boot=true` — wait for SSH before returning.

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

---

## Branching

| Method | Endpoint                              | Description                      |
|--------|---------------------------------------|----------------------------------|
| `POST` | `/vm/{vm_id}/branch`                  | Branch from running VM           |
| `POST` | `/vm/branch/by_commit/{commit_id}`    | Branch directly from a commit    |
| `POST` | `/vm/branch/by_vm/{vm_id}`            | Branch from VM (explicit)        |
| `POST` | `/vm/branch/by_tag/{tag_name}`        | Branch from commit tag           |

Returns: `{ "vm_id": "<new-vm-uuid>" }`

---

## Commits

| Method   | Endpoint                                | Description                 |
|----------|-----------------------------------------|-----------------------------|
| `POST`   | `/vm/{vm_id}/commit`                    | Snapshot VM → commit        |
| `GET`    | `/commits`                              | List your commits           |
| `GET`    | `/commits/public`                       | List all public commits     |
| `PATCH`  | `/commits/{commit_id}`                  | Update commit (e.g. is_public) |
| `DELETE` | `/commits/{commit_id}`                  | Delete commit               |
| `GET`    | `/vm/commits/{commit_id}/parents`       | Get commit parents          |

Returns from commit: `{ "commit_id": "<uuid>", "host_architecture": "x86_64" }`

Make public:
```bash
curl -X PATCH https://api.vers.sh/api/v1/commits/{commit_id} \
  -H "Authorization: Bearer $VERS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"is_public": true}'
```

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

## Networking

- Public URL: `https://{vm_id}.vm.vers.sh:{port}`
- All ports routable, no firewall config needed
- TLS terminated at proxy; VM serves plain HTTP
- **Must bind IPv6** (`::` not `0.0.0.0`) for proxy to route
- SSH over TLS on port 443 (works through HTTPS-only firewalls)
- VM-to-VM: same public URL pattern, no private network

---

## Response shapes

```json
// GET /vms
[{"vm_id": "...", "owner_id": "...", "created_at": "...", "state": "running"}]

// POST /vm/{id}/branch or /vm/from_commit or /vm/new_root
{"vm_id": "<new-uuid>"}

// POST /vm/{id}/commit
{"commit_id": "<uuid>", "host_architecture": "x86_64"}

// errors
{"error": "Error description", "success": false}
```

---

## Source docs

- https://docs.vers.sh/llms.txt — index of all pages
- https://docs.vers.sh/llms-full.txt — full content (~6600 lines, 163 KB)
- https://docs.vers.sh/api-reference/introduction.md
- https://docs.vers.sh/shell-auth/overview.md
- https://docs.vers.sh/vm-access.md
