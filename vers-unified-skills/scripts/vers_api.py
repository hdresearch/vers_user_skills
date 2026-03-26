#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Vers REST API wrapper — endpoints from docs.vers.sh/llms-full.txt"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path

BASE = "https://api.vers.sh/api/v1"


def api_key() -> str:
    k = os.environ.get("VERS_API_KEY", "").strip()
    if not k:
        print("VERS_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    return k


def req(method: str, path: str, body: dict | None = None) -> dict | list:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)


def jprint(data: object) -> None:
    print(json.dumps(data, indent=2))


def cmd_vms(_args: argparse.Namespace) -> int:
    jprint(req("GET", "/vms"))
    return 0


def cmd_vm_status(args: argparse.Namespace) -> int:
    jprint(req("GET", f"/vm/{args.vm_id}/status"))
    return 0


def cmd_vm_new_root(args: argparse.Namespace) -> int:
    qs = "?wait_boot=true" if args.wait_boot else ""
    body: dict = {
        "vm_config": {
            "mem_size_mib": args.mem,
            "vcpu_count": args.vcpu,
            "fs_size_mib": args.disk,
        }
    }
    jprint(req("POST", f"/vm/new_root{qs}", body))
    return 0


def cmd_vm_from_commit(args: argparse.Namespace) -> int:
    jprint(req("POST", "/vm/from_commit", {"commit_id": args.commit_id}))
    return 0


def cmd_vm_commit(args: argparse.Namespace) -> int:
    jprint(req("POST", f"/vm/{args.vm_id}/commit"))
    return 0


def cmd_vm_branch(args: argparse.Namespace) -> int:
    if args.commit_id:
        jprint(req("POST", f"/vm/branch/by_commit/{args.commit_id}"))
    elif args.tag:
        jprint(req("POST", f"/vm/branch/by_tag/{urllib.parse.quote(args.tag)}"))
    elif args.vm_id:
        jprint(req("POST", f"/vm/{args.vm_id}/branch"))
    else:
        print("need --vm-id, --commit-id, or --tag", file=sys.stderr)
        return 2
    return 0


def cmd_vm_state(args: argparse.Namespace) -> int:
    state = args.state
    if state not in ("Paused", "Running"):
        print(f"invalid state {state!r}: must be Paused or Running", file=sys.stderr)
        return 2
    jprint(req("PATCH", f"/vm/{args.vm_id}/state", {"state": state}))
    return 0


def cmd_vm_delete(args: argparse.Namespace) -> int:
    jprint(req("DELETE", f"/vm/{args.vm_id}"))
    print(f"deleted {args.vm_id}")
    return 0


def cmd_vm_ssh_key(args: argparse.Namespace) -> int:
    data = req("GET", f"/vm/{args.vm_id}/ssh_key")
    key_text = data.get("ssh_private_key", "")
    key_path = Path(f"/tmp/vers-{args.vm_id[:12]}.pem")
    key_path.write_text(key_text)
    key_path.chmod(0o600)
    print(f"SSH key written to {key_path}")
    print(f"ssh command:")
    print(
        f'  ssh -i {key_path} -o StrictHostKeyChecking=no '
        f'-o "ProxyCommand=openssl s_client -connect %h:443 -servername %h -quiet 2>/dev/null" '
        f"root@{args.vm_id}.vm.vers.sh"
    )
    return 0


def cmd_commits(_args: argparse.Namespace) -> int:
    jprint(req("GET", "/commits"))
    return 0


def cmd_commits_public(_args: argparse.Namespace) -> int:
    jprint(req("GET", "/commits/public"))
    return 0


def cmd_commit_set_public(args: argparse.Namespace) -> int:
    jprint(req("PATCH", f"/commits/{args.commit_id}", {"is_public": args.public}))
    return 0


def cmd_commit_delete(args: argparse.Namespace) -> int:
    jprint(req("DELETE", f"/commits/{args.commit_id}"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Vers API wrapper (docs.vers.sh/llms-full.txt)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("vms", help="list all VMs")

    p = sub.add_parser("vm-status", help="GET /vm/{id}/status")
    p.add_argument("vm_id")

    p = sub.add_parser("vm-new", help="POST /vm/new_root")
    p.add_argument("--mem", type=int, default=4096)
    p.add_argument("--vcpu", type=int, default=2)
    p.add_argument("--disk", type=int, default=8192)
    p.add_argument("--wait-boot", action="store_true", default=True)

    p = sub.add_parser("vm-from-commit", help="POST /vm/from_commit")
    p.add_argument("commit_id")

    p = sub.add_parser("vm-commit", help="POST /vm/{id}/commit")
    p.add_argument("vm_id")

    p = sub.add_parser("vm-branch", help="branch a VM or commit")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--vm-id")
    group.add_argument("--commit-id")
    group.add_argument("--tag")

    p = sub.add_parser("vm-state", help="PATCH /vm/{id}/state")
    p.add_argument("vm_id")
    p.add_argument("state", choices=["Paused", "Running"])

    p = sub.add_parser("vm-delete", help="DELETE /vm/{id}")
    p.add_argument("vm_id")

    p = sub.add_parser("vm-ssh-key", help="fetch SSH key for a VM")
    p.add_argument("vm_id")

    sub.add_parser("commits", help="list your commits")
    sub.add_parser("commits-public", help="list public commits")

    p = sub.add_parser("commit-set-public", help="PATCH /commits/{id}")
    p.add_argument("commit_id")
    p.add_argument("--public", action="store_true", default=True)

    p = sub.add_parser("commit-delete", help="DELETE /commits/{id}")
    p.add_argument("commit_id")

    args = parser.parse_args()

    dispatch = {
        "vms": cmd_vms,
        "vm-status": cmd_vm_status,
        "vm-new": cmd_vm_new_root,
        "vm-from-commit": cmd_vm_from_commit,
        "vm-commit": cmd_vm_commit,
        "vm-branch": cmd_vm_branch,
        "vm-state": cmd_vm_state,
        "vm-delete": cmd_vm_delete,
        "vm-ssh-key": cmd_vm_ssh_key,
        "commits": cmd_commits,
        "commits-public": cmd_commits_public,
        "commit-set-public": cmd_commit_set_public,
        "commit-delete": cmd_commit_delete,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
