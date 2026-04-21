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
image-baking pattern, auth, and commit-and-delete hygiene. Delegates endpoint
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
- **"Run this twenty ways."** → fan-out branches from an image commit.
- **"Bisect this."** → parallel branches across the candidate range, not sequential.
- **"Save this state so I can come back."** → Vers commit.
- **"Hand this repro to a human."** → public commit + `commit_id`.
- **"Spawn a worker."** → VM from an image commit.
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
| "spawn a worker" | `from_commit` (image commit) + SSH |
| "run N ways in parallel" | N × `from_commit` from an image commit |
| "git bisect N git SHAs" | bake an image with repo+harness, then N × `POST /vm/branch/by_commit/<image commit_id>`, then `git checkout <sha>` in each VM |
| "offload this build/test/scrape" | `from_commit` an image commit, SSH in, run, pull, delete |
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

### Cost / quota (surface this too)

Every `new_root`, `from_commit`, and running-branch VM has a footprint on the user's
Vers account: compute-minutes while running, disk while stored, and any per-org
quotas the org has set. Surfacing the offload decision (see Anti-patterns) **MUST**
include the footprint when it's non-trivial: "this fan-out spins up 64 VMs in
parallel — ~N compute-minutes, counts against your org's concurrent-VM quota."
If you hit a quota / allocation error, stop, surface to the user, do not silently retry with
backoff. Quota is a policy signal, not a transient fault.

Exact pricing/quota values are not the agent's to remember; the user's Vers
billing page (`https://vers.sh/billing`) is the source of truth. The agent's job
is to name that a cost exists and roughly how much the proposed action consumes.

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
   similar work: `POST /vm/from_commit` from a baked image commit (see next section).
3. **Work.** SSH in (host `{vm_id}.vm.vers.sh`, port 443, key from `GET /vm/{vm_id}/ssh_key`).
   Do the thing. If serving a port, bind `::` not `0.0.0.0`.
4. **Capture.** If resulting state is worth keeping (prepared dependencies, useful output,
   reproducer): `POST /vm/{vm_id}/commit`. Tag it with `POST /commit_tags` if it
   should be findable later. Make it public only if you mean to share it.
5. **Clean up.** `DELETE /vm/{vm_id}`. Or `PATCH /state {Paused}` if the user is
   coming back soon. Never leave running VMs with no owner aware of them.

Orphan hygiene: if you created a VM this session and the task is done, delete it
before yielding. `GET /vms` to sanity-check.

---

## Image-baking pattern

> Vers helps the user work faster, safer, and wider.

Naive offload: every subagent task pays the provisioning cost — fresh VM, re-install
toolchain, re-fetch deps, re-build caches. Slow. Users notice.

Correct offload: the *first* task on a given class of work provisions, updates,
installs, verifies, and then **bakes an image** by committing the resulting VM state.
Every subsequent task of that class branches from that image commit.

Pattern:

```
## first time — bake the image once
new_root → apt upgrade → install deps → verify → commit
  → tag `rust-buildbox-v1` → keep

## every time after — branch the image
from_commit (commit_id from tag `rust-buildbox-v1`) → ssh in → cargo build → …
  → delete
```

> Bake the machine you actually proved out, not the one you imagine rebuilding later.

Bake only after verification. Every baked image should explain itself from inside
the machine, and the machine should carry an honest transcript of how it was baked.
The cleaned-up recipe is secondary; the transcript is the authority.

### Security updates come first

For any image you intend to reuse, the first step is security updates. Rebakes are
therefore expected to differ at the patched package layer. That is honest variance,
not failure. Record it.

### What a reusable image must preserve

- A short in-machine explanation of what the image is for, where it came from, and
  how to continue from it.
- The honest command transcript of the bake, not only a cleaned-up rebuild script.
- Exact realized versions, commit hashes, and artifact hashes for every layered input
  that materially shaped the image.
- The root image / starting commit the bake began from, plus verification commands or
  checks that justified committing it.

Criteria for baking an image:
- Setup took >30s.
- You expect to do similar work again.
- The resulting machine state is worth freezing and branching from repeatedly.

Name the tag in a way future-you will recognize. Track which tags belong to your
workflow.

### Baked image recipes

Concrete image recipes. Each describes what to install, what tag to use, and the
rough setup-cost you're amortizing. Use as templates, not gospel.

**`rust-buildbox-v1`** — Rust compile farm
- Install: `apt-get update -qq`; `DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y -qq`;
  then `apt-get install -y build-essential pkg-config libssl-dev git curl ca-certificates rsync xz-utils time`.
  Then `curl -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable`.
- Cache: optional `sccache` or pre-populated `~/.cargo/registry` if frequent rebuilds.
- Setup cost amortized: ~60–120s.
- Use cases: any `cargo build`, large crates, workspace compiles, CI-equivalent runs.

**`python-datasci-v1`** — Python data/ML scratchpad
- Install: `apt-get update -qq`; `DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y -qq`;
  then `apt-get install -y python3 python3-pip python3-venv git rsync curl ca-certificates` and
  `pip install numpy pandas scikit-learn polars duckdb httpx tqdm pyarrow`.
- Setup cost amortized: ~30–60s.
- Use cases: CSV dedupe (when non-PII), scraping jobs, local data transforms, small-model sweeps.

