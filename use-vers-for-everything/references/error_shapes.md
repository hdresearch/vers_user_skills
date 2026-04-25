# vers.sh - observed error and response shapes (empirical)

Reference catalog of the error envelope shapes the live API returns,
across status codes and content types. Use when handling unexpected
responses; cross-reference with SKILL.md anomaly catalog.

## 0. tl;dr - what an http client must do

three independent dispatch axes, all needed:

1. **on auth failure: bytes are NOT JSON.** body may be literal `403 Forbidden` (no Content-Type). don't `r.json()` blind.
2. **on schema-validation failure: bytes are NOT JSON either.** rust serde returns plain text in `text/plain; charset=utf-8`. body starts with literal string `Failed to parse the request body as JSON: ...` (400) or `Failed to deserialize the JSON body into the target type: ...` (422).
3. **on application errors: bytes ARE JSON, two shapes coexist.** mostly `{"error":"...","success":false}` (the documented `ErrorResponse`), but `branch/by_vm` and `branch/by_ref` use a hybrid `{"vms":[],"error":"..."}` (no `success` field).

short version of the rule in code:

```
status >= 400:
    if status == 503 and body == "DNS cache overflow":  # (claude-only proxy blip; retry)
        retry
    if content_type starts with "text/plain" or content_type is empty:
        raise VersTextError(status, body)
    if content_type starts with "application/json":
        d = json.loads(body)
        msg = d.get("error", "(no message)")
        raise VersError(status, msg, d)
```

## 1. error envelope taxonomy (observed)

| status | trigger | format | content-type | example body |
|--------|---------|--------|--------------|--------------|
| 400 | malformed UUID in path | plain text | (none) | `Invalid URL: Cannot parse `vm_id` with value `not-a-uuid-string`: UUID parsing failed: invalid character: found `n` at 1` |
| 400 | request body not parseable as JSON (incl. empty body, truncated body, multi-key oneOf) | plain text | `text/plain; charset=utf-8` | `Failed to parse the request body as JSON: expected value at line 1 column 2` |
| 400 | application-level validation (bad ref format, invalid repo name, env var key bad) | json `ErrorResponse` | `application/json` | `{"error":"Invalid ref format. Expected 'repo_name:tag_name'","success":false}` |
| 403 | bad/missing/wrong auth (gateway layer) | plain text | (none) | `403 Forbidden` (literally 13 bytes) |
| 403 | `DELETE /vm/{uuid}` where uuid does not exist or belongs to someone else | json `ErrorResponse` | `application/json` | `{"error":"Forbidden","success":false}` |
| 404 | resource not found by uuid (most ops) | json `ErrorResponse` | `application/json` | `{"error":"not found","success":false}` |
| 404 | resource not found by name | json `ErrorResponse` | `application/json` | `{"error":"repository not found","success":false}` |
| 404 | `/commit_tags/{tag}` not found (legacy) | json `ErrorResponse` | `application/json` | `{"error":"tag not found","success":false}` |
| 404 | `/vm/from_commit` with non-existent uuid | json `ErrorResponse` | `application/json` | `{"error":"commit not found","success":false}` |
| 404 | `branch/by_commit/{id}` non-existent | json `ErrorResponse` | `application/json` | `{"error":"commit not found","success":false}` |
| 404 | `branch/by_vm/{id}` non-existent | **hybrid** (no `success`) | `application/json` | `{"vms":[],"error":"parent vm not found"}` |
| 404 | `branch/by_ref/{repo}/{tag}` non-existent | **hybrid** (no `success`) | `application/json` | `{"vms":[],"error":"tag not found"}` |
| 404 | polymorphic `/vm/{id}/branch` non-existent | json `ErrorResponse` | `application/json` | `{"error":"commit not found","success":false}` (note: msg always says "commit", even if you intended a vm) |
| 404 | `DELETE /env_vars/{key}` non-existent | json `ErrorResponse` | `application/json` | `{"error":"Environment variable not found","success":false}` |
| 409 | `POST /repositories` with duplicate name | json `ErrorResponse` | `application/json` | `{"error":"repository already exists","success":false}` |
| 409 | `DELETE /commits/{id}` with active descendant VMs | json `ErrorResponse` | `application/json` | `{"error":"commit still has 1 active VM(s)","success":false}` |
| 422 | JSON parses but doesn't match target schema (state enum casing, type mismatch, negative integer for u32, etc.) | plain text | `text/plain; charset=utf-8` | `Failed to deserialize the JSON body into the target type: state: unknown variant `running`, expected `Paused` or `Running` at line 1 column 19` |
| 503 | (NOT vers.sh - claude.ai egress proxy hiccup) | plain text | `text/plain` | `DNS cache overflow` (retry) |

