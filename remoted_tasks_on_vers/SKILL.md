---
name: remoted-tasks-on-vers
description: >
  Use Vers as an agent-subdelegation substrate: one local supervisor farms
  work to planner, codebase-minimap, reviewer, researcher, or execution
  subagents running in Vers VMs. Trigger on subagent, delegate, farm out
  work, remote agents, supervisor/worker, plan+workers, parallel agent
  decomposition, long-running autonomous task, or agent swarm on Vers.
metadata:
  author: Carter Schonwald
  version: 1
  created: 2026-04-29
  lineage: "first operational draft; layered on use-vers-for-everything"
trigger: subagent, delegate, farm out work, remote agents, supervisor, worker agents, planner agent, codebase-minimap on vers, agent swarm, task decomposition
---

# Remoted Tasks on Vers

This skill is for a local Large Language Model (LLM) assistant that wants
to use Vers as the execution substrate for other LLM agents. It layers on
`use-vers-for-everything`: that skill owns VM lifecycle, auth, branching,
exec, preservation, and Vers API details. This skill owns **agent task
topology**: supervisor, planner, workers, envelopes, collection, and merge.

The default topology is:

```text
local supervisor
  ├─ planner agent on Vers      -> decomposes task, returns worker plan
  ├─ worker agent 1 on Vers     -> executes shard A
  ├─ worker agent 2 on Vers     -> executes shard B
  └─ optional reviewer on Vers  -> audits merged result
```

## Non-negotiable entity separation

- **Supervisor**: the local assistant session. It owns user intent, task
  decomposition policy, secrets boundary, final merge, and what to tell the
  user. It may run on Carter's machine or in any harness, but this skill is
  written for the user-local supervisor case.
- **Planner**: a remote subagent asked to produce a plan. It does not edit
  the user's local working tree. Its output is a structured plan artifact.
- **Worker**: a remote subagent asked to execute one bounded shard. It gets
  explicit inputs and returns an artifact/report envelope.
- **Vers VM**: machine substrate. A VM is not an agent; an agent process may
  run inside it.
- **Vers commit/tag**: reusable machine state. A commit is not a git commit.
- **Harness adapter**: the command inside the VM that actually invokes the
  desired agent model/tool. This skill does not assume one universal agent
  binary exists.

Do not blur these. Bad merges come from treating worker reports as truth,
treating VM state as a deliverable, or letting the planner become the user.

## When to use this skill

Use this skill when the task benefits from one or more of:

- parallel independent investigation or implementation shards
- a planning round that should be isolated from the supervisor's current
  working context
- codebase minimap / review / research agents that can run away from the
  user's local machine
- long-running autonomous work that should survive local terminal churn
- sandboxed agent execution with a preservable VM transcript
- fan-out from one prepared agent image into many independent workers

Keep work local when:

- the task is one-file/trivial and remote setup dominates
- inputs are sensitive and the user has not approved moving them to Vers
- local auth/browser/SSH/password-manager state is required
- the worker must mutate the local working tree directly
- the needed harness adapter is not installed in the chosen image

If ambiguous, say the qualitative footprint before allocation:

```text
This would create a planner VM and two worker VMs from <repo>:<tag>, move
the task input and selected files to Vers, preserve reports as artifacts,
and leave VMs paused. I can do it locally or offload the subagents to Vers.
```

## Preconditions

Before using this skill:

1. Load `use-vers-for-everything` for Vers auth, VM lifecycle, branching,
   preservation, and `scripts/vers.py`.
2. Confirm `$VERS_API_KEY` or `~/.versrc` works. If not, load that skill's
   `onboarding.md`.
3. Select an **agent-ready image** by repo-scoped ref, e.g.
   `<repo>:<tag>`. Do not invent refs.
4. Confirm the image has a harness adapter command matching the contract
   below.

## Agent-ready image contract

A reusable image is agent-ready when it exposes one command inside the VM:

```text
/opt/vers-agent/run-agent --input /work/inbox/assignment.json --output /work/out/report.json
```

The concrete implementation may wrap Claude, Codex, OpenCode, a local model,
or a Vers-hosted supervisor model. The command boundary is stable even when
the model substrate changes.

Input is JSON. Output is JSON. Logs may be verbose, but the report path must
exist on success and failure. If this command is absent, the supervisor must
either bake a new image or use `use-vers-for-everything` directly; do not
pretend subdelegation is available.

### Assignment envelope

