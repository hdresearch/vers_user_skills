# Vers REST API Cheat Sheet

Derived from `docs.vers.sh/api-reference/openapi.json` (OpenAPI `info.version` = `0.1.0`,
title "Orchestrator Control Plane API", fetched 2026-04-20). 53 spec endpoints + 1
prose-only (`/health`) documented below. Every endpoint appears in exactly one row.

## Auth & Base URL

- Base URL: `https://api.vers.sh/api/v1`
- Authentication header: `Authorization: Bearer $VERS_API_KEY`
- Public (no bearer required): `GET /health` (at `https://api.vers.sh/health`, NOT under
  `/api/v1`), everything under `/api/v1/public/repositories/**`.
- All other endpoints return `401 Unauthorized` without a valid bearer.
- Response `Content-Type` is `application/json` except `204 No Content`, NDJSON streams
  (`/vm/{id}/exec/stream*`), and the public health check (plain text/JSON, prose-only).
- No key? → `onboarding.md` (same skill).

## Conventions

- IDs (`vm_id`, `commit_id`, `domain_id`, `tag_id`, `repo_id`) are UUIDs. `repo_name`,
  `tag_name`, `key` (env var), `org_name` are opaque strings.
- Error body shape (4xx/5xx, except 401): `{ "error": "<msg>", "success": false }`
  (schema `ErrorResponse`). 401 is an empty body / generic unauthorized.
- Pagination: `GET /commits` and `GET /commits/public` take `limit` and `offset` query params; defaults `limit=50, offset=0`.
- Timestamps: RFC3339 / ISO-8601 strings (`created_at`, `updated_at`, `deleted_at`).
- VM lifecycle states (`VmState`): `booting | running | paused | sleeping | dead`.
- Mutable state transitions via `PATCH /vm/{id}/state` are restricted to `VmUpdateStateEnum = Paused | Running`.
- Two tag namespaces coexist. Prefer the repo-scoped form:
  **modern (preferred)** — `/repositories/{repo}/tags/*`, namespaced per
  repository; referenced by `from_commit {ref}` and `branch/by_ref`.
  **legacy** — `/commit_tags/*`, flat per-account; kept for quick personal
  tagging.

---