### 400 vs 422 - the rule in one sentence

400 = "I couldn't even parse this as JSON" or "your path UUID is malformed". 422 = "your JSON parses fine, but the values don't fit my types".

### the 403 trap

DELETE on a non-existent VM uuid returns **403 not 404**. probably to avoid leaking which uuids exist. but the inconsistency means: a client that handles 404-as-already-gone for idempotent delete logic will misclassify "vm doesn't exist OR is someone else's" as "auth problem". the body `{"error":"Forbidden"}` is the only signal.

## 2. success envelope taxonomy (observed)

| status | endpoint | body shape |
|--------|----------|-----------|
| 200 | `GET /vms` | `[VmStatus, ...]` (VmStatus = vm_id, owner_id, created_at, **labels**, state) |
| 200 | `GET /vm/{id}/metadata` | `VmMetadataResponse` (includes `ip`, `parent_commit_id`, `grandparent_vm_id`, `deleted_at`, no `labels`) |
| 200 | `GET /vm/{id}/status` | `VmStatus` (no `ip`, has `labels`) |
| 200 | `GET /vm/{id}/ssh_key` | `{ssh_private_key, ssh_port}` |
| 200 | `POST /vm/{id}/exec` | `{exit_code, stdout, stderr}` (exec_id may be absent) |
| 200 | `GET /vm/{id}/logs` | `{entries:[{exec_id, timestamp, stream, data_b64}], next_offset, eof}` |
| 200 | `GET /env_vars` / `PUT /env_vars` | `{vars: {KEY:VALUE, ...}}` |
| 200 | `GET /commits` (with or without `?limit=&offset=`) | `{commits:[CommitInfo, ...]}` |
| 200 | `GET /commits/public` | same shape |
| 200 | `GET /repositories` | `{repositories:[RepositoryInfo, ...]}` |
| 200 | `GET /repositories/{name}` | `RepositoryInfo` (full: includes description, is_public, created_at) |
| 200 | `PATCH /vm/{id}/state` | empty body, no Content-Type |
| 200 | `DELETE /vm/{id}` | `{vm_id: "..."}` (NOT 204!) |
| 201 | `POST /vm/new_root` | `{vm_id: "..."}` (NewVmResponse) |
| 201 | `POST /vm/from_commit` | same |
| 201 | `POST /vm/{id}/commit` | `{commit_id: "..."}` (VmCommitResponse) |
| 201 | `POST /vm/branch/by_*` and polymorphic `/branch` | `{vms:[{vm_id:"..."}, ...]}` (NewVmsResponse) |
| 201 | `POST /repositories` | `{repo_id, name}` only - **lighter than `RepositoryInfo`** |
| 204 | `DELETE /env_vars/{key}` | empty body |
| 204 | `DELETE /commits/{id}` | empty body |
| 204 | `DELETE /repositories/{name}` | empty body |

### success-code wart

`DELETE /vm/{id}` returns 200+body, but `DELETE /env_vars/{key}`, `DELETE /commits/{id}`, `DELETE /repositories/{name}` all return 204+empty. one outlier.

## 3. spec divergences (openapi says X, server does Y)

