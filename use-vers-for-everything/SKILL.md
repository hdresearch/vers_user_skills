---
name: use-vers-for-everything
description: >
  Load when local compute is the wrong place for a task. Examples:
  persistent services, one-off heavy jobs, recurring builds, side
  experiments, matrices of configurations or builds run in parallel,
  middleware test boxes, preserving battery when running on portable
  power, building your own Beowulf cluster, or implementing the
  world's most expensive Prolog logic search possible. Loading does
  not allocate Virtual Machines (VMs). Run the reach gate, keep
  small or sensitive work local, tell the user before creating any
  VM, and preserve work product before ending any VM.
metadata:
  author: Carter Schonwald
  version: 0.2.6
  updated: 2026-04-25
  lineage: "v0.2.5 + preservation hardening against small-model data loss; remove VM termination from LLM CLI surface; require explicit fresh-VM dimensions"
trigger: vers, vers.sh, use vers, remote vm, branch vm, snapshot vm, sandbox code, fanout, bisect, public url, long build, parameter sweep, beowulf, prolog, offload compute
---

# Vers: use-vers-for-everything

## Audience and design constraint

This skill is read by Large Language Model (LLM) agents (Claude,
harness cousins, agent frameworks). It is not read by humans except
during package installation. The design choices throughout
(named-flag Command Line Interface (CLI), JavaScript Object Notation
(JSON) bulk-input form, machine-introspectable schema, irreversibility
discipline) follow from this constraint.

## Standing rules the LLM should apply to its own utterances

**Rule A — patterns may not hardcode task-dependent numbers; examples
may.** Patterns are generic guidance the LLM pattern-matches to.
Hardcoded numbers in patterns leak as defaults. Examples are
illustrative (specific runs documented as such); concrete numbers
there are fine.

**Rule B — irreversible operations on information are stated only via
converse terms.** Preserve, keep, capture — never delete, drop, purge,
deallocate. The converse framing forces honesty about what is being
lost rather than legitimizing the loss as a routine action. The
irreversible operation may still happen, but always as a separate
user-authorized action outside the autonomous loop.

## Task → command quick lookup

| task | command |
|---|---|
| who am I | `uv run vers.py whoami` |
| list my VMs | `uv run vers.py vm list --owner mine` |
| describe a VM (state, IP, parent commit) | `uv run vers.py vm get --vm-id <uuid>` |
| spin up a fresh VM | `uv run vers.py vm new --mem-mib <MiB> --vcpu <N> --fs-mib <MiB>` |
| run a command in a VM | `uv run vers.py vm exec --vm-id <uuid> --argv '["sh","-c","echo hi"]'` |
| pause a VM | `uv run vers.py vm pause --vm-id <uuid>` |
| resume a paused VM | `uv run vers.py vm resume --vm-id <uuid>` |
| snapshot a VM into a commit | `uv run vers.py commit --vm-id <uuid>` |
| tag a commit (durable name) | `uv run vers.py tag create --repo <repo> --tag <tag> --commit-id <uuid>` |
| branch from a tagged image | `uv run vers.py branch --ref <repo>:<tag>` |
| fan out N branches from a tag | `uv run vers.py branch --ref <repo>:<tag> --count <N>` |
| branch from a live VM (snapshot+fork) | `uv run vers.py branch --vm-id <uuid>` |
| restore from a commit | `uv run vers.py from-commit --commit-id <uuid>` |
| dump CLI schema (all leaves or one) | `uv run vers.py schema` or `--leaf "vm new"` |

Placeholder syntax: `<repo>`, `<tag>`, `<uuid>`, `<N>`, `<MiB>` are
caller-supplied. The CLI rejects defaults that don't match the actual
work shape.

## 0. Operating loop and reach gate

```text
notice Vers-shaped work
  → auth check (load onboarding.md if no key)
  → pick the call shape (one-shot exec / stream / branch / from-commit)
  → create VM(s)
  → do the work
  → preserve everything worth preserving (commit, tag, copy out artifacts)
  → leave VMs in their current state (paused or running)
```