```json
{
  "schema": "vers.subdelegation.assignment.v1",
  "run_id": "20260429T1157Z-short-name",
  "role": "planner | codebase-minimap | worker | reviewer | researcher",
  "objective": "one sentence",
  "context": "shared background the subagent needs",
  "inputs": [
    {"kind": "file", "path": "/work/input/repo.tar.zst", "note": "optional"},
    {"kind": "text", "name": "requirements", "content": "..."}
  ],
  "constraints": [
    "Do not mutate /work/input; write only under /work/out",
    "Return citations as file paths and line ranges when possible"
  ],
  "expected_output": {
    "format": "json",
    "path": "/work/out/report.json"
  }
}
```

### Report envelope

```json
{
  "schema": "vers.subdelegation.report.v1",
  "run_id": "20260429T1157Z-short-name",
  "role": "worker",
  "status": "completed | blocked | failed",
  "summary": "short human-readable result",
  "artifacts": [
    {"path": "/work/out/patch.diff", "kind": "patch", "sha256": "..."},
    {"path": "/work/out/notes.md", "kind": "notes", "sha256": "..."}
  ],
  "findings": [
    {"claim": "...", "evidence": [{"path": "src/x.py", "lines": "10-30"}]}
  ],
  "open_questions": [],
  "next_actions": []
}
```

Workers should report `blocked` rather than fabricate progress. Failed tool
runs are data; include stderr/log artifact paths, not just prose.

## Operating loop

```text
notice remote-agent-shaped work
  -> load use-vers-for-everything and pass its reach gate
  -> choose agent-ready image ref
  -> prepare input bundle and assignment envelope
  -> run planner agent if decomposition is non-trivial
  -> validate planner output locally
  -> branch one worker VM per independent shard
  -> write each assignment envelope into its VM
  -> run harness adapter via /exec or /exec/stream
  -> fetch report envelopes and referenced artifacts
  -> verify schema + hashes + cited paths
  -> merge locally; optionally send merge to reviewer agent
  -> preserve interesting VM states as commits/tags
  -> pause VMs; report what exists and where
```

The local supervisor remains accountable for the final answer. Subagents are
parallel witnesses and executors, not authorities.

## Planner use

Use a planner when any of these are true:

- the work naturally decomposes into three or more steps
- file ownership or dependency order is unclear
- different agent roles are useful (minimap, executor, reviewer)
- the supervisor wants a second model/substrate to propose task boundaries

Planner output must be constrained to a plan schema, not free-form advice:

```json
{
  "schema": "vers.subdelegation.plan.v1",
  "summary": "...",
  "shared_context": "...",
  "tasks": [
    {
      "id": "worker-a",
      "role": "worker",
      "objective": "...",
      "inputs": ["repo.tar.zst", "requirements"],
      "depends_on": [],
      "acceptance": ["observable result"]
    }
  ],
  "merge_strategy": "how supervisor should combine reports",
  "risks": []
}
```

Validate the plan before launching workers: no circular dependencies, no
worker has vague ownership, no worker requires unavailable secrets, and no
worker is asked to edit the same files as another unless the merge strategy
is explicit.

## Worker fan-out rules

- One worker owns one shard. If two workers need the same file, make one
  read-only or split by line/range/concern.
- Workers may inspect broad context, but acceptance criteria must be narrow.
- Workers write under `/work/out/<worker-id>/`.
- Workers never write to `/work/input/`.
- Workers never decide to allocate more VMs unless explicitly assigned a
  nested supervisor role.
- Use `/exec/stream` for long agent runs so the supervisor can reattach via
  logs.
- Preserve reports and diffs before pausing any VM.

## First working example: planner + two workers

Use case: ask remote agents to inspect a repository snapshot and return two
independent notes, without mutating the local repo.

### 0. Supervisor statement

```text
I will create one planner VM and two worker VMs from <agent-image-ref>.
The repo snapshot and task prompt will move to Vers. Workers will write
reports only; no local files will be changed by the remote agents. VMs will
be left paused after reports are collected.
```

### 1. Input bundle

Create a repository snapshot locally, excluding `.git`, caches, secrets, and
large build outputs. Put it in the planner and worker VMs as:

```text
/work/input/repo.tar.zst
/work/input/task.md
```

### 2. Planner assignment