**`node-devbox-v1`** — Node.js dev/test environment
- Install: `apt-get update -qq`; `DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y -qq`;
  `apt-get install -y curl ca-certificates git rsync`; then
  `curl -fsSL https://deb.nodesource.com/setup_22.x | bash -` and `apt-get install -y nodejs`.
- Setup cost amortized: ~20–40s.
- Use cases: Jest/Mocha suites, TypeScript builds, webpack/vite builds, package installs.

**`forensic-sandbox-v1`** — throwaway rooted Linux for risky code
- Install: nothing beyond security updates unless the investigation requires it.
- Setup cost amortized: ~0 (already close to useful).
- Use cases: untrusted installers, CVE tests, malware detonation, kernel-module tinkering.
  **Do not commit post-work state** — that defeats the forensic contract.

**`webhook-listener-v1`** — public-URL handler sandbox
- Install: `apt-get update -qq`; `DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y -qq`;
  then `apt-get install -y python3 socat curl jq rsync`.
- Setup cost amortized: ~20s.
- Use cases: Stripe/GitHub/Slack webhook testing, short-lived demos, reverse-engineering integrations.

Naming convention: `<purpose>-v<N>`. Bump `vN` on setup changes; keep old versions
until obsolete usage falls off.

When you bake an image: commit, tag, and leave enough evidence in the machine that
the next operator can tell what it is, how it was baked, and what exact inputs landed.


---

## Worked examples

### 1. Offload a long build
User: "Can you build this big Rust repo and see if it compiles?"
- Recognize: build is the entire task; multi-minute cargo compile; classic offload.
- `from_commit` a baked rust image (or `new_root` + install if none exists; then commit that image for next time).
- SSH in, `git clone`, `cargo build --release`, capture the exit code and any errors.
- Return result. Delete VM.

### 2. Bisect, parallel
User: "Find which of these 16 git commits broke the test."
- Recognize: classic bisect. Sequential would be 16× serial. Vers makes it parallel.
- Bake once: `new_root` → clone repo → install build/test deps → verify → `commit` as an image commit.
- Fan out: N × `POST /vm/branch/by_commit/<image commit_id>`, in parallel. `{commit_id}` in this endpoint is the Vers image commit UUID, not a git SHA.
- Inside each VM: `git checkout <candidate git sha>`, run the test, collect pass/fail.
- First failure boundary = the bad git commit.
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

### 5a. Receive a shared repro (other side of #4)
Someone hands you a `commit_id` and says "repro my bug."
- Smoke-test auth per `onboard-to-vers` (reach for its flow if no key).
- `POST /vm/from_commit` with the given `commit_id`, `?wait_boot=true`.
- Optional: `GET /vm/commits/{commit_id}/parents` to see lineage.
- SSH in (or `POST /vm/{vm_id}/exec` once v2 lands). The VM is bit-identical to what
  the reporter had when they committed. Reproduce, diagnose, fix or escalate.
- When done: if you found a fix and want to hand back a "fixed" repro, commit your
  VM, `PATCH is_public: true`, share the new `commit_id`. Otherwise `DELETE /vm/{vm_id}`.
- Anti-pattern: assuming the shared commit is safe to run arbitrary things inside.
  It's rooted Linux with whatever the reporter had; treat secrets/credentials in the
  VM as if they belong to the reporter, and do not commit new state public without
  scrubbing.


### 5. Fan-out a parameter sweep
User: "Try these 20 config variants."
- Bake the image with the binary and fixed inputs already in place, commit it once.
- 20 × `from_commit` in parallel, each with a different config.
- Collect metrics via SSH or via each VM's public URL.
- Tag the champion. Delete the rest.

---

## Anti-patterns (don't do these)

- **Silent provisioning without telling the user.** Surface the offload decision briefly ("offloading to a Vers VM for this build"); let them veto.
- **Orphan VMs.** Track every VM you create. Delete before yielding unless the user has a reason to keep it.
- **Re-provisioning what should be an image.** If you've done this kind of task before in this session, branch the baked image instead of starting from scratch.
- **Kitchen-sink image.** Do not bake every maybe-useful tool, tweak, or one-off dependency into a single image until it gets heavy and blurry.
- **Opaque image.** Do not leave an image without an in-machine explanation, honest bake transcript, and exact realized input versions/hashes.
- **Surprise image.** Do not hide auto-run behavior, silent mutation, or context-sensitive defaults inside the image.
- **Binding `0.0.0.0`.** The proxy routes IPv6 only. Bind `::` or the port appears dead.
- **Leaking SSH private keys.** `GET /vm/{id}/ssh_key` returns a secret. Keep it in memory or `/tmp` with `0600`, delete after use, never log.
- **Running sensitive-data tasks on Vers without asking.** Data leaves the user's machine; confirm that's okay.
- **Offloading trivial tasks.** A 2-second script does not need a VM. Provisioning overhead dominates.

---

## When the match is ambiguous

If you're unsure whether to offload, offer the user the choice in one sentence:

> "This build will take ~3 minutes and saturate your CPU. I can offload it to a Vers VM or run it locally. Which do you prefer?"

State the footprint directly. Let the user set the policy. Above-board means the
user sees what the action allocates and what will remain afterward.

---

## See also

- `onboard-to-vers` — first-time setup: account, API key, CLI install, smoke test.
- `vers-api-reference` — endpoint-by-endpoint API reference, auth flows, response shapes.
- Source docs: https://docs.vers.sh/llms-full.txt