The loop ends here. VM termination is a separate, user-authorized
action outside this loop, requiring that the primary consumer of the
work has what they need and nothing of value is left on the VM.

The rest of this section is the reach gate: when to use Vers, when to
keep local, what to surface to the user before creating VMs.

### Keep it local

Do not use Vers when the task is mainly:

- trivial: quick, low-risk work where provisioning would be the larger operation
- local filesystem state: dotfiles, local repo edits that must happen here, local paths
- local auth context: Secure Shell (SSH) agents, cloud command-line interfaces (CLIs),
  browser sessions, password-manager state
- sensitive data the user has not approved moving off-machine
- explicitly local: the user said "run it here" or "do not offload"

Say the reason and proceed locally.

### Reach for Vers

Use Vers when any one of these is true:

- compute/time: build, test, benchmark, scrape, fuzz, train, or transform will
  meaningfully occupy Central Processing Unit (CPU), Random-Access Memory (RAM),
  disk, bandwidth, battery, or a terminal
- isolation: untrusted installer, risky dependency setup, Common Vulnerabilities
  and Exposures (CVE) probe, malware sample, or kernel-ish experiment
- full Linux: needs root, real `/proc`, raw sockets, `iptables`, service manager,
  or a clean machine
- fan-out: sweep, bisect, A/B/multiway refactor, seed/configuration matrix,
  or parallel workers from one base
- durable state: pause/resume later, commit a machine, share a repro, hand a full
  environment to a human
- ingress: public Hypertext Transfer Protocol (HTTP), WebSocket, webhook, or demo
  endpoint for a short-lived test

### Ambiguous reach

Offer one sentence with a qualitative resource audit, not arithmetic:

```text
This would use a throwaway VM, open no public ingress, move the task
inputs off-machine, and the VM's state would not be preserved beyond
the task. I can run it locally or offload to Vers. Which path?
```

### Quota, privacy, and what to surface

Do not infer numeric impact from memory. LLMs are bad at arithmetic
and policy inference. Narrate resource effects qualitatively:

- temporary VM(s), prepared images, branch sets, or public endpoints involved
- whether public ingress is opened
- whether user data or secrets move off-machine
- whether state will be committed or published
- what state will be preserved at the end

On quota or allocation errors: stop, surface the error, do not
silently retry. Quota is a policy signal, not a transient fault.

### First-time auth boundary

If no Vers Application Programming Interface (API) key is reachable in
`$VERS_API_KEY` or `~/.versrc`, load `onboarding.md` before any Vers
action. Do not proceed past auth until the smoke test passes. Do not
log API keys.

### Source authority and verify-when-surprised

Public docs and the public OpenAPI Specification (OAS) at
`docs.vers.sh/api-reference/openapi.json` are the distribution
contract. The empirical anomaly notes in this package are
live-behavior guardrails observed while building the helper.

If public docs and live behavior disagree, treat it as a contract
question or bug, not as lore to silently spread.

If you are surprised by API behavior — unexpected status code,
missing field, new field — refetch the public OAS and treat the live
spec as authoritative. Then flag the divergence rather than working
around it silently.

### Core operating rules

1. Surface allocation before non-trivial work.
2. Prefer `/exec` before SSH: one-shot command uses `/exec`; long
   command uses `/exec/stream`; interactive sessions and large
   transfers use SSH-over-Transport Layer Security (TLS) on port 443.
3. Bake before fan-out: install dependencies once, verify, commit,
   tag, branch from that base.
4. Name durable things with repo-scoped tags (`<repo>:<tag>`).
5. Each VM you created should end with its work product preserved
   (commit, tag, copy out files). Pause is the default end state;
   keep-running is for explicit ongoing services. VM termination is
   a separate user-authorized action and not part of the autonomous
   loop.
6. Bind public services to `::`; Internet Protocol version 4 (IPv4)
   only `0.0.0.0` listeners are not enough.
7. Keep secrets quiet: no API key or private SSH key in logs,
   transcripts, or public commits.

## The two-phase principle

```text
phase 1 (rare, deliberate): prepare a purpose-built image
    new_root → exec install/configure/harden → commit → tag as <repo>:<tag>

phase 2 (frequent, cheap, ephemeral): branch and use
    branch_from(<repo>:<tag>) → exec the actual job → preserve any results worth preserving
```