```json
{
  "schema": "vers.subdelegation.assignment.v1",
  "run_id": "demo-plan-two-workers",
  "role": "planner",
  "objective": "Split a repository inspection into two independent worker tasks.",
  "context": "The supervisor wants a concise repository understanding pass. Workers must not edit files.",
  "inputs": [
    {"kind": "file", "path": "/work/input/repo.tar.zst"},
    {"kind": "text", "name": "task", "content": "Find project structure and identify likely entry points."}
  ],
  "constraints": [
    "Return exactly two worker tasks",
    "Each worker task must be independently executable",
    "No worker may mutate the repo"
  ],
  "expected_output": {"format": "json", "path": "/work/out/report.json"}
}
```

Expected planner tasks:

```text
worker-a: codebase-minimap pass over tree shape, package files, entry points
worker-b: targeted grep/read pass for command surfaces, docs, tests, scripts
```

### 3. Worker A assignment

```json
{
  "schema": "vers.subdelegation.assignment.v1",
  "run_id": "demo-plan-two-workers",
  "role": "codebase-minimap",
  "objective": "Map repository structure and likely entry points.",
  "context": "Read-only repository inspection. Prefer file paths and concise bullets.",
  "inputs": [{"kind": "file", "path": "/work/input/repo.tar.zst"}],
  "constraints": ["Do not edit files", "Cite paths"],
  "expected_output": {"format": "json", "path": "/work/out/worker-a/report.json"}
}
```

### 4. Worker B assignment

```json
{
  "schema": "vers.subdelegation.assignment.v1",
  "run_id": "demo-plan-two-workers",
  "role": "researcher",
  "objective": "Find command surfaces, tests, and docs relevant to running the project.",
  "context": "Read-only repository inspection. Use content search after tree inspection.",
  "inputs": [{"kind": "file", "path": "/work/input/repo.tar.zst"}],
  "constraints": ["Do not edit files", "Cite paths and command names"],
  "expected_output": {"format": "json", "path": "/work/out/worker-b/report.json"}
}
```

### 5. Supervisor merge

The supervisor fetches both reports and produces one local answer with:

```text
- structure map from worker-a
- run/test/doc surfaces from worker-b
- conflicts or gaps called out explicitly
- VM ids, report paths, and preserved commit/tag refs if any VM was committed
```

Do not hide disagreement. If workers conflict, quote both claims and inspect
the source locally or launch a reviewer agent.

## Script direction

This skill expects small scripts to make the envelopes and VM I/O boring:

- `scripts/subdelegate.py plan` — send one planner assignment to an agent VM
- `scripts/subdelegate.py fanout` — branch N workers, write assignments, run
  adapter, fetch reports
- `scripts/subdelegate.py collect` — validate reports, fetch artifacts, print
  merge manifest
- `scripts/subdelegate.py schema` — emit assignment/report JSON schemas

Until those exist, use `use-vers-for-everything/scripts/vers.py` directly and
keep every step explicit. Do not cite these script names as available unless
they exist in this skill directory.

## Preservation and end state

For every VM created by this skill, end with a manifest:

```json
{
  "run_id": "...",
  "planner_vm": "uuid-or-null",
  "worker_vms": ["uuid"],
  "reports_collected": ["local/path/or/vm/path"],
  "commits": [],
  "tags": [],
  "left_state": "paused | running"
}
```

Pause is the default end state. Commit/tag only VMs with state worth
preserving: a useful prepared image, a reproducer, an interesting branch, or
a long-running agent state the user wants to resume. VM termination is a
separate explicit user-authorized action handled outside this autonomous loop.

## Anti-patterns

- Do not call raw VM fan-out an "agent swarm" unless an agent harness is
  actually running in each VM.
- Do not let a planner allocate resources. The supervisor allocates after
  validating the plan.
- Do not let workers rewrite the task. They may report blockers and suggest
  follow-ups.
- Do not move secrets or local auth state to Vers by accident.
- Do not use one shared writable checkout for multiple workers.
- Do not merge remote patches without local review.
- Do not treat remote reports as proof without cited evidence or artifacts.
- Do not restore reef/fleet/lieutenant vocabulary. This skill uses
  supervisor/planner/worker because those are operational roles, not a
  separate product ontology.

## Load on demand

- Load `use-vers-for-everything/SKILL.md` for Vers lifecycle decisions.
- Load `use-vers-for-everything/patterns.md` before multi-VM allocation.
- Load the chosen agent-ready image's README or manifest before assuming an
  adapter command exists.
- Load future `scripts/subdelegate.py schema` output when validating envelopes.