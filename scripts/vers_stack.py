#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PUBLIC_ROOT_COMMIT = "5d9c6176-2e9e-4b38-8fc2-f7e0fb3507ce"
PUBLIC_GOLDEN_COMMIT = "d2fedfa3-a835-4745-9b50-0e94d347d26b"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = PROJECT_ROOT.parent


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def run(cmd: list[str], cwd: Path) -> int:
    print(f"$ {shell_join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd)
    return proc.returncode


def capture(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout or "").strip()


def resolve_repo(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"vers-fleets repo not found: {path}")
        return path

    env_repo = os.environ.get("VERS_FLEETS_REPO", "").strip()
    if env_repo:
        env_candidate = Path(env_repo).expanduser().resolve()
        if env_candidate.exists():
            return env_candidate

    candidates = [
        SCRATCH_ROOT / "vers-fleets",
        Path("/tmp/vers-fleets"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError("vers-fleets repo not found. Clone into scratch_space or set VERS_FLEETS_REPO")


def sibling_repo(name: str) -> Path | None:
    path = SCRATCH_ROOT / name
    if path.exists() and (path / ".git").exists():
        return path
    return None


def source_flags(
    *,
    use_local_sources: bool,
    reef_path: str | None,
    pi_vers_path: str | None,
    reef_ref: str | None,
    pi_vers_ref: str | None,
    punkin_ref: str | None,
) -> list[str]:
    flags: list[str] = []

    if reef_path:
        flags += ["--reef-path", str(Path(reef_path).expanduser().resolve())]
    elif use_local_sources:
        local_reef = sibling_repo("reef")
        if local_reef:
            flags += ["--reef-path", str(local_reef)]
    elif reef_ref:
        flags += ["--reef-ref", reef_ref]

    if pi_vers_path:
        flags += ["--pi-vers-path", str(Path(pi_vers_path).expanduser().resolve())]
    elif use_local_sources:
        local_pi_vers = sibling_repo("pi-vers")
        if local_pi_vers:
            flags += ["--pi-vers-path", str(local_pi_vers)]
    elif pi_vers_ref:
        flags += ["--pi-vers-ref", pi_vers_ref]

    # If explicit paths are set, refs are ignored upstream anyway. We only pass refs
    # when a corresponding path is not present.
    if punkin_ref:
        flags += ["--punkin-ref", punkin_ref]

    return flags


def auth_flags(email: str | None, force_shell_auth: bool) -> list[str]:
    flags: list[str] = []
    if email:
        flags += ["--email", email]
    if force_shell_auth:
        flags.append("--force-shell-auth")
    return flags


def ensure_vers_fleets_deps(repo: Path) -> int:
    marker = repo / "node_modules" / "@hdresearch" / "pi-v"
    if marker.exists():
        return 0
    print("[vers-stack] installing vers-fleets dependencies (bun install)")
    return run(["bun", "install"], cwd=repo)


def bun_cli(repo: Path, args: list[str]) -> int:
    if ensure_vers_fleets_deps(repo) != 0:
        return 1
    return run(["bun", "src/cli.js", *args], cwd=repo)


def cmd_doctor(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.vers_fleets_repo)
    reef = sibling_repo("reef")
    pi_vers = sibling_repo("pi-vers")
    punkin = sibling_repo("punkin-pi")

    def branch(path: Path | None) -> str | None:
        if not path:
            return None
        code, out = capture(["git", "branch", "--show-current"], cwd=path)
        return out if code == 0 else None

    code_bun, bun_version = capture(["bun", "--version"])
    code_uv, uv_version = capture(["uv", "--version"])

    payload = {
        "vers_fleets_repo": str(repo),
        "vers_fleets_branch": branch(repo),
        "local_sources": {
            "reef": str(reef) if reef else None,
            "reef_branch": branch(reef),
            "pi_vers": str(pi_vers) if pi_vers else None,
            "pi_vers_branch": branch(pi_vers),
            "punkin_pi": str(punkin) if punkin else None,
            "punkin_pi_branch": branch(punkin),
        },
        "tooling": {
            "bun": bun_version if code_bun == 0 else None,
            "uv": uv_version if code_uv == 0 else None,
        },
        "public_commits": {
            "root": PUBLIC_ROOT_COMMIT,
            "golden": PUBLIC_GOLDEN_COMMIT,
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.vers_fleets_repo)
    if run(["npm", "run", "build"], cwd=repo) != 0:
        return 1
    return run(["npm", "test"], cwd=repo)


def cmd_build_root(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.vers_fleets_repo)
    visibility = "--public" if args.public else "--private"
    cmd = [
        "build-root",
        visibility,
        "--out-dir",
        args.out_dir,
        "--root-name",
        args.root_name,
        *auth_flags(args.email, args.force_shell_auth),
        *source_flags(
            use_local_sources=not args.no_local_sources,
            reef_path=args.reef_path,
            pi_vers_path=args.pi_vers_path,
            reef_ref=args.reef_ref,
            pi_vers_ref=args.pi_vers_ref,
            punkin_ref=args.punkin_ref,
        ),
    ]
    return bun_cli(repo, cmd)


def cmd_build_golden(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.vers_fleets_repo)
    visibility = "--public" if args.public else "--private"
    cmd = [
        "build-golden",
        visibility,
        "--out-dir",
        args.out_dir,
        *auth_flags(args.email, args.force_shell_auth),
        *source_flags(
            use_local_sources=not args.no_local_sources,
            reef_path=args.reef_path,
            pi_vers_path=args.pi_vers_path,
            reef_ref=args.reef_ref,
            pi_vers_ref=args.pi_vers_ref,
            punkin_ref=args.punkin_ref,
        ),
    ]
    return bun_cli(repo, cmd)


def cmd_provision(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.vers_fleets_repo)
    cmd = [
        "provision",
        "--root-commit",
        args.root_commit,
        "--golden-commit",
        args.golden_commit,
        "--out-dir",
        args.out_dir,
        "--root-name",
        args.root_name,
        *auth_flags(args.email, args.force_shell_auth),
    ]
    return bun_cli(repo, cmd)


def cmd_provision_public(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.vers_fleets_repo)
    cmd = [
        "provision",
        "--root-commit",
        args.root_commit,
        "--golden-commit",
        args.golden_commit,
        "--out-dir",
        args.out_dir,
        "--root-name",
        args.root_name,
        *auth_flags(args.email, args.force_shell_auth),
    ]
    return bun_cli(repo, cmd)


def load_deployment(path: Path) -> dict:
    return json.loads(path.read_text())


def cmd_magic_link(args: argparse.Namespace) -> int:
    root_url = args.root_url
    auth_token = args.auth_token

    if not root_url or not auth_token:
        deployment_path = Path(args.deployment).expanduser().resolve()
        if not deployment_path.exists():
            print(
                f"deployment manifest not found: {deployment_path} (provide --root-url and --auth-token instead)",
                file=sys.stderr,
            )
            return 2
        deployment = load_deployment(deployment_path)
        root_url = root_url or deployment.get("nodes", {}).get("root", {}).get("url", "")
        auth_token = auth_token or deployment.get("auth", {}).get("versAuthToken", "")

    if not root_url:
        print("root URL is missing (use --root-url or deployment.json)", file=sys.stderr)
        return 2
    if not auth_token:
        print("auth token is missing (use --auth-token or deployment.json)", file=sys.stderr)
        return 2

    endpoint = root_url.rstrip("/") + "/auth/magic-link"
    req = urllib.request.Request(
        endpoint,
        method="POST",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(text)
                print(json.dumps(payload, indent=2))
            except json.JSONDecodeError:
                print(text)
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"magic link request failed ({e.code}): {body}", file=sys.stderr)
        return 1


def add_common_repo_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--vers-fleets-repo",
        default=None,
        help="Path to vers-fleets checkout (default: $VERS_FLEETS_REPO, ../vers-fleets, then /tmp/vers-fleets)",
    )


def add_common_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email", default=None)
    parser.add_argument("--force-shell-auth", action="store_true")


def add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reef-path", default=None)
    parser.add_argument("--pi-vers-path", default=None)
    parser.add_argument("--reef-ref", default=None)
    parser.add_argument("--pi-vers-ref", default=None)
    parser.add_argument("--punkin-ref", default=None)
    parser.add_argument("--no-local-sources", action="store_true", help="Do not auto-wire sibling reef/pi-vers repos")


def add_visibility_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--public", action="store_true")
    group.add_argument("--private", action="store_true")


def main() -> int:
    parser = argparse.ArgumentParser(description="self-contained Vers/Reef standup wrapper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="show resolved repos/tooling/public commit defaults")
    add_common_repo_arg(p)

    p = sub.add_parser("test", help="run vers-fleets build/test")
    add_common_repo_arg(p)

    p = sub.add_parser("build-root", help="build root image commit")
    add_common_repo_arg(p)
    add_common_auth_args(p)
    add_visibility_args(p)
    add_source_args(p)
    p.add_argument("--root-name", default="root-reef")
    p.add_argument("--out-dir", default="out")

    p = sub.add_parser("build-golden", help="build golden image commit")
    add_common_repo_arg(p)
    add_common_auth_args(p)
    add_visibility_args(p)
    add_source_args(p)
    p.add_argument("--out-dir", default="out")

    p = sub.add_parser("provision", help="provision root reef from explicit commit IDs")
    add_common_repo_arg(p)
    add_common_auth_args(p)
    p.add_argument("--root-commit", required=True)
    p.add_argument("--golden-commit", required=True)
    p.add_argument("--root-name", default="root-reef")
    p.add_argument("--out-dir", default="out")

    p = sub.add_parser("provision-public", help="provision using known public root/golden commits")
    add_common_repo_arg(p)
    add_common_auth_args(p)
    p.add_argument("--root-commit", default=PUBLIC_ROOT_COMMIT)
    p.add_argument("--golden-commit", default=PUBLIC_GOLDEN_COMMIT)
    p.add_argument("--root-name", default="root-reef")
    p.add_argument("--out-dir", default="out")

    p = sub.add_parser("magic-link", help="create reef UI magic link from deployment.json or explicit args")
    p.add_argument("--deployment", default="out/deployment.json")
    p.add_argument("--root-url", default=None)
    p.add_argument("--auth-token", default=None)

    args = parser.parse_args()

    try:
        dispatch = {
            "doctor": cmd_doctor,
            "test": cmd_test,
            "build-root": cmd_build_root,
            "build-golden": cmd_build_golden,
            "provision": cmd_provision,
            "provision-public": cmd_provision_public,
            "magic-link": cmd_magic_link,
        }
        return dispatch[args.cmd](args)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
