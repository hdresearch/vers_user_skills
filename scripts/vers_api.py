#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Vers REST API wrapper — endpoints from docs.vers.sh/llms-full.txt.

Covers the surface the sibling skills (offload-to-vers, onboard-to-vers,
vers-api-reference) reference: VM lifecycle, commits + parents, commit
tags, shell-auth, VM metadata, disk resize.

Surface staged for a v2 pass (not yet exposed here): repositories,
public_repositories, branch/by_ref, vm/files, vm/exec, vm/logs, domains,
env_vars. Fall through to raw `curl` + Bearer for those until then.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.vers.sh/api/v1"
AUTH_BASE = "https://vers.sh/api"


def api_key() -> str:
    k = os.environ.get("VERS_API_KEY", "").strip()
    if not k:
        print("VERS_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    return k


def req(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    base: str = API_BASE,
    authed: bool = True,
) -> dict | list:
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if authed:
        headers["Authorization"] = f"Bearer {api_key()}"
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
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


# ───────────────────────── VM lifecycle ─────────────────────────

def cmd_vms(_args: argparse.Namespace) -> int:
    jprint(req("GET", "/vms"))
    return 0


def cmd_vm_status(args: argparse.Namespace) -> int:
    jprint(req("GET", f"/vm/{args.vm_id}/status"))
    return 0


def cmd_vm_metadata(args: argparse.Namespace) -> int:
    jprint(req("GET", f"/vm/{args.vm_id}/metadata"))
    return 0


def cmd_vm_new_root(args: argparse.Namespace) -> int:
    qs = "?wait_boot=true" if args.wait_boot else "?wait_boot=false"
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


def cmd_vm_disk_resize(args: argparse.Namespace) -> int:
    jprint(req("PATCH", f"/vm/{args.vm_id}/disk", {"fs_size_mib": args.size}))
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
    print("ssh command:")
    print(
        f"  ssh -i {key_path} -o StrictHostKeyChecking=no "
        f'-o "ProxyCommand=openssl s_client -connect %h:443 -servername %h -quiet 2>/dev/null" '
        f"root@{args.vm_id}.vm.vers.sh"
    )
    return 0


# ───────────────────────── Commits ─────────────────────────

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


def cmd_commit_parents(args: argparse.Namespace) -> int:
    jprint(req("GET", f"/vm/commits/{args.commit_id}/parents"))
    return 0


# ───────────────────────── Commit tags ─────────────────────────

def cmd_commit_tags(_args: argparse.Namespace) -> int:
    jprint(req("GET", "/commit_tags"))
    return 0


def cmd_commit_tag_get(args: argparse.Namespace) -> int:
    jprint(req("GET", f"/commit_tags/{urllib.parse.quote(args.tag)}"))
    return 0


def cmd_commit_tag_create(args: argparse.Namespace) -> int:
    jprint(req("POST", "/commit_tags", {"tag_name": args.tag, "commit_id": args.commit_id}))
    return 0


def cmd_commit_tag_update(args: argparse.Namespace) -> int:
    jprint(req("PATCH", f"/commit_tags/{urllib.parse.quote(args.tag)}", {"commit_id": args.commit_id}))
    return 0


def cmd_commit_tag_delete(args: argparse.Namespace) -> int:
    jprint(req("DELETE", f"/commit_tags/{urllib.parse.quote(args.tag)}"))
    return 0


# ───────────────────────── Shell auth ─────────────────────────

def cmd_auth_init(args: argparse.Namespace) -> int:
    pub = Path(args.pubkey).read_text().strip() if args.pubkey else args.pubkey_literal
    if not pub:
        print("provide --pubkey PATH or --pubkey-literal STRING", file=sys.stderr)
        return 2
    jprint(req("POST", "/shell-auth", {"email": args.email, "ssh_public_key": pub}, authed=False, base=AUTH_BASE))
    return 0


def cmd_auth_verify(args: argparse.Namespace) -> int:
    pub = Path(args.pubkey).read_text().strip() if args.pubkey else args.pubkey_literal
    if not pub:
        print("provide --pubkey PATH or --pubkey-literal STRING", file=sys.stderr)
        return 2
    jprint(req("POST", "/shell-auth/verify-key", {"email": args.email, "ssh_public_key": pub}, authed=False, base=AUTH_BASE))
    return 0


def cmd_auth_create_key(args: argparse.Namespace) -> int:
    pub = Path(args.pubkey).read_text().strip() if args.pubkey else args.pubkey_literal
    if not pub:
        print("provide --pubkey PATH or --pubkey-literal STRING", file=sys.stderr)
        return 2
    body = {
        "email": args.email,
        "ssh_public_key": pub,
        "label": args.label,
    }
    if args.org_name:
        body["org_name"] = args.org_name
    jprint(req("POST", "/shell-auth/api-keys", body, authed=False, base=AUTH_BASE))
    return 0


def cmd_auth_verify_pubkey(args: argparse.Namespace) -> int:
    pub = Path(args.pubkey).read_text().strip() if args.pubkey else args.pubkey_literal
    if not pub:
        print("provide --pubkey PATH or --pubkey-literal STRING", file=sys.stderr)
        return 2
    jprint(req("POST", "/shell-auth/verify-public-key", {"ssh_public_key": pub}, authed=False, base=AUTH_BASE))
    return 0


# ───────────────────────── Dispatch ─────────────────────────

def _add_pubkey_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pubkey", help="path to SSH public key file (e.g. ~/.ssh/id_ed25519.pub)")
    g.add_argument("--pubkey-literal", help="SSH public key as a string")


def main() -> int:
    parser = argparse.ArgumentParser(description="Vers API wrapper (docs.vers.sh/llms-full.txt)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --- VM lifecycle ---
    sub.add_parser("vms", help="GET /vms — list all VMs")

    p = sub.add_parser("vm-status", help="GET /vm/{id}/status")
    p.add_argument("vm_id")

    p = sub.add_parser("vm-metadata", help="GET /vm/{id}/metadata")
    p.add_argument("vm_id")

    p = sub.add_parser("vm-new", help="POST /vm/new_root")
    p.add_argument("--mem", type=int, default=4096)
    p.add_argument("--vcpu", type=int, default=2)
    p.add_argument("--disk", type=int, default=8192)
    p.add_argument("--wait-boot", action=argparse.BooleanOptionalAction, default=True,
                   help="wait for SSH to come up before returning (default: true; --no-wait-boot to disable)")

    p = sub.add_parser("vm-from-commit", help="POST /vm/from_commit")
    p.add_argument("commit_id")

    p = sub.add_parser("vm-commit", help="POST /vm/{id}/commit")
    p.add_argument("vm_id")

    p = sub.add_parser("vm-branch", help="branch a VM or commit or tag")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--vm-id")
    group.add_argument("--commit-id")
    group.add_argument("--tag")

    p = sub.add_parser("vm-state", help="PATCH /vm/{id}/state")
    p.add_argument("vm_id")
    p.add_argument("state", choices=["Paused", "Running"])

    p = sub.add_parser("vm-disk-resize", help="PATCH /vm/{id}/disk")
    p.add_argument("vm_id")
    p.add_argument("size", type=int, help="new fs_size_mib")

    p = sub.add_parser("vm-delete", help="DELETE /vm/{id}")
    p.add_argument("vm_id")

    p = sub.add_parser("vm-ssh-key", help="fetch SSH key for a VM")
    p.add_argument("vm_id")

    # --- Commits ---
    sub.add_parser("commits", help="GET /commits")
    sub.add_parser("commits-public", help="GET /commits/public")

    p = sub.add_parser("commit-set-public", help="PATCH /commits/{id} — toggle is_public")
    p.add_argument("commit_id")
    p.add_argument("--public", action=argparse.BooleanOptionalAction, default=True,
                   help="default: true; --no-public to flip back to private")

    p = sub.add_parser("commit-delete", help="DELETE /commits/{id}")
    p.add_argument("commit_id")

    p = sub.add_parser("commit-parents", help="GET /vm/commits/{id}/parents")
    p.add_argument("commit_id")

    # --- Commit tags ---
    sub.add_parser("commit-tags", help="GET /commit_tags — list all tags")

    p = sub.add_parser("commit-tag-get", help="GET /commit_tags/{name}")
    p.add_argument("tag")

    p = sub.add_parser("commit-tag-create", help="POST /commit_tags")
    p.add_argument("tag")
    p.add_argument("commit_id")

    p = sub.add_parser("commit-tag-update", help="PATCH /commit_tags/{name} — point tag at a different commit")
    p.add_argument("tag")
    p.add_argument("commit_id")

    p = sub.add_parser("commit-tag-delete", help="DELETE /commit_tags/{name}")
    p.add_argument("tag")

    # --- Shell auth ---
    p = sub.add_parser("auth-init", help="POST /api/shell-auth — send verification email")
    p.add_argument("email")
    _add_pubkey_args(p)

    p = sub.add_parser("auth-verify", help="POST /api/shell-auth/verify-key — poll until verified:true")
    p.add_argument("email")
    _add_pubkey_args(p)

    p = sub.add_parser("auth-create-key", help="POST /api/shell-auth/api-keys — mint API key (shown once)")
    p.add_argument("email")
    p.add_argument("--label", required=True)
    p.add_argument("--org-name", help="defaults to user's first org")
    _add_pubkey_args(p)

    p = sub.add_parser("auth-verify-pubkey", help="POST /api/shell-auth/verify-public-key — lookup key binding")
    _add_pubkey_args(p)

    args = parser.parse_args()

    dispatch = {
        # VM
        "vms": cmd_vms,
        "vm-status": cmd_vm_status,
        "vm-metadata": cmd_vm_metadata,
        "vm-new": cmd_vm_new_root,
        "vm-from-commit": cmd_vm_from_commit,
        "vm-commit": cmd_vm_commit,
        "vm-branch": cmd_vm_branch,
        "vm-state": cmd_vm_state,
        "vm-disk-resize": cmd_vm_disk_resize,
        "vm-delete": cmd_vm_delete,
        "vm-ssh-key": cmd_vm_ssh_key,
        # Commits
        "commits": cmd_commits,
        "commits-public": cmd_commits_public,
        "commit-set-public": cmd_commit_set_public,
        "commit-delete": cmd_commit_delete,
        "commit-parents": cmd_commit_parents,
        # Commit tags
        "commit-tags": cmd_commit_tags,
        "commit-tag-get": cmd_commit_tag_get,
        "commit-tag-create": cmd_commit_tag_create,
        "commit-tag-update": cmd_commit_tag_update,
        "commit-tag-delete": cmd_commit_tag_delete,
        # Shell auth
        "auth-init": cmd_auth_init,
        "auth-verify": cmd_auth_verify,
        "auth-create-key": cmd_auth_create_key,
        "auth-verify-pubkey": cmd_auth_verify_pubkey,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