| # | location | spec says | server does |
|---|----------|-----------|-------------|
| 1 | `VM` schema (used by `/vms` and `/vm/{id}/status`) | 4 fields: vm_id, owner_id, created_at, state | 5 fields: adds **`labels`** (object) |
| 2 | `/commits` items | `CommitInfo` (string types, field `commit_id`) OR `VmCommitEntity` (uuid types, field `id`) | a third de facto shape: uuid types, field `commit_id`, also has `parent_vm_id`, `grandparent_commit_id`, `description`, `is_public` |
| 3 | `GET /commits` `limit` / `offset` | declared `in: path` | accepted as `?limit=&offset=` query (path version is unreachable). **codegen-from-spec will produce broken code** |
| 4 | `branch/by_vm/{id}` and `branch/by_ref/{repo}/{tag}` 4xx response | declared `NewVmsResponse` | hybrid `{"vms":[],"error":"..."}` (no `success` field; merges `NewVmsResponse.vms` and `ErrorResponse.error`) |
| 5 | `branch/by_commit/{id}` and `/vm/{id}/branch` 4xx response | declared `NewVmsResponse` | actually `ErrorResponse` (cleanly) |
| 6 | `VmExecLogEntry.timestamp` | declared `string` (no format) | actually RFC3339 timestamp with high precision and offset |
| 7 | `VmExecResponse.exec_id` | nullable string-uuid | often **absent** from synchronous exec responses (not present-and-null; just missing) |
| 8 | `VmState` enum | { booting, running, paused, sleeping, dead } (lowercase) for reads | confirmed reads use lowercase |
| 9 | `VmUpdateStateEnum` | { Paused, Running } (capitalized) for writes | confirmed; the enum mismatch is real and the 422 error message is informative |
| 10 | `/vm/{id}/ssh_key` | declared returns private key | confirmed: returns full `-----BEGIN OPENSSH PRIVATE KEY-----` block plus port. **clients should treat as a secret** |
| 11 | VM IP format | not specified | observed IPv6 (e.g. ULA / `fd00::/8` style). clients must not assume IPv4 |
| 12 | `GET /vms` ownership scope | implied "vms you own" | actually returns vms from **multiple owner_ids** (a single key sees vms it did not create). probable cause: shared org / fleet. clients should filter by owner_id if they want "mine only" |

## 4. flat-union footgun behavior, empirically confirmed

### `POST /vm/from_commit` (`FromCommitVmRequest`, oneOf 3)

| body sent | status | response | takeaway |
|-----------|--------|----------|----------|
| `{}` | 400 plain text | `Failed to parse the request body as JSON: expected value at line 1 column 2` | misleading: JSON IS valid. the rust untagged-enum deserializer reports the first-branch error. |
| `{"commit_id":"...","ref":"..."}` (two keys) | 400 plain text | `Failed to parse the request body as JSON: expected value at line 1 column 52` | same misleading message. user has no signal that "two keys" is the problem. |
| `{"commit_id":"not-a-uuid"}` | 422 plain text | `Failed to deserialize the JSON body into the target type: commit_id: UUID parsing failed: ...` | informative |
| `{"commit_id":"<valid-but-nonexistent-uuid>"}` | 404 json | `{"error":"commit not found","success":false}` | clean |
| `{"ref":"no-colon-format"}` | 400 json | `{"error":"Invalid ref format. Expected 'repo_name:tag_name'","success":false}` | clean |
| `{"tag_name":"<nonexistent>"}` | 404 json | `{"error":"tag not found","success":false}` | clean (legacy path) |

confirms: the helper must validate before sending. `from_commit({})` and `from_commit({commit_id, ref})` both fail with a misleading 400 plain-text message.

### polymorphic `POST /vm/{vm_or_commit_id}/branch`

empirical: with a non-existent uuid, the response is `{"error":"commit not found","success":false}` regardless of whether the caller intended a vm or a commit. with a valid commit uuid OR a valid vm uuid, the route succeeds (creates a branched vm). there is no client-visible discriminator: the server figures out which kind it is and you can't tell from inspecting the request.

### bare `tag_name` paths (legacy)

`POST /vm/branch/by_tag/{tag_name}` - legacy, dropped from skill writing. `GET /commit_tags/{tag_name}` confirmed to live in a flat namespace separate from `repositories/{name}/tags/{tag_name}`; same string in different namespaces resolves to different things.

