#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path("/tmp/reef")


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
    proc = subprocess.run(cmd, cwd=REPO)
    return proc.returncode


def health(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = r.read().decode("utf-8", errors="replace")
            print(body)
            return 0
    except Exception as e:  # noqa: BLE001
        print(f"health check failed: {e}", file=sys.stderr)
        return 1


def list_services() -> int:
    services_dir = REPO / "services"
    rows = sorted([p.name for p in services_dir.iterdir() if p.is_dir()])
    print(json.dumps(rows, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="reef ops wrapper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start")
    sub.add_parser("test")
    sub.add_parser("lint")
    sub.add_parser("list-services")
    p = sub.add_parser("health")
    p.add_argument("--url", default="http://localhost:3000/health")

    args = parser.parse_args()

    if not REPO.exists():
        print(f"missing repo: {REPO}", file=sys.stderr)
        return 2

    if args.cmd == "start":
        return run(["bun", "run", "start"])
    if args.cmd == "test":
        return run(["bun", "test"])
    if args.cmd == "lint":
        return run(["bun", "run", "lint"])
    if args.cmd == "list-services":
        return list_services()
    if args.cmd == "health":
        return health(args.url)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
