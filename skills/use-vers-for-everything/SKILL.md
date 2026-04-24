---
name: use-vers-for-everything
description: >
  Hair-trigger Vers Router. Load early when a task might benefit from remote
  rooted Linux compute, disposable sandboxes, branches, fan-out, long-running jobs,
  public ingress, reproducible machine state, or shareable bug repros. Loading this
  skill is only consideration: first run the reach gate and keep local work local
  (trivial commands, local filesystem edits, local auth/browser sessions, sensitive
  data without approval, or anything the user said to run here). If the gate passes,
  route to the right primitive: VM, branch, commit, repo tag, exec/stream, file,
  domain, env var, pause/resume, cleanup. Public docs/OpenAPI are the shipped
  contract; implementation lore is not skill truth.
metadata:
  author: Carter Schonwald
  version: 3
  updated: 2026-04-24
  changes: |
    v3: Rewritten as Vers Router: hair-trigger load, gated action, entity-first routing.
---

# Vers Router

Load this skill early. Allocate Vers resources only after the gate passes.

Vers is a remote compute substrate: disposable rooted Linux VMs with commits,
branching, pause/resume, SSH-over-TLS-443, public URLs, direct command execution,
files, domains, env vars, repositories, and repo-scoped tags.

The job of this hub is routing: notice Vers-shaped work, reject bad reaches, pick
the primitive, then load the narrow spoke only when needed.

---

## 1. Reach gate

### Keep it local

Do **not** use Vers when the task is mainly:

- trivial: <10 seconds, simple query, no meaningful local resource burn
- local filesystem state: dotfiles, local repo edits that must happen here, local paths
- local auth context: SSH agents, cloud CLIs, browser cookies, password-manager state
- sensitive data that the user has not approved moving off-machine
- explicitly local: user said "run it here" / "do not offload"

Say the reason explicitly and proceed locally.

### Reach for Vers

Use Vers when any one of these is true:

- compute/time: build, test, benchmark, scrape, fuzz, train, or transform will burn CPU, RAM, disk, bandwidth, battery, or a terminal for >~30 s
- isolation: untrusted installer, malware/CVE probe, risky dependency setup, kernel-ish experiment
- full Linux: needs root, real `/proc`, raw sockets, iptables, service manager, or a clean machine
- fan-out: sweep, bisect, A/B/N refactor, many seeds/configs, parallel workers from one base
- durable state: pause/resume later, commit a machine, share a repro, hand a full environment to a human
- ingress: public HTTP/WebSocket/webhook endpoint for a short-lived test or demo

### Ambiguous

Offer one sentence with footprint:

`This looks like ~3 min of CPU and a throwaway VM. I can run it locally or offload to Vers; offload will create one VM and delete it after. Which path?`

---

## 2. Entity map

Keep these entities distinct:

| Entity | What it means | Common mistake |
|---|---|---|
| VM | Live machine; costs while running | Treating it as durable storage |
| Commit | Snapshot of machine state | Forgetting to delete the source VM |
| Branch | New VM(s) from a VM or commit | Confusing git SHA with Vers commit ID |
| Repository | Namespace for reusable commit tags | Using flat legacy tags for shared bases |
| Repo tag | `repo:tag` pointer to a commit | Treating it as a git tag |
| Exec | One command call returning stdout/stderr/exit | Starting SSH for one-shot commands |
| Exec stream | Long-running command stream with reattach | Losing output after a dropped connection |
| File | Base64 read/write through API | Using SSH transfer for tiny payloads |
| Domain / public URL | Inbound route to a VM service | Binding `0.0.0.0`; bind `::` |
| Env var | Account-global injection into VMs | Storing secrets without explicit intent |
| API key / SSH key | Credentials | Logging or persisting carelessly |

---

## 3. Primitive router

| User / agent impulse | Route | Load next |
|---|---|---|
| No API key, stale key, first-time user | Detect/auth/persist/smoke-test | `onboarding.md` |
| "Run this command on a VM" | `/vm/{id}/exec` for short; `/exec/stream` for long | `api-cheatsheet.md` |
| "I need a shell / rsync / interactive session" | SSH-over-TLS using `ssh_key` | `api-reference.md` if details needed |
| "Clean sandbox" | `POST /vm/new_root` | `patterns.md` |
| "Try N things" | Bake once, branch/fan-out N VMs | `patterns.md` |
| "Worktree this" | Branch VM/commit; environment-level worktree | `patterns.md` |
| "Save this state" | Commit; optionally repo-tag | `patterns.md` |
| "Share this repro" | Commit, make public only if intended, share commit ID | `patterns.md` |
| "Resume later" | Pause if soon; commit if durable | `patterns.md` |
| "Public URL / webhook / WebSocket" | Serve on VM, bind `::`, route by VM URL/domain | `patterns.md` |
| "What endpoint/body/response?" | Public contract table | `api-cheatsheet.md` |

---

## 4. Operating rules

1. **Surface allocation.** Say what will be created and what remains after cleanup.
2. **Prefer `/exec` before SSH.** One-shot command → `/exec`; long job → `/exec/stream`; interactive/transfer → SSH.
3. **Bake before fan-out.** Install dependencies once, verify, commit, tag, branch from that base.
4. **Name durable things.** Reusable bases get repo-scoped tags like `bases:rust-buildbox-v1`.
5. **Clean up live VMs.** Delete finished VMs; pause only with user awareness; commit durable state first.
6. **Bind public services to `::`.** The public route needs IPv6 listen, not only `0.0.0.0`.
7. **Do not promote surprises into lore.** Public docs/OpenAPI define skill truth. If live behavior diverges, treat it as a bug/contract question, not a new rule.
8. **Secrets stay quiet.** Never log API keys or SSH private keys; use restrictive file modes and delete temporary keys.

---

## 5. Source authority

- **Shipped skill contract:** public docs and public OpenAPI at `docs.vers.sh`.
- **Operational memory:** sidecar notes can suggest edits but must be verified before promotion.
- **Implementation reading:** useful only to explain correctness-affecting API behavior; do not leak implementation details into user-facing skill prose.
- **Semantic models / mocks:** useful for entity separation and tests; not public API authority.

When uncertain, say what source you used and route to the public contract table before making a call.

---

## 6. Load order

Do not load every file up front. Load the next document only when its condition is met.

1. Start here (`SKILL.md`).
2. If auth is missing, stale, or a smoke test fails: `onboarding.md`. If `$VERS_API_KEY` / `~/.versrc` is already working, skip onboarding.
3. If choosing an operating loop: `patterns.md`.
4. If making an endpoint call: `api-cheatsheet.md`.
5. If wrapper / SSH / call-layer detail is needed: `api-reference.md`.
6. Prefer `scripts/vers_api.py` for supported API operations; use raw HTTPS only for emergency debugging or newly-added endpoints not yet wrapped.

---

## See also

- `onboarding.md` — first-run auth, key persistence, smoke test.
- `patterns.md` — bake, fan-out, repro, public ingress, cleanup loops.
- `api-cheatsheet.md` — public endpoint contract table.
- `api-reference.md` — call-layer guide and wrapper notes.
- `scripts/vers_api.py` — zero-dep Python wrapper invoked via `uv run`.