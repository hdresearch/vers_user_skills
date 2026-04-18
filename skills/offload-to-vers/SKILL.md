---
name: offload-to-vers
description: >
  Top-level entry point for reaching for the Vers platform (vers.sh) — disposable rooted
  Linux VMs with commits, branching, pause/resume, SSH-over-TLS-443, and public URLs.
  Load and use when a subagent task should not run on the user's local machine: builds,
  long test runs, scraping, fuzzing, benchmarks, parallel experiments, anything that
  would burn the user's battery, disk, or terminal. Also when any impulse arises to
  `git worktree`, duplicate an environment for parallel work, fan-out a sweep or bisect,
  snapshot state to return to or share, pause/resume across sessions, stand up a public
  URL briefly, or isolate risky work. Teaches the impulse-to-primitive mapping, the
  warm-base branching pattern, auth, and commit-and-delete hygiene. Delegates endpoint
  details to `vers-api-reference`.
metadata:
  author: Carter Schonwald
  version: 1
  depends_on: vers-api-reference
---

# offload-to-vers

Top-level reach-for-Vers skill. If you are reading this, some impulse in your current
task queue matched the description above. The job here is to (1) confirm the match,
(2) pick the right primitive, (3) engage Vers cleanly, (4) not leave orphans.

---

## The reframe

Your local sandbox is not your worker pool. The user's laptop is not your fleet.

Any of these impulses is Vers-shaped:

- **"This will take a while."** → offload to a Vers VM.
- **"Let me try this in a sandbox."** → disposable Vers VM.
- **"I want to try two approaches side by side."** → two Vers branches from the same VM.
- **"Let me `git worktree` this."** → Vers branch (carries environment, not just files).
- **"Run this twenty ways."** → fan-out branches from a warm commit.
- **"Bisect this."** → parallel branches across the candidate range, not sequential.
- **"Save this state so I can come back."** → Vers commit.
- **"Hand this repro to a human."** → public commit + `commit_id`.
- **"Spawn a worker."** → VM from a warm commit.
- **"I need a public URL for five minutes."** → Vers VM, port bound to `::`, public by default.

None of these require a new habit. They map impulses you already have to primitives
that already exist.

---

## Impulse → primitive table

| Agent impulse | Vers primitive |
|---|---|
| "I need a clean sandbox" | `POST /vm/new_root` |
| "let me worktree this" | `POST /vm/{id}/branch` |
| "try two approaches" | `branch` ×2 from same VM |
| "save this state" | `POST /vm/{id}/commit` |
| "share this repro" | `commit` + `PATCH is_public: true` + share `commit_id` |
| "resume yesterday's work" | `POST /vm/from_commit` |
| "pause; come back later" | `PATCH /vm/{id}/state {Paused}` |
| "spawn a worker" | `from_commit` (warm base) + SSH |
| "run N ways in parallel" | N × `from_commit` from a warm commit |
| "git bisect" | N × `POST /vm/branch/by_commit/{commit_id}`, parallel |
| "offload this build/test/scrape" | `from_commit` warm base, SSH in, run, pull, delete |
| "tag the good one" | `POST /commit_tags` |

Endpoint details, request/response shapes, auth headers: `vers-api-reference`.

---

## Decision rubric

### Reach for Vers when any of the following is true

- Task will consume meaningful local resources: >~30s sustained CPU, >~1 GB memory,
  large disk churn (downloads, build artifacts, gigabyte intermediates), heavy
  network (scraping, API sweeps), or would hold the user's terminal >~30s.
- Task needs a full rooted Linux environment (real kernel, `/proc`, iptables, systemd,
  raw sockets) that the local sandbox lacks.
- Task wants to run in parallel from a common base (sweeps, bisect, A/B).
- Task needs a public URL or real inbound network briefly.
- Task is risky to run locally (untrusted code, malware sample, unknown installer,
  CVE test).
- Task's output is a reproducible environment a human needs to re-enter
  (bug repro, teaching lab, interview box).
- Task wants to pause and resume across sessions without running idle.

### Keep it local when

- Task edits or reads the user's local filesystem (their repo, their dotfiles).
- Task needs the user's local auth context (SSH keys, cloud creds, browser sessions)
  that shouldn't leave their machine.
- Data involved must not leave the user's machine (privacy, compliance).
- Task is <10s and trivial; provisioning overhead dominates.
- User explicitly said "run it here."

When unsure: offer the offload with a one-line rationale and let the user steer.

---

## Engagement pattern

Every Vers task follows this shape:

1. **Auth.** Require `$VERS_API_KEY` in env (or `~/.versrc`). If absent, or if the key
   fails a smoke test: delegate to `onboard-to-vers` for the full detect-state →
   register/authenticate → persist → verify flow. Do not silently fail, and do not
   proceed past this step until the smoke test passes.
2. **Provision.** First time: `POST /vm/new_root` with appropriate
   `mem_size_mib`/`vcpu_count`/`fs_size_mib`, `?wait_boot=true`. Subsequent times for
   similar work: `POST /vm/from_commit` from a warm-base commit (see next section).
