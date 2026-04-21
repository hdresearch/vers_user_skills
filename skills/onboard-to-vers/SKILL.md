---
name: onboard-to-vers
description: >
  First-time setup for the Vers platform (vers.sh). Load when `offload-to-vers`
  or any Vers operation needs credentials that are not present, when the user
  has no Vers account yet, when there is no API key on this machine, when the
  `vers` CLI is not installed, or when an agent needs its own credentials
  distinct from the user's primary account. Walks the detect-state → choose-path
  → register/install/authenticate → verify flow. Covers the programmatic
  shell-auth route (email + SSH key, no browser except the verification link),
  the dashboard route, CLI install, the `+` alias pattern for agent-specific
  accounts, and the smoke test that confirms auth works. Delegates endpoint
  details to `vers-api-reference`.
metadata:
  author: Carter Schonwald
  version: 1
  depends_on: vers-api-reference
  source: https://docs.vers.sh/start-here/setting-up-cli
---

# onboard-to-vers

First-time setup. This fires once per user per machine (or once per agent
identity). After a successful run the caller should have:

- A Vers account (created or existing).
- An API key on this machine — either in `$VERS_API_KEY` or at `~/.versrc`.
- Optionally, the `vers` CLI installed at `/usr/local/bin/vers`.
- A passing smoke test.

---

## Detect state first

Before doing anything, check what already exists. Most of the time you do not
need to onboard at all — an agent loading this skill on a machine that already
has credentials should short-circuit.

```bash
# 1. Is an API key in the environment?
[ -n "$VERS_API_KEY" ] && echo "env key present"

# 2. Is a key persisted for the CLI?
[ -f ~/.versrc ] && echo "versrc present"

# 3. Is the CLI installed?
command -v vers >/dev/null && vers --version

# 4. Smoke-test whatever key you found (see "Verify" below).
```

Decision tree:

- Env key + smoke passes → **done.** Do not re-onboard.
- `~/.versrc` present + smoke passes → **done.** Export to env if the caller
  needs `$VERS_API_KEY` (`export VERS_API_KEY=$(cat ~/.versrc)`).
- Nothing present → **full onboarding** (below).
- Key present but smoke fails → the key is stale, revoked, or wrong org. Treat
  as no key; re-onboard or ask the user.

---

## Choose a path

Three routes. Pick by context.

### A. Programmatic shell-auth (agent-preferred)

No browser except the one click on the verification email. Works headless on a
server once the user clicks the link in their inbox. Returns an API key you
can use immediately.

Use when: agent is driving, user can be asked for an email and to click a link,
no existing key on the machine. This is the right default for
coding-agent sessions.

### B. Dashboard signup + manual key

User goes to `https://vers.sh`, signs up via the web, creates an API key at
`https://vers.sh/billing`, pastes it into `vers login` (or `export VERS_API_KEY=`).

Use when: user prefers the web flow, corporate SSO/SAML is in play, or the
programmatic flow has hit an edge case.

### C. `vers init` (CLI-driven)

If the CLI is installed, `vers init` inside a project directory prompts the
user through auth on first run. Convenient for developers already in a
terminal.

Use when: user is hands-on and wants CLI-first. The CLI invokes the same
shell-auth machinery underneath.

---

## Install the CLI (optional but recommended)

API-only use does not strictly require the CLI. Install it when the user or
agent will want `vers run`, `vers branch`, `vers commit`, etc.

```bash
# macOS / Linux — production binary
curl -sSL https://vers.sh/api/install | bash
# Installs to /usr/local/bin/vers; may prompt for sudo.

# Verify
vers --version
# → v0.5.x or similar
```

Build from source (when package manager unavailable or air-gapped):

```bash
git clone https://github.com/hdresearch/vers-cli.git
cd vers-cli && make build-and-install
```

Surface the install to the user before running it — it writes to
`/usr/local/bin`. Do not run it silently.

---

## Programmatic shell-auth flow (Route A, full)

> This section is the **operational recipe**: what to do, in what order, with what
> hygiene. For endpoint request/response shapes, error codes (400/403/409), and the
> `body` / `is_new_user` / `is_active` / `key_id` response fields, see
> `vers-api-reference` § Shell Auth. This skill owns the walkthrough; that skill
> owns the wire format.

Three API calls. The user's only manual step is clicking the verification link
in their email.

### Step 0 — Prepare an SSH key

Shell-auth binds an API key to an SSH public key. Use an existing key or
generate a fresh one for this agent identity:

```bash
# Use existing
cat ~/.ssh/id_ed25519.pub

# Or generate a dedicated agent key
ssh-keygen -t ed25519 -f ~/.ssh/vers_agent -N "" -C "vers-agent@$(hostname)"
PUBKEY=$(cat ~/.ssh/vers_agent.pub)
```

Each SSH public key is uniquely bound to one Vers account. If the key is
already registered elsewhere, the API returns 409 — pick a different key or
generate a fresh one.

### Step 1 — Initiate

```bash
EMAIL="you@example.com"
curl -sS -X POST https://vers.sh/api/shell-auth \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"ssh_public_key\":\"$PUBKEY\"}"
```

This sends a verification email. For new emails, shell-auth creates the account;
on verification an organization + free subscription are provisioned automatically.
For existing users, the SSH key is registered against the account.

Response includes `is_new_user` (whether this is a first-time user) and a nonce.

Tell the user: "Check your inbox for a verification email from Vers and click
the link."