## 5. operational gotchas worth surfacing in SKILL.md

1. **VM IP requires `/metadata`, not `/status` or `/vms`.** to list vms with their IPs you need N+1 calls or a wrapper helper.
2. **`labels` field on VM** is undocumented. tolerate it on parse; don't error on unknown fields.
3. **state PATCH returns empty body**. to confirm the new state, follow with a `/status` call. the response is a sync ack only.
4. **`DELETE /commits/{id}` is blocked while VMs descend from the commit.** Commit removal is a user-authorized retention-reduction step, not autonomous cleanup. If a VM descends from the commit, preserve or pause the VM and ask before any termination path; prefer `is_public=false` when the goal is visibility reduction rather than deletion.
5. **`POST /repositories` returns minimal info**. the `description`, `is_public`, `created_at` come back only on subsequent `GET /repositories/{name}`. clients that need the full record should follow the create with a get.
6. **exec stream and exec/stream/attach are streaming endpoints.** they don't fit a one-shot curl pattern; need a streaming http client (`httpx.stream`) to consume incrementally. sync-curl fails (proxy timeouts in our environment, and likely buffering issues anyway).
7. **logs are base64.** `data_b64` field, decode with `base64.b64decode`. binary-safe by design.
8. **`GET /vms` is cross-owner.** filter by `owner_id` if you only want yours.
9. **environment proxy may emit `503 DNS cache overflow`.** these are claude-side, transient, retry. they are NOT vers.sh errors. the skill's helper should retry idempotent reads on this signal up to ~5x with backoff. Destructive calls are not retried unless the user explicitly authorized the removal operation and the caller has checked idempotency.
10. **api key format: `<owner_uuid><64-hex-secret>`.** the first 36 chars (with dashes) ARE the api key id (== owner_id, which appears in responses). only the trailing 64 hex chars are the secret. clients must never log the full key but logging the owner_id alone is fine (it's already in API responses).

## 6. probe coverage

| phase | label-prefix | covered |
|-------|--------------|---------|
| 1 | `AUTH_*` | bad token, missing header, no scheme, wrong-secret-right-uuid |
| 2 | `404_*` | uuid-shaped vs name-shaped 404 across vm, repo, repo_tag, commit_tag, domain, public_repo |
| 3 | `VAL_*` | from_commit oneOf footgun (empty body, two keys, bad uuid, nonexistent uuid, bad ref, legacy nonexistent), bad new_root body, negative integer u32 |
| 4 | `PAGINATE_*`, `LIST_COMMITS_PUBLIC` | spec-bug pagination on /commits + /commits/public |
| 5 | `STATE_*` | state enum casing on lowercase / capitalized / unknown |
| 6 | `LIFECYCLE_*` | new_root → metadata → status → ssh_key → exec → exec env+wd → commit → branch (typed + polymorphic from vm-id + polymorphic from commit-id) → state PATCH (Paused/Running) |
| 7 | `404_VM_BY_NONUUID*` | path-extractor failure |
| 8 | `ENV_*` | full round-trip: get empty → put 2 keys → get echo → put bad keys (dash, too long) → delete each → get empty |
| 9 | `REPO_*` | list, create, dup-create (409), get, invalid-name (400), delete, double-delete (404) |
| 10 | `FAKE_*`, `BRANCH_BY_REF_NONEXIST` | exec/delete/branch with non-existent uuids; reveals the 403-vs-404 wart and the hybrid envelope on by_vm / by_ref |
| 11 | `EXEC_STREAM_LIVE`, `LOGS_AFTER_STREAM` | logs round-trip; stream itself blocked by proxy |
| 12 | `CLEANUP_*` | resource teardown verified clean |

not covered: domain creation, repo fork, repo tag mutation (push tag to a different commit), the `count` query parameter on branch endpoints, `keep_paused` semantics, `wait_boot=false` semantics, `exec/stream/attach` reattach, large-body limits, rate limits.
