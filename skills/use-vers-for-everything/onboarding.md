---
name: use-vers-for-everything:onboarding
description: >
  First-time setup for the Vers platform (vers.sh). Supplementary doc of the
  `use-vers-for-everything` skill. Load when no Vers API key is present, when
  the user has no Vers account yet, when the `vers` CLI is not installed, or
  when an agent needs its own credentials distinct from the user's primary
  account. Owns the detect-state, shell-auth, persist, verify flow. Shell-auth
  request/response shapes are inline with the steps that use them.
metadata:
  author: Carter Schonwald
  version: 2
  source: https://docs.vers.sh/start-here/setting-up-cli
---

# Onboarding to Vers

This doc owns first-run auth. Nothing else in the skill set works without it.

The successful exit state is three things together:

- A Vers account (created or existing).
- An API key reachable as `$VERS_API_KEY` or at `~/.versrc` (mode `0600`).
- A smoke test that actually returned `200` against the live API.

Onboarding is a one-time cost per user per machine (or once per agent identity).
On a machine where all three conditions already hold, detect state and exit.

---

## Detect state first

Before any new API call, check what already exists. Re-onboarding a working
machine wastes a verification email and corrupts the audit trail.

```bash
# 1. Is a key in the environment?
[ -n "$VERS_API_KEY" ] && echo "env key present"

# 2. Is a key persisted for the CLI?
[ -f ~/.versrc ] && echo "versrc present"

# 3. Is the CLI installed?
command -v vers >/dev/null && vers --version

# 4. Smoke-test any key you found.
curl -sS -H "Authorization: Bearer ${VERS_API_KEY:-$(cat ~/.versrc 2>/dev/null)}" \
  https://api.vers.sh/api/v1/vms | head -c 200
# Expect a JSON array (possibly empty). 401 means the key is dead.
```

Outcomes:

- **Env key or `~/.versrc` present and smoke passes** → stop here, return
  success, do not onboard.
- **Nothing present** → run the fast path below.
- **Key present but smoke fails `401`/`403`** → the key is stale, revoked, or
  for the wrong org. Treat as no key. Ask the user before re-onboarding: a
  stale `~/.versrc` usually means something they used to rely on.

---

## Fast path — programmatic shell-auth

This is the agent-preferred route. No browser except the one click on the
verification email. Works headless on a server once the link is clicked.

The flow is three API calls plus a persist step. The user's only manual step
is clicking the link in their inbox.

### Step 0 — Prepare an SSH key

Shell-auth binds one API key to one SSH public key. Use an existing key or
generate a dedicated agent key. A fresh key is the right default when the
caller is an agent: revoking it later is surgical.

```bash
# Use an existing key
PUBKEY=$(cat ~/.ssh/id_ed25519.pub)

# Or generate a dedicated agent key
ssh-keygen -t ed25519 -f ~/.ssh/vers_agent -N "" -C "vers-agent@$(hostname)"
PUBKEY=$(cat ~/.ssh/vers_agent.pub)
```

Each SSH public key is uniquely bound to one Vers account. If the key is
already registered somewhere else, Step 1 returns `409` — generate a fresh
one, do not try to reuse.

### Step 1 — Initiate

```bash
EMAIL="you@example.com"
curl -sS -X POST https://vers.sh/api/shell-auth \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"ssh_public_key\":\"$PUBKEY\"}"
# → { "is_new_user": bool, "nonce": "...", ... }
```

For new emails this creates the account and provisions an organization + free
subscription on verification. For existing users the SSH key is registered
against the existing account.

Then tell the user, verbatim: *"Check your inbox for a verification email from
Vers and click the link."* Do not guess at their address; do not read it from
git config without asking.

### Step 2 — Poll until verified

The user clicks the link. Your job is to notice they did.

```bash
DEADLINE=$(( $(date +%s) + 600 ))   # 10 min to click
while :; do
  HTTP=$(curl -sS -o /tmp/vers_verify.json -w '%{http_code}' \
    -X POST https://vers.sh/api/shell-auth/verify-key \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"ssh_public_key\":\"$PUBKEY\"}")
  case "$HTTP" in
    200)
      if python3 -c 'import json,sys; d=json.load(open("/tmp/vers_verify.json")); sys.exit(0 if d.get("verified") is True else 1)'; then
        break
      fi
      ;;
    401) : ;;  # expected during poll — user has not clicked yet
    403)
      echo "auth rejected (403): base account for this +alias is not verified yet, or the key/email pair is wrong" >&2
      exit 1 ;;
    5*) echo "server error ($HTTP); retrying" >&2 ;;
    *)  echo "unexpected status $HTTP" >&2 ;;
  esac
  [ "$(date +%s)" -gt "$DEADLINE" ] && { echo "verify timed out after 10 min; last status $HTTP" >&2; exit 1; }
  sleep 3
done
# verify response: { "verified": true, "user_id": "...", "key_id": "...",
#                    "is_active": true, "orgs": [ { "name": "...", ... } ] }
```

Named failure modes:

