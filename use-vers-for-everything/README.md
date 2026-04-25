# use-vers-for-everything v0.2.6

A skill for using vers.sh — remote rooted Linux VMs you can branch,
snapshot, run jobs in, and preserve. Read by LLM agents (Claude,
harness cousins, agent frameworks); humans only encounter this README
during package install.

## Files

```text
use-vers-for-everything/
├── SKILL.md                ← entry point + operating loop + cookbook + anomalies
├── onboarding.md           ← first-run auth (load if no API key)
├── api-cheatsheet.md       ← endpoint reference (load for raw curl/HTTP)
├── patterns.md             ← operational loops (bake/fan-out/repro/ingress)
├── CHANGELOG.md
├── MANIFEST.sha3-256.txt
├── scripts/
│   ├── vers.py             ← typed Python helper + JSON-speaking CLI
│   ├── smoke_test.py       ← offline guard test, no network
│   └── curl_recipes.sh     ← raw-curl fallback / debugging recipes
└── references/
    └── error_shapes.md     ← empirical error envelope catalog
```

## Install

Copy the `use-vers-for-everything/` directory into the path your LLM
harness uses for skills. Examples: claude.ai's `~/.claude/skills/`,
or whatever path your harness documents.

## Offline validation

```bash
cd use-vers-for-everything/scripts
uv run smoke_test.py
```

Offline. Checks imports, typed identifier wrappers, RepoRef parsing,
guard failures that should happen before network, exception
hierarchy, and CLI local validation. Should print 0 failures.

## Live validation

After a real key is present in `$VERS_API_KEY` or `~/.versrc`, follow
`onboarding.md` and then test a tiny VM lifecycle before any real
task.
