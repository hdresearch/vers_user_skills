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
from pathlib import Path

REPO = Path("/tmp/pi-vers")


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
    proc = subprocess.run(cmd, cwd=REPO)
    return proc.returncode


def list_docs() -> int:
    files = sorted(str(p.relative_to(REPO)) for p in (REPO / "docs").rglob("*.md"))
    print(json.dumps(files, indent=2))
    return 0


def list_skills() -> int:
    skills_root = REPO / "skills"
    files: list[str] = []
    for p in skills_root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".md":
            files.append(str(p.relative_to(REPO)))
    print(json.dumps(sorted(files), indent=2))
    return 0


def list_extensions() -> int:
    files = sorted(str(p.relative_to(REPO)) for p in (REPO / "extensions").rglob("*.ts"))
    print(json.dumps(files, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="pi-vers ops wrapper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build")
    sub.add_parser("list-docs")
    sub.add_parser("list-skills")
    sub.add_parser("list-extensions")

    args = parser.parse_args()

    if not REPO.exists():
        print(f"missing repo: {REPO}", file=sys.stderr)
        return 2

    if args.cmd == "build":
        return run(["npm", "run", "build"])
    if args.cmd == "list-docs":
        return list_docs()
    if args.cmd == "list-skills":
        return list_skills()
    if args.cmd == "list-extensions":
        return list_extensions()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