## VMs (lifecycle + introspection)

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| GET    | `/vms`                         | List all VMs owned by caller | — | — | `200` `[VM]` (`vm_id, owner_id, state, created_at, labels?`) | 401, 403, 500 |
| POST   | `/vm/new_root`                 | Create a brand-new VM from default rootfs (no parent commit) | q: `wait_boot:bool` | `NewRootRequest { *vm_config: VmCreateVmConfig }` — `VmCreateVmConfig { fs_size_mib?, image_name?='default', kernel_name?='default.bin', labels?, mem_size_mib?, vcpu_count? }` | `201 { vm_id }` | 400, 401, 403, 500 |
| POST   | `/vm/from_commit`              | Boot a VM whose rootfs is restored from a commit / legacy tag / repo ref | — | `FromCommitVmRequest` = oneOf `{commit_id}` / `{tag_name}` (legacy) / `{ref: "repo:tag"}` | `201 { vm_id }` | 400, 401, 403, 404, 500 |
| GET    | `/vm/{vm_id}/status`           | Lightweight VM status (state, labels) | p: `vm_id` | — | `200 VM` | 401, 403, 404, 500 |
| GET    | `/vm/{vm_id}/metadata`         | Full metadata: ip, parent_commit_id, grandparent_vm_id, created_at, deleted_at, state, owner_id | p: `vm_id` | — | `200 VmMetadataResponse` | 401, 403, 404, 500 |
| GET    | `/vm/{vm_id}/ssh_key`          | One-shot SSH private key + ssh_port for this VM | p: `vm_id` | — | `200 { ssh_port, ssh_private_key }` | 401, 403, 404, 500 |
| GET    | `/vm/{vm_id}/logs`             | Fetch accumulated VM exec logs (paged, optional filter) | p: `vm_id`; q: `offset:int`, `max_entries:int`, `stream:string` (`stdout`/`stderr`) | — | `200 { entries:[{timestamp,stream,data_b64,exec_id?}], eof, next_offset }` | 401, 403, 404, 500 |
| PATCH  | `/vm/{vm_id}/state`            | Change run state (Running / Paused) | p: `vm_id`; q: `skip_wait_boot:bool` | `{ state: "Running" \| "Paused" }` | `200` (empty) | 400, 401, 403, 404, 500 |
| PATCH  | `/vm/{vm_id}/disk`             | Resize rootfs disk (MiB, grow only in practice) | p: `vm_id`; q: `skip_wait_boot:bool` | `{ fs_size_mib:int }` | `200` (empty) | 400, 401, 403, 404, 500 |
| POST   | `/vm/{vm_id}/commit`           | Snapshot VM rootfs into a new commit | p: `vm_id`; q: `keep_paused:bool`, `skip_wait_boot:bool` | `VmCommitRequest { commit_id?, name?, description? }` (all fields nullable) | `201 { commit_id }` | 400, 401, 403, 404, 500 |
| DELETE | `/vm/{vm_id}`                  | Destroy the VM (does not delete its commits) | p: `vm_id`; q: `skip_wait_boot:bool` | — | `200 { vm_id }` | 400, 401, 403, 404, 500 |
| POST   | `/vm/{vm_id}/exec`             | Run a command synchronously; return full stdout/stderr/exit_code | p: `vm_id` | `VmExecRequest { *command:[string], env?, stdin?, working_dir?, timeout_secs?, exec_id? }` | `200 { stdout, stderr, exit_code, exec_id? }` | 401, 403, 404, 500 |
| POST   | `/vm/{vm_id}/exec/stream`      | Run command; server streams NDJSON `{timestamp,stream,data_b64,exec_id}` chunks | p: `vm_id` | Same `VmExecRequest`. Supply `exec_id` to enable attach-by-cursor | `200` NDJSON stream | 401, 403, 404, 500 |
| POST   | `/vm/{vm_id}/exec/stream/attach` | Re-attach to a running / completed exec, replay from cursor | p: `vm_id` | `{ *exec_id, cursor?:int, from_latest?:bool }` | `200` NDJSON stream | 401, 403, 404, 500 |
| GET    | `/vm/{vm_id}/files`            | Read a single file out of the VM as base64 | p: `vm_id`; q: `*path:string` | — | `200 { content_b64 }` | 401, 403, 404, 500 |
| PUT    | `/vm/{vm_id}/files`            | Write a file into the VM (base64 body) | p: `vm_id` | `{ *path, *content_b64, mode?:int, create_dirs?:bool }` | `200` (empty) | 401, 403, 404, 500 |



---

## Branch / Fork VMs (copy-on-write spawn)

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| POST | `/vm/{vm_or_commit_id}/branch`            | Branch `count` VMs from a VM **or** a commit (server auto-detects) | p: `vm_or_commit_id`; q: `keep_paused`, `skip_wait_boot`, `count:int` | — | `201 { vms:[{vm_id}] }` | 400, 401, 403, 404, 500 |
| POST | `/vm/branch/by_vm/{vm_id}`                | Branch N VMs from a live VM (state is copied) | p: `vm_id`; q: `keep_paused`, `skip_wait_boot`, `count:int` | — | `201 { vms:[{vm_id}] }` | 400, 401, 403, 404, 500 |
| POST | `/vm/branch/by_commit/{commit_id}`        | Branch N VMs from a committed snapshot | p: `commit_id`; q: `count:int` | — | `201 { vms:[{vm_id}] }` | 400, 401, 403, 404, 500 |
| POST | `/vm/branch/by_tag/{tag_name}`            | Branch N VMs from a legacy org-scoped commit tag | p: `tag_name`; q: `count:int` | — | `201 { vms:[{vm_id}] }` | 400, 401, 403, 404, 500 |
| POST | `/vm/branch/by_ref/{repo_name}/{tag_name}`| Branch N VMs from a repo-scoped tag (modern) | p: `repo_name, tag_name`; q: `count:int` | — | `201 { vms:[{vm_id}] }` | 400, 401, 403, 404, 500 |
| GET  | `/vm/commits/{commit_id}/parents`         | Lineage walk: list this commit + ancestor commits up to the root | p: `commit_id` | — | `200 [VmCommitEntity]` (`id, owner_id, name, created_at, is_public, description?, parent_vm_id?, grandparent_commit_id?`) | 401, 403, 404, 500 |

