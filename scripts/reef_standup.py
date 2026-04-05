#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
reef_standup.py — Pure Python reef provisioner.

No bun. No node. No JS. Just REST + SSH.

Usage:
    # Fast path: provision from pre-built commits
    uv run scripts/reef_standup.py provision \
        --root-commit <id> --golden-commit <id>

    # With known public commits
    uv run scripts/reef_standup.py provision-public

    # Build root image from branch refs
    uv run scripts/reef_standup.py build-root \
        --reef-ref feat/reef-v2-orchestration \
        --pi-vers-ref remote-bg-process-hardening

    # Build golden image
    uv run scripts/reef_standup.py build-golden \
        --reef-ref feat/reef-v2-orchestration \
        --pi-vers-ref remote-bg-process-hardening

    # Fix clock skew on a VM
    uv run scripts/reef_standup.py fix-clock <vm-id>

    # Generate magic link
    uv run scripts/reef_standup.py magic-link --deployment out/deployment.json
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

VERS_API_BASE = "https://api.vers.sh/api/v1"
LLM_PROXY_BASE = "https://tokens.vers.sh"
DEFAULT_PUNKIN_REF = "carter/punkin/v1_rc5"

PUBLIC_ROOT_COMMIT = "5d9c6176-2e9e-4b38-8fc2-f7e0fb3507ce"
PUBLIC_GOLDEN_COMMIT = "d2fedfa3-a835-4745-9b50-0e94d347d26b"

DEFAULT_VM_CONFIG = {
    "vcpu_count": 2,
    "mem_size_mib": 4096,
    "fs_size_mib": 8192,
    "kernel_name": "default.bin",
}

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=30",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4",
    "-o", "ProxyCommand=openssl s_client -connect %h:443 -servername %h -quiet 2>/dev/null",
]

KEY_CACHE = Path("/tmp/vers-ssh-keys")


# ──────────────────────────────────────────────────────────────────────────────
# Vers REST API
# ──────────────────────────────────────────────────────────────────────────────

def vers_api_key() -> str:
    k = os.environ.get("VERS_API_KEY", "").strip()
    if not k:
        sys.exit("VERS_API_KEY not set")
    return k