**Default to two-phase when setup is non-trivial, repeated, or worth
preserving.** For one-off tiny jobs, a fresh `new_root` plus direct
`exec` is fine — don't over-engineer. But the moment you find
yourself doing the same `apt install` (or pip install, or npm
install, or anything taking more than a few seconds) twice, you're
missing a phase 1 commit. Bake it into a tagged image and branch
from there.

Copy-on-Write (CoW) makes phase 2 cheap in the normal case. Branched
VMs come up in `running` state rather than going through a fresh
boot, because the committed machine state is restored.

Use the helper `scripts/vers.py` for normal work. Use
`scripts/curl_recipes.sh` when you want to debug something without
the helper.

The helper and its smoke test both carry Python Enhancement Proposal
723 (PEP 723) inline-script-metadata headers declaring `httpx` (the
only third-party dep) and `requires-python = ">=3.10"`. With `uv`
installed, the only command you need is
`uv run scripts/smoke_test.py` — no pip install, no venv setup, no
requirements.txt. To use the helper from your own script, give your
script a matching PEP 723 header and `uv run` it.

## Mental model

Two object kinds, one verb-set:

- **vm**: live, has IPv6, runs processes, has memory + disk state
- **commit**: frozen snapshot (filesystem + memory) of a vm

Verbs: `commit(vm) -> commit`, `branch(vm|commit|<repo>:<tag>) -> vm`,
`from_commit(commit) -> vm`, `exec(vm, cmd) -> result`.

Naming layer: `<repo>` + `<tag>` (modern, addressable as
`<repo_name>:<tag_name>`). Legacy flat-namespace `commit_tags` exist
but are not exposed by the helper.

State machine (legitimate transitions for the autonomous loop):

```text
new_root with wait_boot=true ──► running ──┬─► branch ──► running (no fresh boot)
                                            ├─► commit ──► commit (frozen)
                                            └─► pause ──► paused ──► resume ──► running
```

Important: `booting` state is reachable ONLY from cold-start
(`new_root` with `wait_boot=false`). A branched VM is never
`booting`. It resumes at the committed machine state rather than
going through a fresh boot. **Reserve "boot" vocabulary for
`new_root`.** For everything else say "resume" or "instantiate".

Termination is omitted from the diagram per Rule B. It is a
user-authorized action outside the autonomous loop.

## First call: curl

```bash
export VERS_API_KEY=...                            # from vers.sh
export V=https://api.vers.sh/api/v1
H="Authorization: Bearer $VERS_API_KEY"

# 1. spin up
vm=$(curl -s -X POST "$V/vm/new_root?wait_boot=true" -H "$H" \
        -H 'content-type: application/json' \
        -d '{"vm_config":{"mem_size_mib":<MiB>,"vcpu_count":<N>,"fs_size_mib":<MiB>}}' \
        | jq -r .vm_id)

# 2. exec
curl -s -X POST "$V/vm/$vm/exec" -H "$H" -H 'content-type: application/json' \
     -d '{"command":["uname","-a"]}' | jq

# 3. commit (this is the preservation step from the operating loop)
commit=$(curl -s -X POST "$V/vm/$vm/commit" -H "$H" | jq -r .commit_id)

# 4. branch from the commit
curl -s -X POST "$V/vm/branch/by_commit/$commit" -H "$H" | jq
```

The `vm` is left in its current state (running). User authorizes any
termination separately.

## First call: helper

```python
from vers import Client, RepoRef

with Client() as c:                                   # reads VERS_API_KEY from env
    vm = c.new_root(mem_mib=<MiB>, vcpu=<N>, fs_mib=<MiB>)
    print(c.exec(vm, ["uname", "-a"]).stdout)
    commit = c.commit(vm)                             # preservation
    branched = c.branch_from(commit)                  # list[VmId]
    # vm and branched are left in their current state.
    # Termination is a separate, user-authorized action.
```

## CLI mode

### Design notes (read first)

