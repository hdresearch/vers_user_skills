---
name: use-vers-for-everything:patterns
description: >
  Operational patterns for Vers Router. Load after the SKILL.md reach gate passes
  and before allocating VMs for common loops: bake a reusable base, fan out a sweep
  or bisect, create a shareable repro, run a public webhook/WebSocket endpoint,
  move files, pause/resume, and clean up. Public API call details live in
  api-cheatsheet.md; first-run auth lives in onboarding.md.
metadata:
  author: Carter Schonwald
  version: 1
  created: 2026-04-24
---

# Vers Operating Patterns

This file is about loops, not endpoint schemas. Use `api-cheatsheet.md` for exact
paths/bodies/responses.

Invariant for every pattern: **name what you allocate, verify what happened, clean
up live VMs unless the user explicitly keeps them.**

---

## Pattern 1 — Bake a base, then branch from it

Use when setup is more expensive than the actual job, or many jobs share one base.

Loop:

1. `new_root` with enough CPU/RAM/disk.
2. Install security updates and dependencies.
3. Put a short README/transcript inside the VM: purpose, commands run, important versions.
4. Verify the base works with a small command/test.
5. Commit the VM.
6. Create/update a repo-scoped tag, e.g. `bases:rust-buildbox-v1`.
7. Delete or pause the source VM.

Rules:

- Bake the machine you verified, not the machine you imagine recreating later.
- Use repo-scoped tags for reusable bases; legacy flat commit tags are only for quick personal scratch.
- Bump the tag version when setup meaningfully changes.

---

## Pattern 2 — Fan out work

Use for sweeps, bisects, A/B/N refactors, test matrices, seeds, benchmark variants.

Loop:

1. Start from a committed base or a live VM branch point.
2. Branch `N` VMs.
3. Give each VM one variant: git SHA, config, seed, patch, workload.
4. Run via `/exec` for short commands or `/exec/stream` for long jobs.
5. Collect structured result: variant id, exit code, metric, logs/artifact path.
6. Keep only winner/interesting commits; delete the rest.

Say the footprint before starting: `This will create N VMs from base X and delete them after collecting results.`

---

## Pattern 3 — Parallel bisect

Do not run a sequential local `git bisect` when candidates can be tested independently.

Loop:

1. Bake a base with repo + test harness + dependencies.
2. Branch one VM per candidate git SHA, or per search tranche.
3. Inside each VM: `git checkout <candidate-sha>` then run the target test.
4. Record pass/fail keyed by git SHA.
5. Identify the first bad boundary.
6. Delete worker VMs; optionally commit/tag a reproducer VM at the bad SHA.

Entity warning: a git SHA is not a Vers commit ID. A Vers commit IDs a machine snapshot.

---

## Pattern 4 — Shareable repro

Use when a human should enter the same machine state, not just read steps.

Loop:

1. Reproduce in a VM.
2. Remove secrets and irrelevant user data.
3. Commit the VM.
4. If sharing outside the account/org, explicitly make the commit public.
5. Give the user the commit ID and a one-line restore instruction.
6. Delete the live VM unless it is still needed.

Do not make a commit public by accident. Public state is a deliberate publication step.

---

## Pattern 5 — Public ingress / webhook / WebSocket

Use when the outside world must reach a temporary service.

Loop:

1. Create or restore a VM with the service runtime.
2. Run the service bound to `::`, not only `0.0.0.0`.
3. Use `https://{vm_id}.vm.vers.sh:{port}` for HTTP.
4. Use `wss://{vm_id}.vm.vers.sh:{port}` for WebSocket if the service supports it.
5. Keep the VM only for the promised test window.
6. Delete the VM or pause it with the user aware.

Failure first check: if public URL is unreachable, verify bind address before reprovisioning.

---

## Pattern 6 — Command execution choice

Choose the least stateful channel that fits.

| Need | Use |
|---|---|
| one command, bounded output | `/vm/{id}/exec` |
| long command, reconnectable output | `/vm/{id}/exec/stream` + `/exec/stream/attach` |
| interactive shell | SSH-over-TLS |
| bulk transfer / rsync / scp | SSH-over-TLS |
| tiny file payload | `/vm/{id}/files` |
| accumulated command logs | `/vm/{id}/logs` |

Prefer `/exec` over SSH until a real session or transfer is needed.

---

## Pattern 7 — File movement

Small file:

1. Base64 encode locally.
2. `PUT /vm/{id}/files` with path, content, mode, create_dirs.
3. Verify with `GET /vm/{id}/files` or an `/exec` checksum.

Large tree:

1. Fetch VM SSH key into a temp file with `0600`.
2. Use SSH-over-TLS as the transport for `rsync`/`scp`.
3. Verify counts/checksums.
4. Delete temp key.

Never log private key material.

---

## Pattern 8 — Pause, commit, delete

Pick exactly one retention story for each VM:

| State | Use when | Cost / consequence |
|---|---|---|
| Delete VM | work complete, no live state needed | cheapest; commits remain |
| Pause VM | user will resume soon and wants RAM/process state | still allocated as paused state |
| Commit | durable filesystem state matters | reusable/restorable snapshot |
| Public commit | someone else should restore it | publication; scrub first |

End-of-session ritual:

1. List VMs.
2. For each VM you created: delete, pause, or commit+delete.
3. Report what remains.

---

## Pattern 9 — Quota / allocation failure

Quota is a policy signal, not a transient network hiccup.

When allocation fails:

1. Stop the fan-out.
2. Report requested footprint and observed failure.
3. Offer smaller `N`, smaller VM shape, reuse existing base, or local execution.
4. Do not silently retry in a loop.

---

## Pattern 10 — Source discipline

Use public docs/OpenAPI for user-facing contract. If implementation reading reveals a
correctness-affecting API behavior, convert it into public-contract language or file a
bug/contract question. Do not teach implementation internals as user knowledge.