- `401` during poll → the normal "not yet" signal. Keep polling.
- `403` during poll → real rejection. Almost always: a `+alias` whose base
  account is not verified, or a mismatch between `email` and `ssh_public_key`.
  Do not retry with the same inputs.
- Timeout → the user never clicked. Ask them before restarting.

### Step 3 — Create the API key

```bash
VERS_API_KEY=$(curl -sS -X POST https://vers.sh/api/shell-auth/api-keys \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"ssh_public_key\":\"$PUBKEY\",
       \"label\":\"agent-$(hostname)-$(date +%Y%m%d)\",
       \"org_name\":\"<org-from-verify-response.orgs[0].name>\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["api_key"])')
# create-key response: { "success": true, "api_key": "...",
#                        "api_key_id": "...", "org_id": "...", "org_name": "..." }
```

The `api_key` is shown once. If you lose it before Step 4, you run shell-auth
again and orphan the unused key.

Label it descriptively. `agent-<hostname>-<yyyymmdd>` is a good default —
future audits have to find this key.

### Step 4 — Persist the key safely

The file must never exist world-readable, not even for the millisecond between
`printf` and `chmod`. Open the write under a strict umask instead.

```bash
# Option 1: ~/.versrc for the CLI (most portable)
( umask 077 && printf '%s' "$VERS_API_KEY" > ~/.versrc )

# Option 2: env only (in-memory for this process + children)
export VERS_API_KEY
# Add to shell rc if future sessions should inherit it.
```

Do not put the key in `.bashrc`/`.zshrc` unless the caller explicitly asked
for it. Those files are often world-readable and frequently version-controlled.

### Step 5 — Verify (smoke test)

Do not yield control before a 200. The key existing is not the same thing as
the key working.

```bash
curl -sS -H "Authorization: Bearer $VERS_API_KEY" \
  https://api.vers.sh/api/v1/vms
# Expect: a JSON array (possibly empty).
# 401 → the key is wrong or already revoked. Something went sideways in steps 1-3.
# 403 → the key is fine but the account does not have access to /vms in this org.
# 409 only appears during key registration, not here.
```

After this passes, onboarding is complete.

---

## Agent-specific accounts (the `+alias` pattern)

Use when the agent needs its own credentials distinct from the user's primary
account — audit separation, blast-radius isolation, or so revoking the agent
key doesn't log the user out.

```
primary:  alice@company.com
agent:    alice+agent@company.com    # shares alice's organizations
```

The primary (`alice@company.com`) must already be verified. Then run the fast
path with the `+agent` address and a dedicated SSH key. The agent gets its
own user record, its own API key, and sees the same orgs the primary does.

UX note to surface: the user will receive a second verification email at the
`+alias` address (which most mail providers route into the primary inbox).
Tell them to expect one more click.

Revocation: deleting the agent key or user leaves the primary untouched.

---

## Other routes

Most agent sessions will take the fast path above. Two other routes exist when
a human is in the loop:

**Dashboard.** User signs up at `https://vers.sh`, creates a key at
`https://vers.sh/billing`, and pastes it. Right when the user wants a web flow
or corporate SSO/SAML is in play. The agent's job: ask for the pasted key,
persist it per Step 4, then run Step 5.

**`vers init` (CLI-driven).** If the CLI is installed and the user is
hands-on in a terminal, `vers init` inside a project directory prompts them
through auth on first run. Under the hood it runs the same shell-auth flow.

Either route ends at the same place: a working key, persisted, smoke-tested.

---

## Install the CLI (optional)

API-only workflows do not need the CLI. Install it when the caller will want
`vers run`, `vers branch`, `vers commit`, etc., interactively.

```bash
# macOS / Linux — production binary (writes to /usr/local/bin, may prompt for sudo)
curl -sSL https://vers.sh/api/install | bash
vers --version
```

Build from source when the package path is unavailable:

```bash
git clone https://github.com/hdresearch/vers-cli.git
cd vers-cli && make build-and-install
```

Surface the install before running it. A script that writes to
`/usr/local/bin` is not something to run silently on someone else's machine.

---

## Hygiene and anti-patterns

- Never log the API key. Mask it in any output. The shell-auth response is a
  one-time disclosure; a leaked log is a leaked key.
- Open key files under `umask 077`. Do not rely on `chmod` after the fact.
- Ask for the email explicitly. Do not guess from git config or environment
  without confirmation.
- One SSH key per agent identity. Makes revocation surgical.
- Label keys descriptively. Future audits need a story.
- Do not silently re-onboard because one API call 5xx'd. Detect-state first.
- Do not persist the key in `.bashrc`/`.zshrc` unless asked.

---

## See also

- `SKILL.md` — top-level reach-for-Vers skill. Every Vers action it describes
  assumes onboarding passed.
- `api-cheatsheet.md` — full endpoint contract (VMs, commits, tags, domains,
  env vars). Shell-auth is documented here in `onboarding.md`, not there.
- Docs: `https://docs.vers.sh/start-here/setting-up-cli`,
  `https://docs.vers.sh/shell-auth/overview.md`.