The CLI intentionally does not expose VM termination. Explicit user-authorized
VM removal remains available only by direct Python API call (`Client.delete_vm`)
or raw HTTP after a separate confirmation outside the autonomous loop.

- **All-named-flags, no positionals.** Tool-use schemas are named-arg
  by construction; the CLI matches that shape. No "is this the source
  or the destination" ambiguity from order.
- **Structured args go in as JSON.** `vm exec --argv '[...]'` and
  `env set --vars '{...}'` take JSON literals, not bash-style splat
  or `KEY=VAL` syntax. LLMs already produce JSON natively; this is
  the shorter path.
- **Typed dispatch is encoded by mutually-exclusive flag groups, not
  string parsing.** `branch --ref X` vs `branch --vm-id X` vs
  `branch --commit-id X` — argparse enforces "exactly one." No "is
  this string a uuid or a `<repo>:<tag>`" guessing.
- **Errors are JSON envelopes on stderr with stable keys**
  (`error_type`, `status`, `message`, `request`, `raw_body`).
  Programmable.
- **Exit codes are scriptable.** 0 success, 2 API error, 64
  (`EX_USAGE`) for caller-side validation/configuration failures.
- **Output is always JSON on stdout.** Even single values:
  `{"vm_id": "..."}`, `{"paused": "..."}`. No bare strings, no
  human-prose status lines on stdout.
- **The CLI schema is machine-introspectable.** Run
  `uv run vers.py schema` for all leaves, or
  `uv run vers.py schema --leaf "vm new"` for one. Output is a
  JSON-Schema-shaped dict suitable for LLM tool-use harness ingestion.

### Subcommands

```bash
uv run vers.py whoami                                   # owner_id
uv run vers.py vm list --owner mine                     # or --owner all
uv run vers.py vm get --vm-id <uuid>                    # metadata incl. IPv6
uv run vers.py vm new --mem-mib <MiB> --vcpu <N> --fs-mib <MiB> [--wait-boot false]
uv run vers.py vm exec --vm-id <uuid> --argv '["sh","-c","echo hi"]'
uv run vers.py vm logs --vm-id <uuid> [--max-entries <N>] [--offset <N>]
uv run vers.py vm pause --vm-id <uuid>
uv run vers.py vm resume --vm-id <uuid>
uv run vers.py vm ssh-key --vm-id <uuid>

uv run vers.py repo list
uv run vers.py repo get --name <repo>
uv run vers.py repo create --name <repo> [--description "..."]

uv run vers.py tag list --repo <repo>
uv run vers.py tag create --repo <repo> --tag <tag> --commit-id <uuid>

uv run vers.py commit --vm-id <uuid> [--name "..."] [--description "..."]

# branch: typed dispatch via mutually-exclusive source flags
uv run vers.py branch --ref <repo>:<tag> [--count <N>]
uv run vers.py branch --vm-id <uuid> [--count <N>]      # snapshot+fork live vm
uv run vers.py branch --commit-id <uuid> [--count <N>]
# 0 or >1 source flags rejected with helpful message

# from-commit: same dispatch shape, single vm (not branched off the source)
uv run vers.py from-commit --commit-id <uuid>
uv run vers.py from-commit --ref <repo>:<tag>

# env vars (boot-time only on next vm; remember!)
uv run vers.py env list
uv run vers.py env set --vars '{"FOO":"bar","BAZ":"qux"}' [--mode replace]
uv run vers.py env delete --key <key>

uv run vers.py domain list
uv run vers.py domain create --vm-id <uuid> --hostname example.com

uv run vers.py schema                                   # all leaves
uv run vers.py schema --leaf "vm new"                   # one leaf
```

VM termination via the API exists (`DELETE /vm/{id}`) but is not
part of the autonomous operating loop per Rule B. If the user
authorizes it explicitly, the helper supports it via direct Python
usage; the CLI surface is intentionally minimal here so an LLM
doesn't reach for it reflexively.

### `--json` bulk-input form (recommended for LLM callers)

Every leaf subcommand also accepts `--json '<dict>'` as an alternative
to named flags. **JSON keys mirror the flag names in snake_case.**
This collapses LLM tool-use schemas to a uniform shape:
`{"command": "vm new", "json": {...}}` for every command. Pass
`--json -` to read from stdin.

