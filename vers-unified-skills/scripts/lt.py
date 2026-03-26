#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
Vers lieutenant manager — pure ssh + subprocess, no pi plugin required.

State: ~/.vers/lieutenants.json  (override: VERS_LT_STATE)

Communication pattern (identical to pi-vers extension, just shell not TS):
  - input:  ssh vm "cat > /tmp/pi-rpc/in"
  - output: ssh vm "tail -n +0 -f /tmp/pi-rpc/out"
  - keepalive: tmux session 'pi-keeper' holds FIFO write-end open
  - worker:   tmux session 'pi-rpc'    runs pi --mode rpc
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────────────────────

STATE_PATH = Path(os.environ.get("VERS_LT_STATE", Path.home() / ".vers/lieutenants.json"))
VERS_API_BASE = "https://api.vers.sh/api/v1"

def vers_api_key() -> str:
    k = os.environ.get("VERS_API_KEY", "").strip()
    if not k:
        print("VERS_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    return k


def _req(method: str, path: str, body: dict | None = None) -> dict:
    import urllib.request, urllib.error
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
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"lieutenants": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# SSH helpers
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

KEY_CACHE = Path("/tmp/vers-ssh-keys")


def get_key_path(vm_id: str) -> Path:
    KEY_CACHE.mkdir(exist_ok=True)
    key_path = KEY_CACHE / f"{vm_id}.pem"
    if not key_path.exists():
        data = _req("GET", f"/vm/{vm_id}/ssh_key")
        key_path.write_text(data["ssh_private_key"])
        key_path.chmod(0o600)
    return key_path


def ssh_cmd(vm_id: str, command: str, *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    key = get_key_path(vm_id)
    cmd = ["ssh", *SSH_OPTS, "-i", str(key), f"root@{vm_id}.vm.vers.sh", command]
    return subprocess.run(cmd, capture_output=capture, text=True, check=check)


def ssh_write_fifo(vm_id: str, json_line: str) -> None:
    """Write a newline-terminated JSON line to the pi-rpc FIFO."""
    key = get_key_path(vm_id)
    cmd = ["ssh", *SSH_OPTS, "-i", str(key), f"root@{vm_id}.vm.vers.sh",
           "cat > /tmp/pi-rpc/in"]
    proc = subprocess.run(cmd, input=json_line + "\n", text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ssh write failed: {proc.stderr.strip()}")


def ssh_probe(vm_id: str, max_wait: int = 60) -> bool:
    """Wait for SSH to become available."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = ssh_cmd(vm_id, "echo ok", check=False, capture=True)
            if r.returncode == 0 and "ok" in r.stdout:
                return True
        except Exception:
            pass
        time.sleep(2)
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
    print(f"[lt] vm_id={vm_id}. waiting for SSH...")

    if not ssh_probe(vm_id, max_wait=90):
        print(f"[lt] SSH unavailable after 90s — cleaning up", file=sys.stderr)
        _req("DELETE", f"/vm/{vm_id}")
        return 1

    # Write system prompt to VM
    prompt_path = "/tmp/pi-rpc-system-prompt.txt"
    prompt = f"You are lieutenant '{args.name}'. Your role: {args.role}"
    ssh_cmd(vm_id, f"cat > {prompt_path} << 'EOFPROMPT'\n{prompt}\nEOFPROMPT")

    # Bootstrap RPC infra if not already present
    bootstrap = textwrap.dedent("""
        mkdir -p /tmp/pi-rpc
        [ -p /tmp/pi-rpc/in ] || mkfifo /tmp/pi-rpc/in
        touch /tmp/pi-rpc/out /tmp/pi-rpc/err
        tmux has-session -t pi-keeper 2>/dev/null || \
          tmux new-session -d -s pi-keeper 'sleep infinity > /tmp/pi-rpc/in'
        if ! tmux has-session -t pi-rpc 2>/dev/null; then
          tmux new-session -d -s pi-rpc \
            "pi --mode rpc --append-system-prompt \"$(cat /tmp/pi-rpc-system-prompt.txt)\" \
             < /tmp/pi-rpc/in >> /tmp/pi-rpc/out 2>> /tmp/pi-rpc/err"
        fi
    """).strip()
    ssh_cmd(vm_id, bootstrap)

    # Handshake
    time.sleep(2)
    handshake = json.dumps({"type": "get_state", "id": "handshake-001"})
    try:
        ssh_write_fifo(vm_id, handshake)
    except RuntimeError as e:
        print(f"[lt] handshake failed: {e}", file=sys.stderr)
        return 1

    # Persist
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

    task_id = f"task-{lt['taskCount'] + 1:04d}"
    mode = args.mode

    # Auto-convert prompt→followUp if working
    if mode == "prompt" and lt.get("status") == "working":
        mode = "followUp"
        print(f"[lt] auto-converting to followUp (lieutenant is working)")

    payload = json.dumps({
        "type": "prompt" if mode in ("prompt", "followUp") else "steer",
        "id": task_id,
        "message": args.message,
        "mode": mode,
    })

    ssh_write_fifo(lt["vmId"], payload)

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
    if args.follow:
        cmd = ["ssh", *SSH_OPTS, "-i", str(key), f"root@{lt['vmId']}.vm.vers.sh",
               "tail -n 0 -f /tmp/pi-rpc/out"]
    else:
        tail = args.tail if args.tail else 200
        cmd = ["ssh", *SSH_OPTS, "-i", str(key), f"root@{lt['vmId']}.vm.vers.sh",
               f"tail -n {tail} /tmp/pi-rpc/out"]
    subprocess.run(cmd)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    state = load_state()
    lts = state["lieutenants"]
    if not lts:
        print("no lieutenants")
        return 0
    for lt in lts.values():
        icon = {"working": "⟳", "idle": "●", "paused": "⏸", "error": "✗"}.get(lt.get("status", ""), "○")
        print(f"{icon} {lt['name']} [{lt.get('status','?')}]  vm={lt['vmId'][:12]}  tasks={lt['taskCount']}")
        print(f"    role: {lt['role']}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    state = load_state()
    lt = state["lieutenants"].get(args.name)
    if not lt:
        print(f"lieutenant '{args.name}' not found", file=sys.stderr)
        return 1
    if lt.get("status") == "working":
        print(f"[lt] cannot pause '{args.name}' while working — steer it to stop first", file=sys.stderr)
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
    print(f"[lt] waiting for SSH after resume...")
    if not ssh_probe(lt["vmId"], max_wait=30):
        lt["status"] = "error"
        save_state(state)
        print(f"[lt] SSH unavailable after resume — status=error", file=sys.stderr)
        return 1
    # Verify tmux session
    r = ssh_cmd(lt["vmId"], "tmux has-session -t pi-rpc 2>/dev/null && echo alive || echo dead",
                check=False, capture=True)
    if "dead" in r.stdout:
        lt["status"] = "error"
        save_state(state)
        print(f"[lt] pi-rpc session lost — status=error. Destroy and recreate.", file=sys.stderr)
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
        # Kill remote sessions best-effort
        try:
            ssh_cmd(vm_id, "tmux kill-session -t pi-rpc 2>/dev/null; tmux kill-session -t pi-keeper 2>/dev/null",
                    check=False, capture=True)
        except Exception:
            pass
        # If paused, resume first (some backends require Running for delete)
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

    import urllib.request
    req = urllib.request.Request(
        f"{infra_url}/registry/vms",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        entries = json.loads(resp.read())

    state = load_state()
    found = [e for e in entries if e.get("registeredBy") == "vers-lieutenant"]
    print(f"[lt] found {len(found)} lieutenant entries in registry")
    for entry in found:
        vm_id = entry.get("id", "")
        name = entry.get("metadata", {}).get("agentId", entry.get("name", vm_id))
        if name in state["lieutenants"]:
            print(f"  {name}: already tracked")
            continue
        # Probe VM
        try:
            r = ssh_cmd(vm_id, "tmux has-session -t pi-rpc 2>/dev/null && echo alive || echo dead",
                        check=False, capture=True)
            alive = "alive" in r.stdout
        except Exception:
            alive = False
        status = "idle" if alive else "error"
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
        print(f"  {name}: reconnected vm={vm_id} status={status}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vers lieutenant manager — pure ssh/subprocess, no pi plugin",
        epilog=f"State: {STATE_PATH}"
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
    p.add_argument("--follow", "-f", action="store_true", help="stream live (tail -f)")

    sub.add_parser("lt-status", help="show all tracked lieutenants")

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