def _req(method: str, url: str, body: dict | None = None,
         headers: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", "User-Agent": "reef-standup/1.0"}
    if headers:
        hdrs.update(headers)
    r = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        sys.exit(f"HTTP {e.code} {method} {url}: {body_text}")


def vers_req(method: str, path: str, body: dict | None = None) -> dict:
    return _req(method, VERS_API_BASE + path, body,
                headers={"Authorization": f"Bearer {vers_api_key()}"})


def exchange_llm_key(api_key: str, name: str = "reef-standup") -> dict:
    """Exchange VERS_API_KEY for LLM proxy key."""
    health = _req("GET", f"{LLM_PROXY_BASE}/health")
    if health.get("status") != "ok":
        sys.exit(f"LLM proxy unhealthy: {health}")
    result = _req("POST", f"{LLM_PROXY_BASE}/v1/keys/exchange",
                  body={"vers_api_key": api_key, "name": name})
    key = result.get("key", "")
    if not key.startswith("sk-vers-"):
        sys.exit(f"LLM key exchange failed: {result}")
    return result


# ──────────────────────────────────────────────────────────────────────────────
# SSH
# ──────────────────────────────────────────────────────────────────────────────

def fetch_ssh_key(vm_id: str) -> Path:
    KEY_CACHE.mkdir(exist_ok=True)
    key_path = KEY_CACHE / f"{vm_id}.pem"
    data = vers_req("GET", f"/vm/{vm_id}/ssh_key")
    key_path.write_text(data["ssh_private_key"])
    key_path.chmod(0o600)
    return key_path


def get_ssh_key(vm_id: str) -> Path:
    key_path = KEY_CACHE / f"{vm_id}.pem"
    if not key_path.exists():
        return fetch_ssh_key(vm_id)
    return key_path


def ssh_base(vm_id: str) -> list[str]:
    key = get_ssh_key(vm_id)
    return ["ssh", *SSH_OPTS, "-i", str(key), f"root@{vm_id}.vm.vers.sh"]


def ssh_exec(vm_id: str, command: str, *, check: bool = True,
             stdin: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = [*ssh_base(vm_id), command]
    return subprocess.run(cmd, capture_output=True, text=True, check=check,
                          input=stdin, timeout=timeout)


def ssh_probe(vm_id: str, max_wait: int = 90) -> bool:
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        try:
            r = ssh_exec(vm_id, "echo ok", check=False, timeout=15)
            if r.returncode == 0 and "ok" in r.stdout:
                return True
        except Exception:
            pass
        attempt += 1
        time.sleep(min(2.0 * (1.3 ** attempt), 10.0))
    return False


def fix_vm_clock(vm_id: str) -> None:
    """Sync VM clock to current UTC — fixes TLS cert validation after snapshot restore."""
    local_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ssh_exec(vm_id, f'date -u -s "{local_utc}" >/dev/null 2>&1', check=False)
    print(f"[standup] clock synced on {vm_id[:16]}")


# ──────────────────────────────────────────────────────────────────────────────
# Runtime Script Generation
# ──────────────────────────────────────────────────────────────────────────────

def shell_quote(value: str) -> str:
    return shlex.quote(value)


def build_runtime_env(vm_id: str, *, api_key: str, auth_token: str,
                      llm_proxy_key: str, golden_commit_id: str,
                      root_commit_id: str = "",
                      punkin_ref: str = DEFAULT_PUNKIN_REF,
                      user_name: str = "", user_email: str = "",
                      user_timezone: str = "", user_location: str = "") -> str:
    """Build the .env block for a reef root VM.

    User identity is passed via REEF_USER_* env vars. Reef's
    ensureProfileFromEnv() reads these on first boot to seed the
    operator profile that gets injected into all agent prompts.
    No names are hardcoded — set via CLI flags or env vars at
    provision time.
    """
    root_url = f"https://{vm_id}.vm.vers.sh:3000"
    lines = [
        "PORT=3000",
        f"VERS_VM_ID={vm_id}",
        "VERS_AGENT_NAME=root-reef",
        "VERS_AGENT_ROLE=infra_vm",
        f"VERS_API_KEY={shell_quote(api_key)}",
        f"VERS_AUTH_TOKEN={shell_quote(auth_token)}",
        f"VERS_INFRA_URL={shell_quote(root_url)}",
        f"LLM_PROXY_KEY={shell_quote(llm_proxy_key)}",
        f"ANTHROPIC_API_KEY={shell_quote(llm_proxy_key)}",
        "REEF_ROLE=root",
        "REEF_CATEGORY=infra_vm",
        f"REEF_ROOT_VM_ID={vm_id}",
        "REEF_SQLITE_AUTHORITY=true",
        f"PUNKIN_RELEASE_TAG={shell_quote(punkin_ref)}",
        f"PUNKIN_BIN={shell_quote('punkin')}",
        f"PI_PATH={shell_quote('punkin')}",
        f"PI_VERS_HOME={shell_quote('/opt/pi-vers')}",
        f"SERVICES_DIR={shell_quote('/opt/reef/services-active')}",
    ]
    if root_commit_id:
        lines.append(f"VERS_ROOT_COMMIT_ID={shell_quote(root_commit_id)}")
    if golden_commit_id:
        lines.append(f"VERS_GOLDEN_COMMIT_ID={shell_quote(golden_commit_id)}")
    # Operator identity — reef reads these to seed the user profile
    if user_name:
        lines.append(f"REEF_USER_NAME={shell_quote(user_name)}")
    if user_email:
        lines.append(f"REEF_USER_EMAIL={shell_quote(user_email)}")
    if user_timezone:
        lines.append(f"REEF_USER_TIMEZONE={shell_quote(user_timezone)}")
    if user_location:
        lines.append(f"REEF_USER_LOCATION={shell_quote(user_location)}")
    return "\n".join(lines)


def build_runtime_script(vm_id: str, *, api_key: str, auth_token: str,
                         llm_proxy_key: str, golden_commit_id: str,
                         root_commit_id: str = "",
                         services: str = "",
                         user_name: str = "", user_email: str = "",
                         user_timezone: str = "", user_location: str = "") -> str:
    env_block = build_runtime_env(
        vm_id, api_key=api_key, auth_token=auth_token,
        llm_proxy_key=llm_proxy_key, golden_commit_id=golden_commit_id,
        root_commit_id=root_commit_id,
        user_name=user_name, user_email=user_email,
        user_timezone=user_timezone, user_location=user_location,
    )

    return f"""#!/bin/bash
set -euo pipefail

echo "[standup] configuring runtime for root-reef"
export PATH="/root/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

cat > /opt/reef/.env <<'ENVEOF'
{env_block}
ENVEOF

set -a
source /opt/reef/.env
set +a

# Activate services
rm -rf /opt/reef/services-active
mkdir -p /opt/reef/services-active
for dir in /opt/reef/services/*/; do
  svc=$(basename "$dir")
  ln -s "../services/$svc" "/opt/reef/services-active/$svc"
done
export SERVICES_DIR="/opt/reef/services-active"

# Install extensions with runtime env
mkdir -p /root/.punkin/agent /root/.pi/agent
if command -v punkin >/dev/null 2>&1; then
  punkin install /opt/pi-vers 2>/dev/null || true
  punkin install /opt/reef 2>/dev/null || true
fi

cd /opt/reef
pkill -f "bun run src/main.ts" 2>/dev/null || true
tmux kill-session -t reef 2>/dev/null || true
tmux new-session -d -s reef "set -a; source /opt/reef/.env; set +a; export PATH=/root/.bun/bin:\\$PATH; cd /opt/reef; bun run src/main.ts >> /tmp/reef.log 2>&1"

for i in $(seq 1 45); do
  if curl -sf http://localhost:3000/health >/dev/null 2>&1; then
    echo "[standup] reef is healthy"
    exit 0
  fi
  sleep 1
done

echo "[standup] reef failed to start" >&2
tail -50 /tmp/reef.log >&2 || true
exit 1
"""


# ──────────────────────────────────────────────────────────────────────────────
# Image Build Script Generation
# ──────────────────────────────────────────────────────────────────────────────

def build_image_script(*, reef_ref: str = "main", pi_vers_ref: str = "main",
                       punkin_ref: str = DEFAULT_PUNKIN_REF,
                       target: str = "root") -> str:
    """Generate the shell script that builds a root or golden image on a fresh VM."""
    if target == "root":
        base_dir = "/opt/src"
        reef_dir = "/opt/src/reef"
        pi_vers_dir = "/opt/src/pi-vers"
        punkin_dir = "/opt/src/punkin-pi"
        reef_link = "ln -sfn /opt/src/reef /opt/reef\nln -sfn /opt/src/pi-vers /opt/pi-vers\nln -sfn /opt/src/punkin-pi /opt/punkin-pi"
        agents_md_path = "/opt/reef/AGENTS.md"
        agents_md_link = f'ln -sfn {agents_md_path} /root/.pi/agent/AGENTS.md'
        env_profile = ""
    else:  # golden
        base_dir = "/root"
        reef_dir = "/root/reef"
        pi_vers_dir = "/root/pi-vers"
        punkin_dir = "/root/punkin-pi"
        reef_link = ""
        agents_md_path = "/root/reef/AGENTS.md"
        agents_md_link = f"""ln -sfn {agents_md_path} /root/.pi/agent/AGENTS.md
ln -sfn {agents_md_path} /root/workspace/AGENTS.md"""
        env_profile = f"""
cat > /etc/profile.d/reef-agent.sh <<'ENVEOF'
export PATH="/root/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export PUNKIN_RELEASE_TAG={shell_quote(punkin_ref)}
export PUNKIN_BIN=punkin
export PI_PATH=punkin
export PI_VERS_HOME=/root/pi-vers
export SERVICES_DIR=/root/reef/services-active
export REEF_CHILD_AGENT=true
ENVEOF
chmod 0644 /etc/profile.d/reef-agent.sh

for shell_rc in /root/.profile /root/.bashrc /root/.zshenv; do
  touch "$shell_rc"
  if ! grep -q "reef-agent.sh" "$shell_rc"; then
    printf '\\n[ -f /etc/profile.d/reef-agent.sh ] && . /etc/profile.d/reef-agent.sh\\n' >> "$shell_rc"
  fi
done

set -a
source /etc/profile.d/reef-agent.sh
set +a
"""

    services_active = ""
    if target == "golden":
        services_active = """
rm -rf /root/reef/services-active
mkdir -p /root/reef/services-active
for dir in /root/reef/services/*/; do
  svc=$(basename "$dir")
  ln -s "../services/$svc" "/root/reef/services-active/$svc"
done
"""

    return f"""#!/bin/bash
set -euo pipefail

echo "[standup] building {target} image"
export DEBIAN_FRONTEND=noninteractive
export PATH="/root/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

echo "nameserver 8.8.8.8" > /etc/resolv.conf

apt-get update -qq
apt-get install -y -qq curl git ca-certificates build-essential openssl unzip

if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi

if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash
  export PATH="/root/.bun/bin:$PATH"
fi

mkdir -p {base_dir}

# Clone repos
for repo_spec in "reef|https://github.com/hdresearch/reef.git|{reef_ref}|{reef_dir}" \\
                 "pi-vers|https://github.com/hdresearch/pi-vers.git|{pi_vers_ref}|{pi_vers_dir}" \\
                 "punkin-pi|https://github.com/hdresearch/punkin-pi.git|{punkin_ref}|{punkin_dir}"; do
  IFS='|' read -r name url ref dir <<< "$repo_spec"
  if [ -d "$dir/.git" ]; then
    cd "$dir" && git fetch --all --tags --force
  else
    rm -rf "$dir"
    git clone "$url" "$dir"
    cd "$dir"
  fi
  git fetch --tags --force origin
  if git rev-parse --verify -q "refs/tags/$ref" >/dev/null 2>&1; then
    git -c advice.detachedHead=false checkout --detach "refs/tags/$ref"
  else
    git checkout "$ref" || git checkout "origin/$ref"
  fi
done

{reef_link}

# Build
cd {punkin_dir}
HUSKY=0 npm install
npm run build

cd {pi_vers_dir}
npm install
npm run build

cd {reef_dir}
bun install

# Legacy compat aliases
for pkg_root in {pi_vers_dir} {reef_dir}; do
  mkdir -p "$pkg_root/node_modules/@mariozechner"
  ln -sfn {punkin_dir}/packages/tui "$pkg_root/node_modules/@mariozechner/pi-tui"
  ln -sfn {punkin_dir}/packages/coding-agent "$pkg_root/node_modules/@mariozechner/pi-coding-agent"
  ln -sfn {punkin_dir}/packages/ai "$pkg_root/node_modules/@mariozechner/pi-ai"
  ln -sfn {punkin_dir}/packages/agent "$pkg_root/node_modules/@mariozechner/pi-agent-core"
done

{services_active}

# Punkin wrapper
BUN_PATH=$(command -v bun 2>/dev/null || echo "/root/.bun/bin/bun")
if [ -x {punkin_dir}/builds/punkin ]; then
  cat > /usr/local/bin/punkin <<WRAPPER
#!/bin/sh
{"[ -f /etc/profile.d/reef-agent.sh ] && set -a && . /etc/profile.d/reef-agent.sh && set +a" if target == "golden" else ""}
exec $BUN_PATH {punkin_dir}/builds/punkin "\\$@"
WRAPPER
elif [ -x {punkin_dir}/packages/coding-agent/dist/cli.js ]; then
  cat > /usr/local/bin/punkin <<WRAPPER
#!/bin/sh
{"[ -f /etc/profile.d/reef-agent.sh ] && set -a && . /etc/profile.d/reef-agent.sh && set +a" if target == "golden" else ""}
exec $BUN_PATH {punkin_dir}/packages/coding-agent/dist/cli.js "\\$@"
WRAPPER
fi
chmod +x /usr/local/bin/punkin 2>/dev/null || true
ln -sf /usr/local/bin/punkin /usr/local/bin/pi

# Patch shebang
for f in {punkin_dir}/packages/coding-agent/dist/cli.js {punkin_dir}/builds/punkin; do
  if [ -f "$f" ] && head -1 "$f" | grep -q "#!/usr/bin/env node"; then
    sed -i '1s|#!/usr/bin/env node|#!/usr/bin/env bun|' "$f"
  fi
done

mkdir -p /root/.punkin/agent /root/.pi/agent {"" if target == "root" else "/root/workspace"}
{agents_md_link}

{env_profile}

# Install extensions
if command -v punkin >/dev/null 2>&1; then
  punkin install {"/opt/pi-vers" if target == "root" else pi_vers_dir}
  punkin install {"/opt/reef" if target == "root" else reef_dir}
fi

# Git credential helper
if [ -f {reef_dir}/services/github/credential-helper.sh ]; then
  cp {reef_dir}/services/github/credential-helper.sh /usr/local/bin/git-credential-vers
  chmod +x /usr/local/bin/git-credential-vers
  git config --global credential.https://github.com.helper /usr/local/bin/git-credential-vers
fi

{"test -x /usr/local/bin/pi && test -d /root/pi-vers && test -d /root/reef/services-active" if target == "golden" else ""}

echo "[standup] {target} image build complete"
"""


# ──────────────────────────────────────────────────────────────────────────────
# Fleet Registration
# ──────────────────────────────────────────────────────────────────────────────

def wait_for_health(root_url: str, max_attempts: int = 60) -> bool:
    for _ in range(max_attempts):
        try:
            r = _req("GET", f"{root_url}/health", timeout=10)
            if r.get("status") == "ok":
                return True
        except (urllib.error.URLError, SystemExit):
            pass
        time.sleep(2)
    return False


def register_root(vm_id: str, auth_token: str) -> None:
    root_url = f"https://{vm_id}.vm.vers.sh:3000"
    print("[standup] waiting for reef health...")
    if not wait_for_health(root_url):
        sys.exit("[standup] reef failed health check")

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    vm_record = {
        "vmId": vm_id,
        "name": "root-reef",
        "category": "infra_vm",
        "status": "running",
        "address": f"{vm_id}.vm.vers.sh",
        "lastHeartbeat": int(time.time() * 1000),
    }
    try:
        _req("PATCH", f"{root_url}/vm-tree/vms/{urllib.parse.quote(vm_id)}",
             body=vm_record, headers=headers)
    except SystemExit:
        _req("POST", f"{root_url}/vm-tree/vms", body=vm_record, headers=headers)

    print("[standup] root registered in vm-tree")


# ──────────────────────────────────────────────────────────────────────────────
# Deployment Manifest
# ──────────────────────────────────────────────────────────────────────────────

def write_deployment(out_dir: str, vm_id: str, api_key: str, auth_token: str,
                     llm_proxy: dict) -> Path:
    root_url = f"https://{vm_id}.vm.vers.sh:3000"
    deployment = {
        "nodes": {"root": {"vmId": vm_id, "url": root_url}},
        "auth": {
            "versApiKey": api_key,
            "versAuthToken": auth_token,
            "llmProxyKey": llm_proxy.get("key", ""),
            "llmProxyKeyPrefix": llm_proxy.get("key_prefix", ""),
            "llmProxyKeyId": llm_proxy.get("id", ""),
            "llmProxyTeamId": llm_proxy.get("team_id", ""),
        },
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "deployment.json"
    path.write_text(json.dumps(deployment, indent=2) + "\n")
    return path


# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────

def cmd_provision(args: argparse.Namespace) -> int:
    api_key = vers_api_key()
    root_commit = args.root_commit
    golden_commit = args.golden_commit
    out_dir = args.out_dir

    print("[standup] exchanging LLM proxy key...")
    llm_proxy = exchange_llm_key(api_key)

    auth_token = secrets.token_hex(32)

    print(f"[standup] restoring root from commit {root_commit[:16]}...")
    vm = vers_req("POST", "/vm/from_commit", {"commit_id": root_commit})
    vm_id = vm["vm_id"]
    print(f"[standup] vm_id={vm_id}")

    print("[standup] fetching SSH key...")
    fetch_ssh_key(vm_id)

    print("[standup] waiting for SSH...")
    if not ssh_probe(vm_id, max_wait=90):
        sys.exit("[standup] SSH unavailable after 90s")

    print("[standup] fixing clock...")
    fix_vm_clock(vm_id)

    print("[standup] injecting runtime config...")
    script = build_runtime_script(
        vm_id, api_key=api_key, auth_token=auth_token,
        llm_proxy_key=llm_proxy["key"], golden_commit_id=golden_commit,
        root_commit_id=root_commit,
        user_name=getattr(args, "user_name", "") or os.environ.get("REEF_USER_NAME", ""),
        user_email=getattr(args, "user_email", "") or os.environ.get("REEF_USER_EMAIL", ""),
        user_timezone=getattr(args, "user_timezone", "") or os.environ.get("REEF_USER_TIMEZONE", ""),
        user_location=getattr(args, "user_location", "") or os.environ.get("REEF_USER_LOCATION", ""),
    )
    r = ssh_exec(vm_id, "bash -s", stdin=script, check=False, timeout=120)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        sys.exit(f"[standup] runtime script failed (rc={r.returncode})")

    register_root(vm_id, auth_token)

    path = write_deployment(out_dir, vm_id, api_key, auth_token, llm_proxy)
    root_url = f"https://{vm_id}.vm.vers.sh:3000"

    print(f"\n[standup] ✓ Reef root is live at {root_url}")
    print(f"[standup] deployment manifest: {path}")
    print(f"[standup] VERS_API_KEY: {api_key[:20]}...")
    return 0


def cmd_provision_public(args: argparse.Namespace) -> int:
    args.root_commit = args.root_commit or PUBLIC_ROOT_COMMIT
    args.golden_commit = args.golden_commit or PUBLIC_GOLDEN_COMMIT
    return cmd_provision(args)


def cmd_build(args: argparse.Namespace) -> int:
    target = args.target  # "root" or "golden"
    vers_api_key()
    out_dir = args.out_dir

    print(f"[standup] creating VM for {target} image build...")
    vm = vers_req("POST", "/vm/new_root?wait_boot=true", {"vm_config": DEFAULT_VM_CONFIG})
    vm_id = vm["vm_id"]
    print(f"[standup] vm_id={vm_id}")

    fetch_ssh_key(vm_id)

    print("[standup] waiting for SSH...")
    if not ssh_probe(vm_id, max_wait=90):
        vers_req("DELETE", f"/vm/{vm_id}")
        sys.exit("[standup] SSH unavailable — VM deleted")

    fix_vm_clock(vm_id)

    print(f"[standup] running {target} image build (this takes a few minutes)...")
    script = build_image_script(
        reef_ref=args.reef_ref,
        pi_vers_ref=args.pi_vers_ref,
        punkin_ref=args.punkin_ref,
        target=target,
    )
    r = ssh_exec(vm_id, "bash -s", stdin=script, check=False, timeout=900)
    if r.returncode != 0:
        print(r.stdout[-2000:] if r.stdout else "", file=sys.stderr)
        print(r.stderr[-2000:] if r.stderr else "", file=sys.stderr)
        print(f"[standup] build failed — VM kept alive for debugging: {vm_id}")
        return 1

    print(f"[standup] committing {target} image...")
    commit = vers_req("POST", f"/vm/{vm_id}/commit")
    commit_id = commit["commit_id"]
    print(f"[standup] committed: {commit_id}")

    if args.public:
        vers_req("PATCH", f"/commits/{commit_id}", {"is_public": True})
        print("[standup] commit made public")
        vers_req("DELETE", f"/vm/{vm_id}")
        print("[standup] builder VM deleted")
    else:
        print(f"[standup] builder VM kept alive: {vm_id}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = {"commitId": commit_id, "vmId": vm_id, "target": target}
    manifest = out / f"build-{target}.json"
    manifest.write_text(json.dumps(result, indent=2) + "\n")
    print(f"[standup] manifest: {manifest}")
    return 0


def cmd_fix_clock(args: argparse.Namespace) -> int:
    fetch_ssh_key(args.vm_id)
    fix_vm_clock(args.vm_id)
    return 0


def cmd_magic_link(args: argparse.Namespace) -> int:
    deployment_path = Path(args.deployment).expanduser().resolve()
    if not deployment_path.exists():
        sys.exit(f"deployment manifest not found: {deployment_path}")
    d = json.loads(deployment_path.read_text())
    root_url = d["nodes"]["root"]["url"]
    auth_token = d["auth"]["versAuthToken"]

    result = _req("POST", f"{root_url.rstrip('/')}/auth/magic-link",
                  body={}, headers={"Authorization": f"Bearer {auth_token}"})
    print(json.dumps(result, indent=2))
    return 0


def cmd_nuke(args: argparse.Namespace) -> int:
    """Delete all VMs created today."""
    vers_api_key()
    vms = vers_req("GET", "/vms")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    targets = [v for v in vms if v.get("created_at", "").startswith(today)]
    print(f"[standup] {len(targets)} VMs from {today}")
    for v in targets:
        vm_id = v["vm_id"]
        if v.get("state") == "paused":
            vers_req("PATCH", f"/vm/{vm_id}/state", {"state": "Running"})
            time.sleep(1)
        try:
            vers_req("DELETE", f"/vm/{vm_id}")
            print(f"  ✓ {vm_id[:16]} deleted")
        except SystemExit as e:
            print(f"  ✗ {vm_id[:16]} {e}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Pure Python reef provisioner — no JS, no bun")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Operator identity flags — shared by provision commands.
    # Also reads REEF_USER_NAME, REEF_USER_EMAIL, REEF_USER_TIMEZONE,
    # REEF_USER_LOCATION from environment as fallbacks.
    def add_user_flags(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--user-name", default="", help="operator display name")
        parser.add_argument("--user-email", default="", help="operator email")
        parser.add_argument("--user-timezone", default="", help="e.g. America/New_York")
        parser.add_argument("--user-location", default="", help="e.g. NYC")

    p = sub.add_parser("provision", help="provision from pre-built commits")
    p.add_argument("--root-commit", required=True)
    p.add_argument("--golden-commit", required=True)
    p.add_argument("--out-dir", default="out")
    add_user_flags(p)

    p = sub.add_parser("provision-public", help="provision from known public commits")
    p.add_argument("--root-commit", default=PUBLIC_ROOT_COMMIT)
    p.add_argument("--golden-commit", default=PUBLIC_GOLDEN_COMMIT)
    p.add_argument("--out-dir", default="out")
    add_user_flags(p)

    p = sub.add_parser("build-root", help="build root image from branch refs")
    p.add_argument("--reef-ref", default="main")
    p.add_argument("--pi-vers-ref", default="main")
    p.add_argument("--punkin-ref", default=DEFAULT_PUNKIN_REF)
    p.add_argument("--public", action="store_true")
    p.add_argument("--out-dir", default="out")
    p.set_defaults(target="root")

    p = sub.add_parser("build-golden", help="build golden image from branch refs")
    p.add_argument("--reef-ref", default="main")
    p.add_argument("--pi-vers-ref", default="main")
    p.add_argument("--punkin-ref", default=DEFAULT_PUNKIN_REF)
    p.add_argument("--public", action="store_true")
    p.add_argument("--out-dir", default="out")
    p.set_defaults(target="golden")

    p = sub.add_parser("fix-clock", help="fix clock skew on a VM")
    p.add_argument("vm_id")

    p = sub.add_parser("magic-link", help="generate reef UI login link")
    p.add_argument("--deployment", default="out/deployment.json")

    p = sub.add_parser("nuke", help="delete all VMs created today")

    args = parser.parse_args()

    dispatch = {
        "provision": cmd_provision,
        "provision-public": cmd_provision_public,
        "build-root": cmd_build,
        "build-golden": cmd_build,
        "fix-clock": cmd_fix_clock,
        "magic-link": cmd_magic_link,
        "nuke": cmd_nuke,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