---

## Commits

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| GET    | `/commits`                  | Paginated list of caller's commits | q: `limit?:int=50`, `offset?:int=0` | — | `200 { commits:[CommitInfo], total, limit, offset }` — `CommitInfo { commit_id, name, owner_id, is_public, created_at, description?, parent_vm_id?, grandparent_commit_id? }` | 401, 500 |
| GET    | `/commits/public`           | Paginated list of all public commits (cross-account) | q: `limit?`, `offset?` | — | `200 { commits, total, limit, offset }` | 401, 500 |
| PATCH  | `/commits/{commit_id}`      | Update commit metadata | p: `commit_id` | `UpdateCommitRequest { *is_public:bool, name?:string, description?:string }` | `200 CommitInfo` | 401, 403, 404, 500 |
| DELETE | `/commits/{commit_id}`      | Delete the commit (fails 409 if referenced by VMs/tags) | p: `commit_id` | — | `204` | 401, 403, 404, 409, 500 |

---

## Commit Tags (legacy, flat per-account namespace)

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| GET    | `/commit_tags`              | List caller's legacy commit tags | — | — | `200 { tags:[TagInfo] }` — `TagInfo { tag_id, tag_name, commit_id, created_at, updated_at, description? }` | 401, 500 |
| POST   | `/commit_tags`              | Create a tag pointing at a commit | — | `{ *tag_name, *commit_id, description? }` | `201 { tag_id, tag_name, commit_id }` | 400, 401, 403, 404, 409, 500 |
| GET    | `/commit_tags/{tag_name}`   | Fetch tag by name | p: `tag_name` | — | `200 TagInfo` | 401, 403, 404, 500 |
| PATCH  | `/commit_tags/{tag_name}`   | Repoint tag to new commit and/or edit description | p: `tag_name` | `{ commit_id?, description? }` (nullable) | `204` | 400, 401, 403, 404, 500 |
| DELETE | `/commit_tags/{tag_name}`   | Delete the tag (commit untouched) | p: `tag_name` | — | `204` | 401, 403, 404, 500 |

---

## Repositories (and repo-scoped tags)

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| GET    | `/repositories`                                 | List caller's repos | — | — | `200 { repositories:[RepositoryInfo] }` — `{ repo_id, name, is_public, created_at, description? }` | 401, 500 |
| POST   | `/repositories`                                 | Create a new (empty) repo | — | `{ *name, description? }` | `201 { repo_id, name }` | 400, 401, 409, 500 |
| GET    | `/repositories/{repo_name}`                     | Fetch one repo | p: `repo_name` | — | `200 RepositoryInfo` | 401, 404, 500 |
| DELETE | `/repositories/{repo_name}`                     | Delete a repo (cascades its tags; commits remain) | p: `repo_name` | — | `204` | 401, 403, 404, 500 |
| PATCH  | `/repositories/{repo_name}/visibility`          | Flip public/private flag | p: `repo_name` | `{ *is_public:bool }` | `204` | 401, 403, 404, 500 |
| POST   | `/repositories/fork`                            | Fork a public repo's tag into caller's namespace as a new VM+tag | — | `{ *source_org, *source_repo, *source_tag, repo_name?, tag_name? }` | `201 { repo_name, tag_name, reference, commit_id, vm_id }` | 401, 404, 409, 500 |
| GET    | `/repositories/{repo_name}/tags`                | List tags in a repo | p: `repo_name` | — | `200 { repository, tags:[RepoTagInfo] }` — `{ tag_id, tag_name, commit_id, reference, created_at, updated_at, description? }` | 401, 404, 500 |
| POST   | `/repositories/{repo_name}/tags`                | Create a repo-scoped tag | p: `repo_name` | `{ *tag_name, *commit_id, description? }` | `201 { tag_id, commit_id, reference }` | 400, 401, 403, 404, 409, 500 |
| GET    | `/repositories/{repo_name}/tags/{tag_name}`     | Fetch one repo-scoped tag | p: `repo_name, tag_name` | — | `200 RepoTagInfo` | 401, 404, 500 |
| PATCH  | `/repositories/{repo_name}/tags/{tag_name}`     | Repoint a repo-scoped tag | p: `repo_name, tag_name` | `{ commit_id?, description? }` | `204` | 400, 401, 403, 404, 500 |
| DELETE | `/repositories/{repo_name}/tags/{tag_name}`     | Delete a repo-scoped tag | p: `repo_name, tag_name` | — | `204` | 401, 403, 404, 500 |