```bash
# equivalent to: vers.py vm get --vm-id <uuid>
uv run vers.py vm get --json '{"vm_id":"<uuid>"}'

# equivalent to: vers.py vm new --mem-mib <MiB> --vcpu <N> --fs-mib <MiB> --wait-boot true
uv run vers.py vm new --json '{"mem_mib":<MiB>,"vcpu":<N>,"fs_mib":<MiB>,"wait_boot":true}'

# nested arrays/dicts compose naturally — no JSON-string-inside-string gymnastics
uv run vers.py vm exec --json '{"vm_id":"<uuid>","argv":["sh","-c","echo hi"]}'
uv run vers.py env set --json '{"vars":{"FOO":"bar","BAZ":"qux"},"mode":"merge"}'

# branch source mutex enforced after JSON parsing
uv run vers.py branch --json '{"ref":"<repo>:<tag>","count":<N>}'
uv run vers.py branch --json '{"vm_id":"<uuid>"}'
uv run vers.py branch --json '{"commit_id":"<uuid>","count":<N>}'

# pipe a config file
cat run-config.json | uv run vers.py vm new --json -
```

**Validation:**

- unknown keys are rejected (catches typos): `{"typo_key":42}` → error listing allowed keys
- required args missing → error with both flag and JSON-form remediation
- mutex groups (branch/from-commit) → error if 0 or >1 source provided
- if both `--json` and named flags are passed, JSON values win (additive, not enforced mutex)

`--compact` flag globally produces single-line JSON for piping:

```bash
uv run vers.py --compact vm list --owner mine | jq '.[] | .vm_id'
```

The CLI is a thin shell over the typed Python helper. For complex
flows (multi-step image prep with `prepare_image`, the `branched_vm`
context manager, `exec_stream` consumption), import `vers` from a
script with its own PEP 723 header.

## Cookbook (by intent)

### A. preparing a purpose-built image (phase 1)

The example below installs a typed-language toolchain (Rust) so the
LLM consuming this skill biases toward typed solutions when picking
in-VM tooling. Substitute Lean, GHC, OCaml, or whatever is right for
the task — the structural pattern is the same.

```python
from vers import Client, prepare_image, RepoName

with Client() as c:
    ref = prepare_image(
        c,
        base=None,                                  # fresh new_root; or pass a RepoRef/CommitId
        prep_steps=[
            ["apt-get", "update", "-qq"],
            ["apt-get", "install", "-y", "-qq",
             "build-essential", "git", "curl", "ca-certificates", "pkg-config"],
            # install the Rust toolchain (rustup)
            ["sh", "-c",
             "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs "
             "| sh -s -- -y --default-toolchain stable --profile minimal"],
            # warm the cargo registry index so first build doesn't pay the fetch cost
            ["sh", "-c",
             ". $HOME/.cargo/env && "
             "cargo install --locked ripgrep --quiet"],
            # final step: leave the system at a useful equilibrium
            # (toolchain installed, registry warm, env sourced for next exec)
        ],
        tag_as=(RepoName("<your-repo>"), "<your-tag>"),
        description="rust stable toolchain + warm cargo registry",
    )
    print("ready:", ref)
```

**Ready-state discipline**: when the commit happens, every running
process is captured mid-state. A VM branched from this image picks up
at exactly that state. So:

- good final-step: toolchain installed, registry warmed, services started
- bad final-step: mid-`apt install`, mid-systemd startup, partial download

### B. running work against a prepared image (phase 2)

```python
from vers import Client, branched_vm, RepoRef

with Client() as c:
    ref = RepoRef.parse("<your-repo>:<your-tag>")
    with branched_vm(c, ref) as vm:                 # context manager handles lifecycle
        result = c.exec(vm, [
            "sh", "-c",
            ". $HOME/.cargo/env && "
            "cargo new --quiet /tmp/demo && cd /tmp/demo && "
            "cargo build --release --quiet && ./target/release/demo"
        ])
        print(result.stdout)
```

