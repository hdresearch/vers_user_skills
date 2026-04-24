#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///

"""Strict-typed Vers REST API wrapper for agent use.

Covers the public API surface used by the Vers Router skill: VM lifecycle,
exec/stream/logs/files, commits, legacy commit tags, repositories/repo-tags,
public repos, domains, env vars, and shell-auth.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from collections.abc import Iterator
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NoReturn, Protocol, cast, final

API_BASE = "https://api.vers.sh/api/v1"
AUTH_BASE = "https://vers.sh/api"

type Json = object


class BinaryResponse(Protocol):
    def read(self) -> bytes: ...
    def __iter__(self) -> Iterator[bytes]: ...
    def __enter__(self) -> BinaryResponse: ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class CliError(Exception):
    pass


@final
class Argv:
    def __init__(self, xs: list[str]) -> None:
        self.xs: list[str] = xs

    def take(self, name: str) -> str:
        if not self.xs:
            raise CliError(f"missing {name}")
        return self.xs.pop(0)

    def rest(self) -> list[str]:
        out = self.xs
        self.xs = []
        return out[1:] if out and out[0] == "--" else out

    def flag(self, name: str) -> bool:
        if name in self.xs:
            self.xs.remove(name)
            return True
        return False

    def opt(self, name: str) -> str | None:
        if name not in self.xs:
            return None
        i = self.xs.index(name)
        if i + 1 >= len(self.xs):
            raise CliError(f"{name} needs a value")
        value = self.xs[i + 1]
        del self.xs[i : i + 2]
        return value

    def opt_int(self, name: str) -> int | None:
        value = self.opt(name)
        return None if value is None else int(value)

    def bool_opt(self, yes: str, no: str, default: bool = False) -> bool:
        out = default
        if yes in self.xs:
            self.xs.remove(yes)
            out = True
        if no in self.xs:
            self.xs.remove(no)
            out = False
        return out

    def many(self, name: str) -> list[str]:
        out: list[str] = []
        while name in self.xs:
            i = self.xs.index(name)
            if i + 1 >= len(self.xs):
                raise CliError(f"{name} needs a value")
            out.append(self.xs[i + 1])
            del self.xs[i : i + 2]
        return out

    def done(self) -> None:
        if self.xs:
            raise CliError(f"unexpected arguments: {' '.join(self.xs)}")


def die(msg: str, code: int = 2) -> NoReturn:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def usage() -> NoReturn:
    cmds = [
        "vms", "vm-status", "vm-metadata", "vm-new", "vm-from-commit", "vm-branch",
        "vm-state", "vm-disk-resize", "vm-delete", "vm-ssh-key", "vm-exec",
        "vm-exec-stream", "vm-exec-attach", "vm-logs", "vm-file-get", "vm-file-put",
        "commits", "commits-public", "vm-commit", "commit-edit", "commit-set-public",
        "commit-delete", "commit-parents", "commit-tags", "commit-tag-get",
        "commit-tag-create", "commit-tag-update", "commit-tag-delete", "repos",
        "repo-create", "repo-get", "repo-delete", "repo-visibility", "repo-fork",
        "repo-tags", "repo-tag-create", "repo-tag-get", "repo-tag-update",
        "repo-tag-delete", "public-repos", "public-repo-get", "public-repo-tags",
        "public-repo-tag-get", "domains", "domain-create", "domain-get",
        "domain-delete", "env-vars", "env-set", "env-delete", "auth-init",
        "auth-verify", "auth-create-key", "auth-verify-pubkey",
    ]
    die("usage: vers_api.py <command> [args...]\ncommands:\n  " + "\n  ".join(cmds), 0)


COMMAND_USAGE: dict[str, str] = {
    "vm-exec": "usage: vers_api.py vm-exec VM_ID [--shell CMD | -- ARGV...] [--env K=V] [--stdin TEXT] [--cwd DIR] [--timeout-secs N] [--exec-id ID]",
    "vm-exec-stream": "usage: vers_api.py vm-exec-stream VM_ID [--shell CMD | -- ARGV...] [--env K=V] [--exec-id ID]",
    "vm-exec-attach": "usage: vers_api.py vm-exec-attach VM_ID EXEC_ID [--cursor N] [--from-latest]",
    "vm-new": "usage: vers_api.py vm-new [--mem MIB] [--vcpu N] [--disk MIB] [--image NAME] [--kernel NAME] [--label K=V] [--hypervisor firecracker|cloud-hypervisor] [--wait-boot|--no-wait-boot]",
    "vm-from-commit": "usage: vers_api.py vm-from-commit [COMMIT_ID | --commit-id ID | --tag-name TAG | --ref REPO:TAG]",
    "vm-branch": "usage: vers_api.py vm-branch (--vm-id ID | --commit-id ID | --tag TAG | --ref REPO:TAG | --any-id ID) [--count N] [--keep-paused] [--skip-wait-boot]",
    "repo-tag-create": "usage: vers_api.py repo-tag-create REPO TAG COMMIT_ID [--description TEXT]",
    "repo-tag-update": "usage: vers_api.py repo-tag-update REPO TAG [--commit-id ID] [--description TEXT]",
    "env-set": "usage: vers_api.py env-set [--replace] KEY=VALUE [KEY=VALUE ...]",
    "vm-file-put": "usage: vers_api.py vm-file-put VM_ID LOCAL_PATH REMOTE_PATH [--mode INT] [--create-dirs|--no-create-dirs]",
    "vm-file-get": "usage: vers_api.py vm-file-get VM_ID REMOTE_PATH [--out LOCAL_PATH | --text]",
    "auth-create-key": "usage: vers_api.py auth-create-key EMAIL --label LABEL (--pubkey PATH | --pubkey-literal TEXT) [--org-name ORG]",
}


def api_key() -> str:
    env_key = os.environ.get("VERS_API_KEY", "").strip()
    if env_key:
        return env_key
    versrc = Path.home() / ".versrc"
    if versrc.exists():
        key = versrc.read_text().strip()
        if key:
            return key
    die("VERS_API_KEY not set and ~/.versrc is empty/missing")


def quote(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def query(params: dict[str, str | int | bool | None]) -> str:
    clean: dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        clean[k] = "true" if v is True else "false" if v is False else str(v)
    return "?" + urllib.parse.urlencode(clean) if clean else ""


def kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise CliError(f"expected KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        if not k:
            raise CliError("empty key")
        out[k] = v
    return out


def json_str_map(items: dict[str, str]) -> dict[str, Json]:
    return {k: v for k, v in items.items()}


def field_str(data: object, key: str) -> str:
    if not isinstance(data, dict):
        die(f"expected object response, got {type(data).__name__}", 1)
    obj = cast(dict[object, object], data)
    value = obj.get(key)
    if not isinstance(value, str):
        die(f"expected string field {key!r}, got {value!r}", 1)
    return value


def req(method: str, path: str, body: dict[str, Json] | None = None, *, base: str = API_BASE, authed: bool = True, timeout: int = 120, extra_headers: dict[str, str] | None = None) -> object:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if extra_headers is not None:
        headers.update(extra_headers)
    if authed:
        headers["Authorization"] = f"Bearer {api_key()}"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with cast(BinaryResponse, urllib.request.urlopen(request, timeout=timeout)) as resp:
            raw: bytes = resp.read()
            if not raw.strip():
                return {}
            text = raw.decode("utf-8", errors="replace")
            return cast(object, json.loads(text))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {text}", file=sys.stderr)
        raise SystemExit(1)


def stream_req(path: str, body: dict[str, Json]) -> int:
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key()}"}
    request = urllib.request.Request(API_BASE + path, data=data, method="POST", headers=headers)
    try:
        with cast(BinaryResponse, urllib.request.urlopen(request, timeout=120)) as resp:
            for line in resp:
                _ = sys.stdout.buffer.write(line)
                _ = sys.stdout.buffer.flush()
        return 0
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {text}", file=sys.stderr)
        return 1


def out(x: object) -> int:
    print(json.dumps(x, indent=2, sort_keys=True))
    return 0


def command_body(a: Argv) -> dict[str, Json]:
    shell = a.opt("--shell")
    env = kv(a.many("--env"))
    stdin = a.opt("--stdin")
    cwd = a.opt("--cwd")
    timeout_secs = a.opt_int("--timeout-secs")
    exec_id = a.opt("--exec-id")
    cmd = ["sh", "-lc", shell] if shell is not None else a.rest()
    if not cmd:
        raise CliError("provide --shell 'cmd' or argv after --")
    body: dict[str, Json] = {"command": list[Json](cmd)}
    if env:
        body["env"] = json_str_map(env)
    if stdin is not None:
        body["stdin"] = stdin
    if cwd is not None:
        body["working_dir"] = cwd
    if timeout_secs is not None:
        body["timeout_secs"] = timeout_secs
    if exec_id is not None:
        body["exec_id"] = exec_id
    return body


def pubkey(a: Argv) -> str:
    path = a.opt("--pubkey")
    lit = a.opt("--pubkey-literal")
    if (path is None) == (lit is None):
        raise CliError("provide exactly one of --pubkey or --pubkey-literal")
    return Path(path).read_text().strip() if path is not None else lit.strip() if lit is not None else ""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        usage()
    cmd = argv[0]
    if len(argv) >= 2 and argv[1] in {"-h", "--help"}:
        print(COMMAND_USAGE.get(cmd, f"usage: vers_api.py {cmd} [args...]"))
        return 0
    a = Argv(argv[1:])

    # VM lifecycle
    if cmd == "vms":
        a.done(); return out(req("GET", "/vms"))
    if cmd == "vm-status":
        vm = a.take("vm_id"); a.done(); return out(req("GET", f"/vm/{quote(vm)}/status"))
    if cmd == "vm-metadata":
        vm = a.take("vm_id"); a.done(); return out(req("GET", f"/vm/{quote(vm)}/metadata"))
    if cmd in {"vm-new", "new-root"}:
        mem = a.opt_int("--mem") or 4096; vcpu = a.opt_int("--vcpu") or 2; disk = a.opt_int("--disk") or 8192
        image = a.opt("--image") or "default"; kernel = a.opt("--kernel") or "default.bin"; hypervisor = a.opt("--hypervisor")
        labels = kv(a.many("--label")); wait = a.bool_opt("--wait-boot", "--no-wait-boot", True); a.done()
        if hypervisor is not None and hypervisor not in {"firecracker", "cloud-hypervisor"}: raise CliError("--hypervisor must be firecracker or cloud-hypervisor")
        cfg: dict[str, Json] = {"mem_size_mib": mem, "vcpu_count": vcpu, "fs_size_mib": disk, "image_name": image, "kernel_name": kernel}
        if labels: cfg["labels"] = json_str_map(labels)
        headers = {"X-Vers-Hypervisor": hypervisor} if hypervisor is not None else None
        return out(req("POST", "/vm/new_root" + query({"wait_boot": wait}), {"vm_config": cfg}, extra_headers=headers))
    if cmd == "vm-from-commit":
        ref = a.opt("--ref"); tag = a.opt("--tag-name"); cid = a.opt("--commit-id")
        target = a.take("commit_id") if cid is None and ref is None and tag is None and a.xs else cid
        a.done()
        from_commit_body: dict[str, Json] = {"ref": ref} if ref is not None else {"tag_name": tag} if tag is not None else {"commit_id": target or ""}
        if from_commit_body.get("commit_id") == "": raise CliError("provide commit id, --tag-name, or --ref repo:tag")
        return out(req("POST", "/vm/from_commit", from_commit_body))
    if cmd == "vm-branch":
        count = a.opt_int("--count") or 1; keep = a.flag("--keep-paused"); skip = a.flag("--skip-wait-boot")
        vm = a.opt("--vm-id"); cid = a.opt("--commit-id"); tag = a.opt("--tag"); ref = a.opt("--ref"); any_id = a.opt("--any-id"); a.done()
        provided = [x is not None for x in (vm, cid, tag, ref, any_id)].count(True)
        if provided != 1: raise CliError("provide exactly one of --vm-id/--commit-id/--tag/--ref/--any-id")
        if ref is not None:
            if ":" not in ref: raise CliError("--ref must be repo:tag")
            repo, rtag = ref.split(":", 1); path = f"/vm/branch/by_ref/{quote(repo)}/{quote(rtag)}" + query({"count": count})
        elif cid is not None: path = f"/vm/branch/by_commit/{quote(cid)}" + query({"count": count})
        elif tag is not None: path = f"/vm/branch/by_tag/{quote(tag)}" + query({"count": count})
        elif vm is not None: path = f"/vm/branch/by_vm/{quote(vm)}" + query({"count": count, "keep_paused": keep, "skip_wait_boot": skip})
        else: path = f"/vm/{quote(any_id or '')}/branch" + query({"count": count, "keep_paused": keep, "skip_wait_boot": skip})
        return out(req("POST", path))
    if cmd == "vm-state":
        vm = a.take("vm_id"); state = a.take("state"); skip = a.flag("--skip-wait-boot"); a.done(); return out(req("PATCH", f"/vm/{quote(vm)}/state" + query({"skip_wait_boot": skip}), {"state": state}))
    if cmd == "vm-disk-resize":
        vm = a.take("vm_id"); size = int(a.take("size")); skip = a.flag("--skip-wait-boot"); a.done(); return out(req("PATCH", f"/vm/{quote(vm)}/disk" + query({"skip_wait_boot": skip}), {"fs_size_mib": size}))
    if cmd == "vm-delete":
        vm = a.take("vm_id"); skip = a.flag("--skip-wait-boot"); a.done(); return out(req("DELETE", f"/vm/{quote(vm)}" + query({"skip_wait_boot": skip})))
    if cmd == "vm-ssh-key":
        vm = a.take("vm_id"); dest = a.opt("--out"); print_key = a.flag("--print-key"); a.done(); data = req("GET", f"/vm/{quote(vm)}/ssh_key")
        key = field_str(data, "ssh_private_key")
        if print_key: print(key, end="" if key.endswith("\n") else "\n"); return 0
        path = Path(dest or f"/tmp/vers-{vm[:12]}.pem"); _ = path.write_text(key); path.chmod(0o600); print(path); return 0

    # exec/files/logs
    if cmd == "vm-exec":
        vm = a.take("vm_id"); http_timeout = a.opt_int("--http-timeout") or 120; body = command_body(a); a.done(); return out(req("POST", f"/vm/{quote(vm)}/exec", body, timeout=http_timeout))
    if cmd == "vm-exec-stream":
        vm = a.take("vm_id"); body = command_body(a); a.done(); return stream_req(f"/vm/{quote(vm)}/exec/stream", body)
    if cmd == "vm-exec-attach":
        vm = a.take("vm_id"); eid = a.take("exec_id"); cursor = a.opt_int("--cursor"); latest = a.flag("--from-latest"); a.done(); attach_body: dict[str, Json] = {"exec_id": eid}
        if cursor is not None: attach_body["cursor"] = cursor
        if latest: attach_body["from_latest"] = True
        return stream_req(f"/vm/{quote(vm)}/exec/stream/attach", attach_body)
    if cmd == "vm-logs":
        vm = a.take("vm_id"); offset = a.opt_int("--offset"); max_entries = a.opt_int("--max-entries"); stream = a.opt("--stream"); a.done(); return out(req("GET", f"/vm/{quote(vm)}/logs" + query({"offset": offset, "max_entries": max_entries, "stream": stream})))
    if cmd == "vm-file-get":
        vm = a.take("vm_id"); path = a.take("path"); dest = a.opt("--out"); text = a.flag("--text"); a.done(); data = req("GET", f"/vm/{quote(vm)}/files" + query({"path": path}))
        raw = base64.b64decode(field_str(data, "content_b64"))
        if dest: _ = Path(dest).write_bytes(raw)
        elif text: print(raw.decode("utf-8", errors="replace"), end="")
        else: _ = sys.stdout.buffer.write(raw)
        return 0
    if cmd == "vm-file-put":
        vm = a.take("vm_id"); local = a.take("local"); remote = a.take("remote"); mode = a.opt_int("--mode") or 0o644; create_dirs = a.bool_opt("--create-dirs", "--no-create-dirs", True); a.done()
        raw = Path(local).read_bytes(); file_body: dict[str, Json] = {"path": remote, "content_b64": base64.b64encode(raw).decode("ascii"), "mode": mode, "create_dirs": create_dirs}
        return out(req("PUT", f"/vm/{quote(vm)}/files", file_body))

    # commits
    if cmd == "commits":
        limit = a.opt_int("--limit"); offset = a.opt_int("--offset"); a.done(); return out(req("GET", "/commits" + query({"limit": limit, "offset": offset})))
    if cmd == "commits-public":
        limit = a.opt_int("--limit"); offset = a.opt_int("--offset"); a.done(); return out(req("GET", "/commits/public" + query({"limit": limit, "offset": offset})))
    if cmd == "vm-commit":
        vm = a.take("vm_id"); commit_body: dict[str, Json] = {}; cid = a.opt("--commit-id"); name = a.opt("--name"); desc = a.opt("--description"); keep = a.flag("--keep-paused"); skip = a.flag("--skip-wait-boot"); a.done()
        if cid: commit_body["commit_id"] = cid
        if name: commit_body["name"] = name
        if desc: commit_body["description"] = desc
        return out(req("POST", f"/vm/{quote(vm)}/commit" + query({"keep_paused": keep, "skip_wait_boot": skip}), commit_body))
    if cmd in {"commit-edit", "commit-set-public"}:
        cid = a.take("commit_id"); edit_body: dict[str, Json] = {}; public = a.bool_opt("--public", "--no-public", cmd == "commit-set-public"); name = a.opt("--name"); desc = a.opt("--description"); a.done(); edit_body["is_public"] = public
        if name is not None: edit_body["name"] = name
        if desc is not None: edit_body["description"] = desc
        return out(req("PATCH", f"/commits/{quote(cid)}", edit_body))
    if cmd == "commit-delete":
        cid = a.take("commit_id"); a.done(); return out(req("DELETE", f"/commits/{quote(cid)}"))
    if cmd == "commit-parents":
        cid = a.take("commit_id"); a.done(); return out(req("GET", f"/vm/commits/{quote(cid)}/parents"))

    # legacy commit tags
    if cmd == "commit-tags": a.done(); return out(req("GET", "/commit_tags"))
    if cmd == "commit-tag-get": tag = a.take("tag"); a.done(); return out(req("GET", f"/commit_tags/{quote(tag)}"))
    if cmd == "commit-tag-create":
        tag = a.take("tag"); cid = a.take("commit_id"); desc = a.opt("--description"); a.done(); tag_body: dict[str, Json] = {"tag_name": tag, "commit_id": cid}
        if desc is not None: tag_body["description"] = desc
        return out(req("POST", "/commit_tags", tag_body))
    if cmd == "commit-tag-update":
        tag = a.take("tag"); cid = a.opt("--commit-id"); desc = a.opt("--description"); a.done(); tag_update_body: dict[str, Json] = {}
        if cid is not None: tag_update_body["commit_id"] = cid
        if desc is not None: tag_update_body["description"] = desc
        return out(req("PATCH", f"/commit_tags/{quote(tag)}", tag_update_body))
    if cmd == "commit-tag-delete": tag = a.take("tag"); a.done(); return out(req("DELETE", f"/commit_tags/{quote(tag)}"))

    # repositories
    if cmd == "repos": a.done(); return out(req("GET", "/repositories"))
    if cmd == "repo-create":
        name = a.take("name"); desc = a.opt("--description"); a.done(); repo_body: dict[str, Json] = {"name": name}
        if desc is not None: repo_body["description"] = desc
        return out(req("POST", "/repositories", repo_body))
    if cmd == "repo-get": repo = a.take("repo"); a.done(); return out(req("GET", f"/repositories/{quote(repo)}"))
    if cmd == "repo-delete": repo = a.take("repo"); a.done(); return out(req("DELETE", f"/repositories/{quote(repo)}"))
    if cmd == "repo-visibility": repo = a.take("repo"); public = a.bool_opt("--public", "--no-public", True); a.done(); return out(req("PATCH", f"/repositories/{quote(repo)}/visibility", {"is_public": public}))
    if cmd == "repo-fork":
        org = a.take("source_org"); repo = a.take("source_repo"); tag = a.take("source_tag"); repo_name = a.opt("--repo-name"); tag_name = a.opt("--tag-name"); a.done(); fork_body: dict[str, Json] = {"source_org": org, "source_repo": repo, "source_tag": tag}
        if repo_name is not None: fork_body["repo_name"] = repo_name
        if tag_name is not None: fork_body["tag_name"] = tag_name
        return out(req("POST", "/repositories/fork", fork_body))
    if cmd == "repo-tags": repo = a.take("repo"); a.done(); return out(req("GET", f"/repositories/{quote(repo)}/tags"))
    if cmd == "repo-tag-create":
        repo = a.take("repo"); tag = a.take("tag"); cid = a.take("commit_id"); desc = a.opt("--description"); a.done(); repo_tag_body: dict[str, Json] = {"tag_name": tag, "commit_id": cid}
        if desc is not None: repo_tag_body["description"] = desc
        return out(req("POST", f"/repositories/{quote(repo)}/tags", repo_tag_body))
    if cmd == "repo-tag-get": repo = a.take("repo"); tag = a.take("tag"); a.done(); return out(req("GET", f"/repositories/{quote(repo)}/tags/{quote(tag)}"))
    if cmd == "repo-tag-update":
        repo = a.take("repo"); tag = a.take("tag"); cid = a.opt("--commit-id"); desc = a.opt("--description"); a.done(); repo_tag_update_body: dict[str, Json] = {}
        if cid is not None: repo_tag_update_body["commit_id"] = cid
        if desc is not None: repo_tag_update_body["description"] = desc
        return out(req("PATCH", f"/repositories/{quote(repo)}/tags/{quote(tag)}", repo_tag_update_body))
    if cmd == "repo-tag-delete": repo = a.take("repo"); tag = a.take("tag"); a.done(); return out(req("DELETE", f"/repositories/{quote(repo)}/tags/{quote(tag)}"))
    if cmd == "public-repos": a.done(); return out(req("GET", "/public/repositories"))
    if cmd == "public-repo-get": org = a.take("org"); repo = a.take("repo"); a.done(); return out(req("GET", f"/public/repositories/{quote(org)}/{quote(repo)}"))
    if cmd == "public-repo-tags": org = a.take("org"); repo = a.take("repo"); a.done(); return out(req("GET", f"/public/repositories/{quote(org)}/{quote(repo)}/tags"))
    if cmd == "public-repo-tag-get": org = a.take("org"); repo = a.take("repo"); tag = a.take("tag"); a.done(); return out(req("GET", f"/public/repositories/{quote(org)}/{quote(repo)}/tags/{quote(tag)}"))

    # domains/env
    if cmd == "domains": vm = a.opt("--vm-id"); a.done(); return out(req("GET", "/domains" + query({"vm_id": vm})))
    if cmd == "domain-create": domain = a.take("domain"); vm = a.take("vm_id"); a.done(); return out(req("POST", "/domains", {"domain": domain, "vm_id": vm}))
    if cmd == "domain-get": did = a.take("domain_id"); a.done(); return out(req("GET", f"/domains/{quote(did)}"))
    if cmd == "domain-delete": did = a.take("domain_id"); a.done(); return out(req("DELETE", f"/domains/{quote(did)}"))
    if cmd == "env-vars": a.done(); return out(req("GET", "/env_vars"))
    if cmd == "env-set": replace = a.flag("--replace"); vars_ = kv(a.rest()); a.done(); return out(req("PUT", "/env_vars", {"vars": json_str_map(vars_), "replace": replace}))
    if cmd == "env-delete": key = a.take("key"); a.done(); return out(req("DELETE", f"/env_vars/{quote(key)}"))

    # shell auth
    if cmd == "auth-init": email = a.take("email"); key = pubkey(a); a.done(); return out(req("POST", "/shell-auth", {"email": email, "ssh_public_key": key}, authed=False, base=AUTH_BASE))
    if cmd == "auth-verify": email = a.take("email"); key = pubkey(a); a.done(); return out(req("POST", "/shell-auth/verify-key", {"email": email, "ssh_public_key": key}, authed=False, base=AUTH_BASE))
    if cmd == "auth-create-key":
        email = a.take("email"); label = a.opt("--label"); org = a.opt("--org-name"); key = pubkey(a); a.done()
        if label is None: raise CliError("--label required")
        auth_body: dict[str, Json] = {"email": email, "ssh_public_key": key, "label": label}
        if org is not None: auth_body["org_name"] = org
        return out(req("POST", "/shell-auth/api-keys", auth_body, authed=False, base=AUTH_BASE))
    if cmd == "auth-verify-pubkey": key = pubkey(a); a.done(); return out(req("POST", "/shell-auth/verify-public-key", {"ssh_public_key": key}, authed=False, base=AUTH_BASE))

    raise CliError(f"unknown command {cmd!r}; run --help")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except CliError as e:
        die(str(e))