---

## Public Repositories (no auth; discovery only)

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| GET | `/public/repositories`                                         | List all public repos across accounts | — | — | `200 { repositories:[PublicRepositoryInfo] }` — `{ repo_id, org_name, name, full_name, created_at, description? }` | 500 |
| GET | `/public/repositories/{org_name}/{repo_name}`                  | Fetch a public repo by org+name | p: `org_name, repo_name` | — | `200 PublicRepositoryInfo` | 404, 500 |
| GET | `/public/repositories/{org_name}/{repo_name}/tags`             | List tags in a public repo | p: `org_name, repo_name` | — | `200 { repository, tags:[RepoTagInfo] }` | 404, 500 |
| GET | `/public/repositories/{org_name}/{repo_name}/tags/{tag_name}`  | Resolve a public repo tag → commit | p: `org_name, repo_name, tag_name` | — | `200 RepoTagInfo` | 404, 500 |

---

## Domains (route traffic at VM via `*.vm.vers.sh` or custom)

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| GET    | `/domains`              | List domains; optional filter by VM | q: `vm_id?` | — | `200 [DomainResponse]` — `{ domain_id, domain, vm_id, created_at }` | 401, 403, 500 |
| POST   | `/domains`              | Bind a domain to a VM | — | `{ *domain, *vm_id }` | `201 DomainResponse` | 400, 401, 403, 404, 409, 500 |
| GET    | `/domains/{domain_id}`  | Fetch one binding | p: `domain_id` | — | `200 DomainResponse` | 401, 403, 404, 500 |
| DELETE | `/domains/{domain_id}`  | Release a domain | p: `domain_id` | — | `200 { domain_id }` (note: 200, not 204) | 401, 403, 404, 500 |

---

## Env Vars (account-global, injected into every VM)

| Method | Path | Purpose | Params | Body | 2xx | Errors |
|---|---|---|---|---|---|---|
| GET    | `/env_vars`        | Read all account env vars | — | — | `200 { vars: {k:v} }` | 401, 500 |
| PUT    | `/env_vars`        | Upsert env vars; `replace:true` wipes pre-existing | — | `{ *vars:{k:v}, replace?:bool=false }` | `200 { vars }` | 400, 401, 500 |
| DELETE | `/env_vars/{key}`  | Remove one key | p: `key` | — | `204` | 401, 404, 500 |

---

## Undocumented / prose-only

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/health` (at `https://api.vers.sh/health`, **NOT under `/api/v1`**) | Public liveness check | Absent from `paths` in openapi.json. Documented only in the prose (`llms-full.txt`): `curl https://api.vers.sh/health`. No auth required. |

Also absent from the spec: no explicit `/rootfs` resource (rootfs is managed implicitly
via `VmCreateVmConfig.image_name` / `kernel_name`, both of which currently must be the
string `"default"` / `"default.bin"`).

---

## Endpoint count by section

VMs 16 | Branch 6 | Commits 4 | Commit Tags 5 | Repositories 11 | Public Repos 4 | Domains 4 | Env Vars 3 = **53** spec endpoints. `/health` is documented in prose only.