### Step 2 — Poll until verified

```bash
DEADLINE=$(( $(date +%s) + 600 ))   # give the user 10 minutes to click
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
    401|403)
      echo "auth rejected ($HTTP); check email/key, do not retry blindly" >&2; exit 1 ;;
    409)
      echo "SSH key already bound to another account; pick a different key" >&2; exit 1 ;;
    5*)
      echo "server error ($HTTP); will retry briefly" >&2 ;;
    *)
      echo "unexpected status $HTTP" >&2 ;;
  esac
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    echo "verify timed out after 10 min; last status $HTTP" >&2
    exit 1
  fi
  sleep 3
done
```

The verify response carries `user_id`, `key_id`, `is_active`, and an `orgs[]`
array once the user has clicked through.

### Step 3 — Create the API key

```bash
API_KEY=$(curl -sS -X POST https://vers.sh/api/shell-auth/api-keys \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"ssh_public_key\":\"$PUBKEY\",
       \"label\":\"agent-$(hostname)-$(date +%Y%m%d)\",
       \"org_name\":\"<org-from-verify-response>\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["api_key"])')
```

The API key is **shown once**. Persist it immediately:

```bash
# Option 1: .versrc for the CLI
printf '%s' "$API_KEY" > ~/.versrc && chmod 600 ~/.versrc

# Option 2: environment for API-only use
export VERS_API_KEY="$API_KEY"
# Add to shell rc for persistence across sessions.
```

If you skip this step you will need to re-run shell-auth to create another key.

---

## Agent-specific accounts (the `+` alias pattern)

When an agent needs its own credentials distinct from the user's primary
account — for audit separation, blast-radius isolation, or so revoking the
agent key doesn't kick the user out — use email-plus-aliasing:

```
primary:  alice@company.com
agent:    alice+agent@company.com   (shares alice's organizations)
```

The primary account (`alice@company.com`) must already exist and be verified.
Then run the shell-auth flow with the `+agent` address and a dedicated SSH
key. The agent gets its own user record, its own API key, and sees the same
organizations as the primary.

Revocation: deleting the agent key or user leaves the primary untouched.

---

## Verify (smoke test)

After any route, confirm the key actually works before yielding control:

```bash
curl -sS -H "Authorization: Bearer $VERS_API_KEY" \
  https://api.vers.sh/api/v1/vms
# → JSON array (possibly empty). No 401/403.

# Optional: health endpoint requires no auth but confirms network
curl -sS https://api.vers.sh/health
```

A 401 means the key is wrong or revoked. A 403 on shell-auth typically means
the base account for a `+alias` isn't verified yet. A 409 during key registration
means the SSH key is already bound to another account.

---

## Decision summary

```
detect state
  env key or ~/.versrc present + smoke passes → done
  otherwise → need onboarding
     |
     ├─ agent-driven, email available → Route A (shell-auth)
     ├─ user prefers web/SSO            → Route B (dashboard)
     └─ user hands-on in terminal       → Route C (vers init)
         |
         (CLI not installed? offer `curl … | bash` first)
         |
        persist key (~/.versrc 0600, or $VERS_API_KEY)
         |
        smoke test
         |
        done
```

---

## First SSH and first tools

After onboarding, the first SSH session is the first real proof that the machine is
usable for work. One caveat matters immediately:

- The default image is sparse. Expect to install core tools explicitly before doing
  real work.

A safe first pass looks like:

```bash
# First connection: prove the VM is alive before bulk copy
ssh -i /tmp/vers-{vm_id}.key \
  -o StrictHostKeyChecking=no \
  -o ProxyCommand="openssl s_client -connect %h:443 -servername %h -quiet 2>/dev/null" \
  root@{vm_id}.vm.vers.sh 'echo alive && uname -a'

# Then install the small core you actually need
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync curl git build-essential
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq procps parallel || true
which rsync curl git parallel free
```

Treat `procps` / `parallel` configure errors on the default image as suspicious but
not automatically fatal: verify the binaries before trusting the install.

---


## Hygiene

- **Never log the API key.** Mask in any output. The shell-auth response is
  a one-time disclosure.
- **Chmod 600** on `~/.versrc` and any file containing the key.
- **Surface the onboarding decision** to the user before starting it. Do not
  silently create accounts or install binaries.
- **Ask for email explicitly.** Do not guess from git config or environment
  without confirmation.
- **One SSH key, one account.** Use a dedicated key per agent identity so
  revocation is surgical.
- **Label API keys descriptively** (`label:` field): e.g.,
  `agent-<hostname>-<yyyymmdd>`. Future-you will want to audit.

---

## Anti-patterns

- Running `curl … | bash` without telling the user first.
- Reading `~/.gitconfig` for the email and skipping the confirmation prompt.
- Persisting the API key in `.bashrc`/`.zshrc` world-readable.
- Creating multiple API keys per session when one would do.
- Skipping the smoke test and then failing an hour later mid-workflow.
- Silently re-onboarding when an existing key is present but a single API call
  happened to 5xx.

---

## See also

- `offload-to-vers` — the top-level reach-for-Vers skill that depends on a
  working auth state established here.
- `vers-api-reference` — full shell-auth endpoint shapes (`/api/shell-auth`,
  `/verify-key`, `/api-keys`, `/verify-public-key`) and error codes.
- Docs: `https://docs.vers.sh/start-here/setting-up-cli`,
  `https://docs.vers.sh/shell-auth/overview.md`.