`branched_vm` is a context manager so the LLM doesn't have to think
about cleanup imperatively. The exit handler preserves any commits
the user requested and leaves the VM paused.

### C. branching alternatives at a decision point

CoW makes branching the intended workflow. Choose the worker count
explicitly; do not derive it from vague phrases like "try a bunch".

```python
with Client() as c:
    parent_commit = c.commit(some_vm)
    # requested_branch_count is chosen explicitly by the caller or user.
    branches = c.branch_from(parent_commit, count=requested_branch_count)
    results = []
    for vm in branches:
        results.append(c.exec(vm, ["./run_variant.sh"]))
    # preserve interesting branches as commits
    for vm, result in zip(branches, results):
        if result_is_interesting(result):
            c.commit(vm, name=f"interesting-{vm}")
    # branches are left paused. User authorizes any termination.
```

### D. long-running command with reattach

For a command that may outlive your HTTP connection, use
`exec_stream` plus the logs API:

```python
with Client() as c:
    # consume the live stream; if disconnected, fall back to /logs polling
    for chunk in c.exec_stream(vm, ["./long_running.sh"]):
        if chunk.stream == "stdout":
            print(chunk.data.decode("utf-8", errors="replace"), end="")
    # later, even after disconnect:
    page = c.get_logs(vm)
    for entry in page.entries:
        print(entry.timestamp, entry.stream, entry.data)
```

### E. publish a snapshot to share

```python
with Client() as c:
    commit = c.commit(vm)
    repo = c.create_repo(RepoName("<your-repo>"), description="...")
    ref = c.tag(RepoName("<your-repo>"), "<your-tag>", commit)
    print(ref)                                      # <your-repo>:<your-tag>
```

### F. consume someone else's public snapshot

```python
with Client() as c:
    vm = c.from_commit(ref=RepoRef.parse("<org>/<repo>:<tag>"))
    # ... use ...
    # vm is left in its current state. User authorizes any termination.
```

Public refs are obtained out-of-band (vers documentation, the user,
or a successful call to the public-repo discovery surface once it is
verified working in your environment). Do not invent placeholder refs
and assume they exist.

### G. boot-time env vars (for NEW VMs only)

```python
with Client() as c:
    c.set_env({"OPENAI_API_KEY": "sk-...", "ANTHROPIC_API_KEY": "sk-ant-..."})
    # next time you new_root, /etc/environment will have these.
    # existing vms are NOT retroactively updated — env vars are a boot-time concept.
```

## Empirical anomalies (read once, don't memorize)

These are observed divergences between the public OAS contract and
live API behavior. The helper papers over them; if you go raw, mind
the list.

1. **Two state enums, different casing.** Reads return lowercase
   (`running`); writes require capitalized (`Running`, `Paused`). The
   helper handles this; if you go raw, `PATCH /vm/{id}/state` with
   `{"state":"Running"}` not `"running"`.
2. **Two commit schemas in the spec.** `CommitInfo` says `commit_id`
   (string), `VmCommitEntity` says `id` (uuid). Reality uses uuid
   types AND field name `commit_id`. The helper normalizes.
3. **Five branch endpoints + one polymorphic.** Helper exposes only
   one, `branch_from(source)`, dispatched by python type. Never use
   the polymorphic `POST /vm/{vm_or_commit_id}/branch` directly: its
   error messages always say "commit not found" even if you intended
   a vm_id.
4. **`from_commit` is a 3-way `oneOf` body.** The helper only exposes
   `commit=...` and `ref=...` (legacy `tag_name` is intentionally not
   surfaced). If you pass empty body or two keys via raw curl, you
   get a misleading "Failed to parse the request body as JSON" error
   — actually a `oneOf` rejection.
5. **`branch/by_vm` and `branch/by_ref` use a hybrid error
   envelope.** `{"vms":[],"error":"..."}`. Naive deserialization sees
   an empty success and misses the error. The helper detects this
   and raises `VersHybridBranchError`.
6. **`/vms` returns vms across multiple `owner_id`s.** Filter to your
   own with the helper's default `owned_by_me=True`, or by owner_id
   matching your api key prefix in raw curl.
