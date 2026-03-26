#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path("/tmp/vers-fleets")


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
    proc = subprocess.run(cmd, cwd=REPO)
    return proc.returncode


def bun_cli(subcommand: str, extra: list[str]) -> int:
    cmd = ["bun", "src/cli.js", subcommand, *extra]
    return run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="vers-fleets wrapper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("test", help="run build+test checks")

    for name in ("build-root", "build-golden", "provision", "raw"):
        p = sub.add_parser(name, help=f"run {name} through bun cli")
        p.add_argument("extra", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    if not REPO.exists():
        print(f"missing repo: {REPO}", file=sys.stderr)
        return 2

    if args.cmd == "test":
        if run(["npm", "run", "build"]) != 0:
            return 1
        return run(["npm", "test"])

    extra = getattr(args, "extra", [])
    if extra and extra[0] == "--":
        extra = extra[1:]

    if args.cmd == "raw":
        if not extra:
            print("raw requires args, e.g. raw -- build-root --private", file=sys.stderr)
            return 2
        return run(["bun", "src/cli.js", *extra])

    return bun_cli(args.cmd, extra)


if __name__ == "__main__":
    raise SystemExit(main())
