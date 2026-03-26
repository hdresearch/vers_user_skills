#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
Vers lieutenant manager — pure ssh + subprocess, no pi plugin required.

State: ~/.vers/lieutenants.json  (override: VERS_LT_STATE)

Protocol (same as pi-vers extension, just shell not TS):
  - input:  ssh vm "cat > /tmp/pi-rpc/in"        ← write JSON lines
  - output: ssh vm "tail -n +0 -f /tmp/pi-rpc/out" ← read streaming JSON
  - keepalive: tmux 'pi-keeper'  holds FIFO write-end open
  - worker:    tmux 'pi-rpc'     runs pi --mode rpc
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

_state_env = os.environ.get("VERS_LT_STATE")
STATE_PATH = Path(_state_env) if _state_env else Path.home() / ".vers/lieutenants.json"
VERS_API_BASE = "https://api.vers.sh/api/v1"
KEY_CACHE = Path("/tmp/vers-ssh-keys")

# ──────────────────────────────────────────────────────────────────────────────
# Vers REST API
# ──────────────────────────────────────────────────────────────────────────────

def vers_api_key() -> str:
    k = os.environ.get("VERS_API_KEY", "").strip()
    if not k:
        sys.exit("VERS_API_KEY not set")
    return k


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = VERS_API_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {vers_api_key()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")


# ──────────────────────────────────────────────────────────────────────────────
# State (atomic writes)
# ──────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"lieutenants": {}}


def save_state(state: dict) -> None:
    """Atomic write via tmp + rename — safe on crash."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(STATE_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# SSH
# ──────────────────────────────────────────────────────────────────────────────

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=30",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4",
    "-o", "ProxyCommand=openssl s_client -connect %h:443 -servername %h -quiet 2>/dev/null",
]


def _fetch_key(vm_id: str) -> Path:
    """Always fetch from API and write to cache."""
    KEY_CACHE.mkdir(exist_ok=True)
    key_path = KEY_CACHE / f"{vm_id}.pem"
    data = _req("GET", f"/vm/{vm_id}/ssh_key")
    key_path.write_text(data["ssh_private_key"])
    key_path.chmod(0o600)
    return key_path


def get_key_path(vm_id: str, force_refresh: bool = False) -> Path:
    key_path = KEY_CACHE / f"{vm_id}.pem"
    if force_refresh or not key_path.exists():
        return _fetch_key(vm_id)
    return key_path


def _ssh_base(vm_id: str, key: Path) -> list[str]:
    return ["ssh", *SSH_OPTS, "-i", str(key), f"root@{vm_id}.vm.vers.sh"]


def ssh_cmd(vm_id: str, command: str, *, key: Path | None = None,
            check: bool = True, capture: bool = False,
            stdin: str | None = None) -> subprocess.CompletedProcess:
    k = key or get_key_path(vm_id)
    cmd = [*_ssh_base(vm_id, k), command]
    return subprocess.run(cmd, capture_output=capture, text=True, check=check,
                          input=stdin)


def ssh_write_fifo(vm_id: str, json_line: str, key: Path, timeout: int = 15) -> None:
    """Write a newline-terminated JSON line to the pi-rpc FIFO with timeout."""
    cmd = [*_ssh_base(vm_id, key), "timeout 10 bash -c 'cat > /tmp/pi-rpc/in'"]
    proc = subprocess.run(cmd, input=json_line + "\n", text=True,
                          capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ssh write failed (rc={proc.returncode}): {proc.stderr.strip()}")


def ssh_probe(vm_id: str, key: Path, max_wait: int = 90) -> bool:
    """Wait for SSH to become available, with jitter to avoid thundering herds."""
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        try:
            r = ssh_cmd(vm_id, "echo ok", key=key, check=False, capture=True)
            if r.returncode == 0 and "ok" in r.stdout:
                return True
        except Exception:
            pass
        attempt += 1
        # Exponential backoff + jitter: base 1.5s, cap 10s, ±50% jitter
        base = min(1.5 * (1.5 ** attempt), 10.0)
        jitter = base * random.uniform(-0.5, 0.5)
        sleep_time = min(max(base + jitter, 0.5), time.time() - deadline + max_wait)
        if sleep_time > 0:
            time.sleep(sleep_time)
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────

def cmd_create(args: argparse.Namespace) -> int:
    state = load_state()
    if args.name in state["lieutenants"]:
        print(f"lieutenant '{args.name}' already exists", file=sys.stderr)
        return 1

    print(f"[lt] restoring VM from commit {args.commit_id}...")
    vm = _req("POST", "/vm/from_commit", {"commit_id": args.commit_id})
    vm_id = vm["vm_id"]
    print(f"[lt] vm_id={vm_id}")

    # Pre-fetch key once before probe loop (avoids repeated API calls per probe)
    print("[lt] fetching SSH key...")
    key = _fetch_key(vm_id)

    print("[lt] waiting for SSH...")
    if not ssh_probe(vm_id, key, max_wait=90):
        print("[lt] SSH unavailable after 90s — cleaning up", file=sys.stderr)
        _req("DELETE", f"/vm/{vm_id}")
        return 1

    # Write system prompt via stdin, not heredoc (safe with any characters)
    prompt = f"You are lieutenant '{args.name}'. Your role: {args.role}"
    r = ssh_cmd(vm_id, "cat > /tmp/pi-rpc-system-prompt.txt", key=key,
                check=False, capture=True, stdin=prompt)
    if r.returncode != 0:
        print(f"[lt] failed to write system prompt — cleaning up", file=sys.stderr)
        _req("DELETE", f"/vm/{vm_id}")
        return 1

    # Bootstrap RPC infra.
    # pi is launched via a wrapper script to avoid quoting nightmares with tmux.
    bootstrap = r"""
set -e
mkdir -p /tmp/pi-rpc
[ -p /tmp/pi-rpc/in ] || mkfifo /tmp/pi-rpc/in
touch /tmp/pi-rpc/out /tmp/pi-rpc/err

# Wrapper script reads prompt from file at launch time — no quoting needed
cat > /tmp/pi-rpc-launch.sh << 'LAUNCH_EOF'
#!/bin/sh
exec pi --mode rpc \
  --append-system-prompt "$(cat /tmp/pi-rpc-system-prompt.txt)" \
  < /tmp/pi-rpc/in \
  >> /tmp/pi-rpc/out \
  2>> /tmp/pi-rpc/err
LAUNCH_EOF
chmod +x /tmp/pi-rpc-launch.sh

tmux has-session -t pi-keeper 2>/dev/null || \
  tmux new-session -d -s pi-keeper 'sleep infinity > /tmp/pi-rpc/in'
tmux has-session -t pi-rpc 2>/dev/null || \
  tmux new-session -d -s pi-rpc '/tmp/pi-rpc-launch.sh'
""".strip()

    r = ssh_cmd(vm_id, bootstrap, key=key, check=False, capture=True)
    if r.returncode != 0:
        print(f"[lt] bootstrap failed: {r.stderr.strip()} — cleaning up", file=sys.stderr)
        _req("DELETE", f"/vm/{vm_id}")
        return 1

    # Give pi a moment to start, then handshake
    time.sleep(3)
    handshake = json.dumps({"type": "get_state", "id": "handshake-001"})
    try:
        ssh_write_fifo(vm_id, handshake, key=key)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"[lt] handshake failed: {e} — cleaning up", file=sys.stderr)
        _req("DELETE", f"/vm/{vm_id}")
        return 1

    state["lieutenants"][args.name] = {
        "name": args.name,
        "role": args.role,
        "vmId": vm_id,
        "commitId": args.commit_id,
        "status": "idle",
        "taskCount": 0,
        "createdAt": now_iso(),
        "lastActivityAt": now_iso(),
    }
    save_state(state)
    print(f"[lt] '{args.name}' ready — vm_id={vm_id}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    state = load_state()
    lt = state["lieutenants"].get(args.name)
    if not lt:
        print(f"lieutenant '{args.name}' not found", file=sys.stderr)
        return 1

    mode = args.mode
    if mode == "prompt" and lt.get("status") == "working":
        mode = "followUp"
        print("[lt] auto-converting to followUp (lieutenant is working)")

    task_id = f"task-{lt['taskCount'] + 1:04d}"
    # pi RPC wire format: type is the outer intent, mode is advisory
    payload = json.dumps({
        "type": "steer" if mode == "steer" else "prompt",
        "id": task_id,
        "message": args.message,
        "mode": mode,
    })

    key = get_key_path(lt["vmId"])
    ssh_write_fifo(lt["vmId"], payload, key=key)

    lt["status"] = "working"
    lt["taskCount"] += 1
    lt["lastActivityAt"] = now_iso()
    save_state(state)
    print(f"[lt] sent ({mode}) task {task_id} to '{args.name}'")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    state = load_state()
    lt = state["lieutenants"].get(args.name)
    if not lt:
        print(f"lieutenant '{args.name}' not found", file=sys.stderr)
        return 1

    key = get_key_path(lt["vmId"])
    base = _ssh_base(lt["vmId"], key)

    if args.follow:
        # Reconnect loop: retry with jitter on connection drop (like TS extension)
        attempt = 0
        while True:
            cmd = [*base, "tail -n 0 -f /tmp/pi-rpc/out"]
            try:
                subprocess.run(cmd)
            except KeyboardInterrupt:
                break
            except Exception:
                pass
            attempt += 1
            wait = min(1.5 * (1.5 ** attempt), 10.0) * random.uniform(0.5, 1.5)
            print(f"\n[lt] connection dropped — reconnecting in {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)
    else:
        tail = args.tail or 200
        subprocess.run([*base, f"tail -n {tail} /tmp/pi-rpc/out"])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state()
    lts = state["lieutenants"]
    if not lts:
        print("no lieutenants")
        return 0

    icons = {"working": "⟳", "idle": "●", "paused": "⏸", "error": "✗"}

    for lt in lts.values():
        live_status = lt.get("status", "?")
        if args.probe and lt.get("status") not in ("paused",):
            # Actually SSH in and check tmux
            try:
                key = get_key_path(lt["vmId"])
                r = ssh_cmd(lt["vmId"], "tmux has-session -t pi-rpc 2>/dev/null && echo alive || echo dead",
                            key=key, check=False, capture=True)
                live_status = "idle" if "alive" in r.stdout else "error"
                if live_status != lt.get("status"):
                    lt["status"] = live_status
            except Exception:
                live_status = "error"

        icon = icons.get(live_status, "○")
        print(f"{icon} {lt['name']} [{live_status}]  vm={lt['vmId'][:12]}  tasks={lt['taskCount']}")
        print(f"    role: {lt['role']}")
        print(f"    last: {lt.get('lastActivityAt','?')}")

    if args.probe:
        save_state(state)
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    state = load_state()
    lt = state["lieutenants"].get(args.name)
    if not lt:
        print(f"lieutenant '{args.name}' not found", file=sys.stderr)
        return 1
    if lt.get("status") == "working":
        print(f"[lt] cannot pause '{args.name}' while working — steer it first", file=sys.stderr)
        return 1
    _req("PATCH", f"/vm/{lt['vmId']}/state", {"state": "Paused"})
    lt["status"] = "paused"
    save_state(state)
    print(f"[lt] '{args.name}' paused")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    state = load_state()
    lt = state["lieutenants"].get(args.name)
    if not lt:
        print(f"lieutenant '{args.name}' not found", file=sys.stderr)
        return 1
    _req("PATCH", f"/vm/{lt['vmId']}/state", {"state": "Running"})
    print("[lt] waiting for SSH after resume...")

    key = get_key_path(lt["vmId"], force_refresh=True)
    if not ssh_probe(lt["vmId"], key, max_wait=30):
        lt["status"] = "error"
        save_state(state)
        print("[lt] SSH unavailable after resume — status=error", file=sys.stderr)
        return 1

    r = ssh_cmd(lt["vmId"], "tmux has-session -t pi-rpc 2>/dev/null && echo alive || echo dead",
                key=key, check=False, capture=True)
    if "dead" in r.stdout:
        lt["status"] = "error"
        save_state(state)
        print("[lt] pi-rpc session lost after resume — destroy and recreate", file=sys.stderr)
        return 1

    lt["status"] = "idle"
    lt["lastActivityAt"] = now_iso()
    save_state(state)
    print(f"[lt] '{args.name}' resumed")
    return 0


def cmd_destroy(args: argparse.Namespace) -> int:
    state = load_state()
    names = list(state["lieutenants"].keys()) if args.name == "*" else [args.name]
    for name in names:
        lt = state["lieutenants"].get(name)
        if not lt:
            print(f"[lt] '{name}' not found, skipping")
            continue
        vm_id = lt["vmId"]
        try:
            key = get_key_path(vm_id)
            ssh_cmd(vm_id, "tmux kill-session -t pi-rpc 2>/dev/null; tmux kill-session -t pi-keeper 2>/dev/null",
                    key=key, check=False, capture=True)
        except Exception:
            pass
        # Some backends require Running state before delete
        if lt.get("status") == "paused":
            try:
                _req("PATCH", f"/vm/{vm_id}/state", {"state": "Running"})
                time.sleep(2)
            except Exception:
                pass
        _req("DELETE", f"/vm/{vm_id}")
        del state["lieutenants"][name]
        save_state(state)
        print(f"[lt] '{name}' destroyed (vm_id={vm_id})")
    return 0


def cmd_discover(_args: argparse.Namespace) -> int:
    infra_url = os.environ.get("VERS_INFRA_URL", "").strip()
    auth_token = os.environ.get("VERS_AUTH_TOKEN", "").strip()
    if not infra_url:
        print("VERS_INFRA_URL not set — registry discovery requires it", file=sys.stderr)
        return 2

    registry_req = urllib.request.Request(
        f"{infra_url}/registry/vms",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    with urllib.request.urlopen(registry_req, timeout=30) as resp:
        entries = json.loads(resp.read())

    state = load_state()
    found = [e for e in entries if e.get("registeredBy") == "vers-lieutenant"]
    print(f"[lt] {len(found)} lieutenant entries in registry")

    for entry in found:
        vm_id = entry.get("id", "")
        name = entry.get("metadata", {}).get("agentId", entry.get("name", vm_id))
        if name in state["lieutenants"]:
            print(f"  {name}: already tracked")
            continue
        try:
            key = _fetch_key(vm_id)
            r = ssh_cmd(vm_id, "tmux has-session -t pi-rpc 2>/dev/null && echo alive || echo dead",
                        key=key, check=False, capture=True)
            status = "idle" if "alive" in r.stdout else "error"
        except Exception:
            status = "error"
        state["lieutenants"][name] = {
            "name": name,
            "role": entry.get("metadata", {}).get("role", ""),
            "vmId": vm_id,
            "status": status,
            "taskCount": 0,
            "createdAt": entry.get("metadata", {}).get("createdAt", now_iso()),
            "lastActivityAt": now_iso(),
        }
        save_state(state)
        print(f"  {name}: vm={vm_id} status={status}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vers lieutenant manager — pure ssh/subprocess, no pi plugin",
        epilog=f"State: {STATE_PATH}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lt-create", help="create + bootstrap a lieutenant VM")
    p.add_argument("name")
    p.add_argument("role")
    p.add_argument("commit_id")

    p = sub.add_parser("lt-send", help="send a message to a lieutenant")
    p.add_argument("name")
    p.add_argument("message")
    p.add_argument("--mode", choices=["prompt", "steer", "followUp"], default="prompt")

    p = sub.add_parser("lt-read", help="read output from a lieutenant")
    p.add_argument("name")
    p.add_argument("--tail", type=int, default=None, help="last N lines (default 200)")
    p.add_argument("--follow", "-f", action="store_true", help="stream live, auto-reconnects")

    p = sub.add_parser("lt-status", help="show all tracked lieutenants")
    p.add_argument("--probe", action="store_true", help="SSH in to verify actual tmux state")

    p = sub.add_parser("lt-pause", help="pause a lieutenant VM")
    p.add_argument("name")

    p = sub.add_parser("lt-resume", help="resume a paused lieutenant VM")
    p.add_argument("name")

    p = sub.add_parser("lt-destroy", help="kill tmux + delete VM")
    p.add_argument("name", help="lieutenant name or '*' for all")

    sub.add_parser("lt-discover", help="discover lieutenants from registry (VERS_INFRA_URL)")

    args = parser.parse_args()
    dispatch = {
        "lt-create": cmd_create,
        "lt-send": cmd_send,
        "lt-read": cmd_read,
        "lt-status": cmd_status,
        "lt-pause": cmd_pause,
        "lt-resume": cmd_resume,
        "lt-destroy": cmd_destroy,
        "lt-discover": cmd_discover,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