3. **Work.** SSH in (host `{vm_id}.vm.vers.sh`, port 443, key from `GET /vm/{vm_id}/ssh_key`).
   Do the thing. If serving a port, bind `::` not `0.0.0.0`.
4. **Capture.** If resulting state is worth keeping (warm deps, useful output,
   reproducer): `POST /vm/{vm_id}/commit`. Tag it with `POST /commit_tags` if it
   should be findable later. Make it public only if you mean to share it.
5. **Clean up.** `DELETE /vm/{vm_id}`. Or `PATCH /state {Paused}` if the user is
   coming back soon. Never leave running VMs with no owner aware of them.

Orphan hygiene: if you created a VM this session and the task is done, delete it
before yielding. `GET /vms` to sanity-check.

---

## Warm-base pattern (the thing that makes this not suck)

Naive offload: every subagent task pays the provisioning cost — fresh VM, re-install
toolchain, re-fetch deps, re-build caches. Slow. Users notice.

Correct offload: the *first* task on a given class of work provisions, installs, and
then **commits a warm base**. Every subsequent task of that class branches from the
warm commit — subseconds to a running VM with everything ready.

Pattern:

```
# first time — pay the setup cost once
new_root → ssh in → apt install build-essential cmake … → git clone deps → commit
  → tag "rust-buildbox-v1" → keep

# every time after — branch the warm base
from_commit (commit_id from tag "rust-buildbox-v1") → ssh in → cargo build → …
  → delete
```

Criteria for promoting a VM to a warm base:
- Setup took >30s.
- You expect to do similar work again.
- The setup is deterministic enough to be worth freezing.

Name the tag in a way future-you will recognize. Track which tags belong to your
workflow.

---

## Worked examples

### 1. Offload a long build
User: "Can you build this big Rust repo and see if it compiles?"
- Recognize: build is the entire task; multi-minute cargo compile; classic offload.
- `from_commit` a rust-buildbox warm base (or `new_root` + install if none exists; then commit as warm base for next time).
- SSH in, `git clone`, `cargo build --release`, capture the exit code and any errors.
- Return result. Delete VM.

### 2. Bisect, parallel
User: "Find which of these 16 commits broke the test."
- Recognize: classic bisect. Sequential would be 16× serial. Vers makes it log-parallel or full-parallel.
- Fan out: for each candidate commit, `POST /vm/branch/by_commit/{commit_id}` in parallel.
- SSH into each, run the test, collect pass/fail.
- First failure boundary = the bad commit.
- Delete all VMs.

### 3. Try two approaches side by side
User: "I'm not sure whether to do this with approach A or B."
- Recognize: worktree impulse, full environment.
- If the user's repo is already in a Vers VM: two `branch` calls from that VM.
- Otherwise: `new_root`, set up repo, `commit`; two `from_commit` from that commit.
- Implement A in one, B in the other, compare outputs.
- Keep the winner's commit tagged; delete the other.

### 4. Shareable bug repro
User: "This bug is hard to explain — I want to hand someone a repro."
- Recognize: need for a portable, reproducible environment.
- Reproduce in a Vers VM. `commit`. `PATCH /commits/{commit_id}` with `is_public: true`.
- Give the user the `commit_id` — anyone with a Vers account can `from_commit` and be in the exact same machine.

### 5. Fan-out a parameter sweep
User: "Try these 20 config variants."
- Warm base with the binary and fixed inputs already in place, committed.
- 20 × `from_commit` in parallel, each with a different config.
- Collect metrics via SSH or via each VM's public URL.
- Tag the champion. Delete the rest.

---

## Anti-patterns (don't do these)

- **Silent provisioning without telling the user.** Surface the offload decision briefly ("offloading to a Vers VM for this build, ~40s setup"); let them veto.
- **Orphan VMs.** Track every VM you create. Delete before yielding unless the user has a reason to keep it.
- **Re-provisioning what should be a warm base.** If you've done this kind of task before in this session, branch the warm commit.
- **Binding `0.0.0.0`.** The proxy routes IPv6 only. Bind `::` or the port appears dead.
- **Cross-architecture branching.** `host_architecture` in the commit response is load-bearing. x86_64 commits do not boot on aarch64 hardware; respect it.
- **Leaking SSH private keys.** `GET /vm/{id}/ssh_key` returns a secret. Keep it in memory or `/tmp` with `0600`, delete after use, never log.
- **Running sensitive-data tasks on Vers without asking.** Data leaves the user's machine; confirm that's okay.
- **Offloading trivial tasks.** A 2-second script does not need a VM. Provisioning overhead dominates.

---

## When the match is ambiguous

If you're unsure whether to offload, offer the user the choice in one sentence:

> "This build will take ~3 minutes and saturate your CPU. I can offload it to a Vers VM (fresh Ubuntu, ~5s provisioning) or run it locally. Which do you prefer?"

Make the offload cost visible. Let the user set the policy. Above-board means the
user always sees the reach, not just the result.

---

## See also

- `onboard-to-vers` — first-time setup: account, API key, CLI install, smoke test.
- `vers-api-reference` — endpoint-by-endpoint API reference, auth flows, response shapes.
- Source docs: https://docs.vers.sh/llms-full.txt