7. **`/vms` does not include IP.** Use `get_vm()` (=
   `/vm/{id}/metadata`) for IPs.
8. **VM IPs are Internet Protocol version 6 (IPv6).** Bind public
   services to `::`, not `0.0.0.0`.
9. **Env vars are boot-time only.** Setting them does not affect
   running VMs.
10. **`DELETE /vm/{id}` returns 200 + body**, not 204. Other deletes
    return 204. (Helper handles; this is documented for raw-curl
    callers operating under explicit user authorization.)
11. **`DELETE /vm/{nonexistent_uuid}` returns 403, not 404.** Helper
    raises `VersForbidden`.
12. **`DELETE /commits/{id}` is blocked while VMs descend from the
    commit.** Branches must be ended first, or the helper's
    explicit-removal flow used, before the commit can be removed.
13. **CoW but not content-addressed.** Two commits made from
    identical filesystem states get distinct uuids and consume
    separate (logical) storage. Vers commits are uuid-addressed
    (server-assigned), not content-addressed (git-style).
14. **CLI aliases are local-only.** `vers alias` operates on a
    client-side `vers.toml`, not the API. There is no API endpoint
    for vm aliases.
15. **`skip_wait_boot` query param** on branch / commit / state /
    disk ops is for chaining ops on a vm that's still finishing its
    cold-boot. Default usage: leave alone.

See `references/error_shapes.md` for the full empirical
error/response shape catalog.

## Error handling

The helper raises typed exceptions for API failures:

```text
VersError                       (base for API failures)
├── VersAuthError              # 403 with no JSON body (auth failed)
├── VersForbidden              # 403 with JSON body
├── VersNotFound               # 404
├── VersConflict               # 409 (caller-side; never retried)
├── VersBadRequest             # 400 with JSON body
├── VersValidationError        # 400/422 with rust-serde plain-text body
├── VersHybridBranchError      # silent branch-envelope failure, detected explicitly
└── VersServerError            # 5xx

VersConfigError                # SEPARATE construction/configuration failure
                               # (NOT a VersError subclass)
VersCliUsageError              # SEPARATE CLI argument-shape failure
                               # (subclass of ValueError)
```

`VersError` instances carry `.status`, `.message`, `.raw_body`,
`.method`, and `.url`. Catch `VersError` for API failures. Catch
`VersConfigError` separately for missing API keys or client
construction problems. The CLI reports config and usage failures as
JSON error envelopes with exit code 64.

## What NOT to do

- **Don't ad-hoc-install in phase 2.** If a script `apt install`s, it
  should be in phase 1.
- **Don't use the polymorphic `/vm/{vm_or_commit_id}/branch` endpoint
  directly.** Use the helper's `branch_from()` so you get a typed
  dispatch and a useful error.
- **Don't pass `tag_name` to `from_commit`** (legacy org-scoped flat
  namespace, shadowed by `<repo>:<tag>`). The helper doesn't expose it.
- **Don't rely on `wait_boot` semantics for branch ops.** Branched
  VMs don't boot.
- **Don't reflexively terminate VMs at the end of a task.** The
  operating loop terminates at "preserved + left in current state."
  Termination is a separate user-authorized action.
- **Don't invent public refs or use placeholder identifiers as if
  they were real.** Obtain public refs out-of-band.

## Supplementary files

Load these on demand:

- `onboarding.md` — first-run auth, shell-auth, persistence, smoke
  test. Load when no API key is present.
- `api-cheatsheet.md` — endpoint contract table derived from the
  public OAS. Load when constructing raw curl/HTTP calls.
- `patterns.md` — operational loops (bake/fan-out/repro/ingress).
  Load when planning a multi-step workflow.
- `scripts/vers.py` — typed Python helper and JSON-speaking CLI.
- `scripts/smoke_test.py` — offline guard test for the helper, no
  network required.
- `scripts/curl_recipes.sh` — raw-curl fallback and debugging
  recipes.
- `references/error_shapes.md` — empirical error/response shape
  catalog. Load when handling unexpected error envelopes.

Before packaging or editing the helper, run:

```bash
cd use-vers-for-everything/scripts
uv run smoke_test.py
```
