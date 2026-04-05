# Verse Swarm Examples

Practical examples for orchestrating multi-agent swarms on Verse VMs.

---

## Files

### `swarm_harness.py`

Production-quality swarm coordinator with:
- **Task queue management** — parallel execution with dependency tracking
- **Progress monitoring** — rich terminal UI with live updates
- **Verification** — output markers for task completion detection
- **Checkpointing** — snapshot all lieutenants' state for recovery

**Usage:**

```bash
# Show dashboard of all lieutenants
uv run examples/swarm_harness.py dashboard

# Execute workflow from config
uv run examples/swarm_harness.py run examples/workflow_example.json

# Create checkpoint (snapshot all VMs)
uv run examples/swarm_harness.py checkpoint
```

### `workflow_example.json`

Example workflow config showing:
- Parallel task execution (backend, frontend, infra start simultaneously)
- Dependency management (auth depends on database, UI depends on auth)
- Output markers for completion detection
- Multi-phase coordination

**Structure:**

```json
{
  "name": "workflow-name",
  "tasks": [
    {
      "id": "unique-task-id",
      "lieutenant": "lieutenant-name",
      "description": "Task prompt to send",
      "dependencies": ["other-task-id"],
      "output_marker": "STRING_TO_SEARCH_FOR"
    }
  ]
}
```

---

## Creating Your Own Workflow

### 1. Define Tasks

Break your project into independent units that can run in parallel:

```json
{
  "name": "my-project",
  "tasks": [
    {
      "id": "task-1",
      "lieutenant": "backend",
      "description": "Setup FastAPI with hello world endpoint",
      "dependencies": [],
      "output_marker": "SETUP_COMPLETE"
    }
  ]
}
```

### 2. Add Dependencies

Order tasks using dependency chains:

```json
{
  "id": "task-2",
  "lieutenant": "backend",
  "description": "Add database models",
  "dependencies": ["task-1"],  // Runs after task-1
  "output_marker": "MODELS_READY"
}
```

### 3. Use Output Markers

Tell your lieutenants to emit markers when done:

```
"description": "Setup FastAPI... When complete, echo 'SETUP_COMPLETE' to signal completion."
```

The harness polls output looking for this string.

### 4. Execute

```bash
uv run examples/swarm_harness.py run my-workflow.json
```

---

## Best Practices

### Task Granularity

**Too coarse:**
```json
{"description": "Build entire backend with auth, database, API, tests"}
```

**Good:**
```json
[
  {"description": "Setup project structure"},
  {"description": "Add database models", "dependencies": ["setup"]},
  {"description": "Add auth endpoints", "dependencies": ["database"]},
  {"description": "Write tests", "dependencies": ["auth"]}
]
```

### Clear Output Markers

**Ambiguous:**
```json
{"output_marker": "done"}  // Too common in logs
```

**Clear:**
```json
{"output_marker": "BACKEND_AUTH_ENDPOINTS_READY"}  // Unique, searchable
```

### Dependency Management

**Linear (slow):**
```
task-1 → task-2 → task-3 → task-4
```

**Parallel (fast):**
```
           ┌→ task-2 ┐
task-1 ────┼→ task-3 ┼──→ task-5
           └→ task-4 ┘
```

### Error Handling

Add verification tasks at the end:

```json
{
  "id": "verify",
  "lieutenant": "infra",
  "description": "Run integration tests. Check all services are running. Verify API endpoints return expected responses.",
  "dependencies": ["all-other-tasks"],
  "output_marker": "VERIFICATION_COMPLETE"
}
```

---

## Advanced Patterns

### Pattern 1: Experiment Branching

```bash
# Checkpoint current state
uv run examples/swarm_harness.py checkpoint

# Extract commit ID
COMMIT=$(cat ~/.vers/checkpoints/latest/backend.json | jq -r '.commit.id')

# Create experimental branch
uv run scripts/lt.py lt-create backend-experiment "try alternative approach" $COMMIT

# Add to workflow
{
  "id": "compare-approaches",
  "lieutenant": "backend-experiment",
  "description": "Implement same feature using Django instead of FastAPI",
  "dependencies": ["backend-main-implementation"]
}
```

### Pattern 2: Staged Rollout

```json
{
  "tasks": [
    {"id": "phase-1-setup", "dependencies": []},
    {"id": "phase-1-verify", "dependencies": ["phase-1-setup"]},
    {"id": "phase-2-features", "dependencies": ["phase-1-verify"]},
    {"id": "phase-2-verify", "dependencies": ["phase-2-features"]},
    {"id": "phase-3-integration", "dependencies": ["phase-2-verify"]}
  ]
}
```

### Pattern 3: Hierarchical Coordination

```bash
# Coordinator workflow
{
  "id": "backend-lead",
  "lieutenant": "backend-coordinator",
  "description": "Coordinate backend team: assign auth to backend-1, assign API to backend-2, merge results",
  "dependencies": ["planning"]
}

# Sub-workflows run by lieutenants themselves
```

---

## Monitoring

### Live Dashboard

```bash
# Terminal 1: Run workflow
uv run examples/swarm_harness.py run workflow.json

# Terminal 2: Monitor specific lieutenant
uv run scripts/lt.py lt-read backend --follow

# Terminal 3: Watch all status
watch -n 5 "uv run scripts/lt.py lt-status --probe"
```

### Checkpointing

Snapshot state at key milestones:

```bash
# After phase 1
uv run examples/swarm_harness.py checkpoint

# After phase 2
uv run examples/swarm_harness.py checkpoint

# Restore from checkpoint
COMMIT=$(cat ~/.vers/checkpoints/20260403T143000/backend.json | jq -r '.commit.id')
uv run scripts/lt.py lt-create backend-restored "continue from checkpoint" $COMMIT
```

---

## Troubleshooting

### Task Stuck "Running"

```bash
# Check output
uv run scripts/lt.py lt-read <lieutenant> --tail 100

# Check if marker is wrong
grep "output_marker_string" <(uv run scripts/lt.py lt-read <lieutenant> --tail 200)

# Manually mark complete (edit workflow state or destroy/restart)
```

### Dependency Deadlock

If workflow reports unmet dependencies that look circular:

```bash
# Review dependencies in config
jq '.tasks[] | {id, dependencies}' workflow.json

# Draw dependency graph
jq -r '.tasks[] | "\(.id) -> \(.dependencies | join(", "))"' workflow.json
```

### Lieutenant Not Responding

```bash
# Probe actual state
uv run scripts/lt.py lt-status --probe

# If dead, recreate
uv run scripts/lt.py lt-destroy <name>
uv run scripts/lt.py lt-create <name> "<role>" $GOLDEN_COMMIT_ID
```

---

## Next Steps

1. **Test simple workflow**: Run `workflow_example.json` with 2-3 lieutenants
2. **Customize**: Edit example to match your project structure
3. **Scale up**: Add more lieutenants and parallel tasks
4. **Iterate**: Refine task granularity and dependencies

---

## References

- **Main Guide**: `/Users/carter/local_dev/scratch_space/verse_swarm_setup.md`
- **Scripts**: `../scripts/`
- **Skills**: `../skills/`
- **Verse Docs**: https://docs.vers.sh/llms.txt
