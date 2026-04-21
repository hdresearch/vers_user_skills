# use-vers-for-everything

An agent skill for driving the Vers platform (vers.sh). Drop this directory
into a harness that loads `SKILL.md` frontmatter — Claude Code, OpenAI
Codex CLI, Sourcegraph Amp, anything following the `agentskills.io`
convention — and the agent gains rooted disposable Linux VMs with commits,
branching, pause/resume, SSH-over-TLS-443, public URLs, and direct
`/exec` over the API.

## Layout

```
use-vers-for-everything/
├── SKILL.md           # operating skill: engagement pattern, image-baking, worked examples
├── onboarding.md      # first-run auth (shell-auth, dashboard, `vers init`)
├── api-cheatsheet.md  # API contract: every endpoint, in one row each
├── README.md          # this file
└── scripts/
    ├── vers_api.py    # zero-dep Python wrapper
    └── pyproject.toml # basedpyright strict, Python 3.14
```

`SKILL.md` is the load target. `onboarding.md` and `api-cheatsheet.md` are
supplementary docs loaded on demand.

## Install into a harness

The `agentskills.io` convention treats each skill as a directory:

```
<harness-skills-dir>/
└── use-vers-for-everything/
    ├── SKILL.md
    ├── onboarding.md
    ├── api-cheatsheet.md
    └── scripts/...
```

Copy or symlink the directory into the harness's skills path. Common
destinations:

- Claude Code: `~/.claude/skills/use-vers-for-everything/`
- OpenAI Codex CLI: `~/.agents/skills/use-vers-for-everything/`
- Sourcegraph Amp: per harness docs (same shape)

## Scripts

Every script in `scripts/` is a single-file `uv run` target. `uv` is a
hard prerequisite — shebangs pull the right Python and resolve declared
dependencies before running.

Install `uv` once:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Invoke:

```bash
VERS_API_KEY=... uv run skills/use-vers-for-everything/scripts/vers_api.py vms
VERS_API_KEY=... uv run skills/use-vers-for-everything/scripts/vers_api.py new-root --mem 4096 --vcpu 2
```

Type-check locally:

```bash
cd skills/use-vers-for-everything/scripts
uv run --group dev basedpyright
```

The wrapper covers VM lifecycle, commits, commit tags, branching, and the
shell-auth endpoints. Anything it does not cover — `/vm/{id}/exec`,
`/files`, `/logs`, `/domains`, `/env_vars`, repositories,
`branch/by_ref` — is reached by raw HTTPS with the bearer header; see
`api-cheatsheet.md`.

## Authenticating

First run without a key: the skill routes the agent to `onboarding.md`,
which walks the shell-auth flow (email + SSH key, one browser click on a
verification link). Persist the returned key under `umask 077`:

```bash
( umask 077 && printf '%s' "$API_KEY" > ~/.versrc )
```

or export it as `$VERS_API_KEY`. Smoke-test before doing anything else.

## Refreshing

The API surface evolves. When `api-cheatsheet.md` disagrees with live
behavior:

```bash
curl -sS https://docs.vers.sh/api-reference/openapi.json > /tmp/openapi.json
# Diff against the tables in api-cheatsheet.md and update rows in place.
```

Upstream documentation lives at `https://docs.vers.sh/llms-full.txt` and
the OpenAPI spec at `https://docs.vers.sh/api-reference/openapi.json`.

## License

See the repo root.
