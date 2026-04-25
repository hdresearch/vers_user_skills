# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx",
# ]
# ///
"""
vers.py — opinionated client for the vers.sh API (Application Programming Interface).

Dependencies & running
----------------------
This file carries a PEP 723 inline-script-metadata header. With `uv` installed,
the only thing you need to do is run it:

    uv run vers.py            # for ad-hoc one-off use as a script
    uv run smoke_test.py      # to verify the helper is working

To use vers.py as a library from your own script, give your script a matching
PEP 723 header listing `httpx` as a dependency, then `uv run` it. No virtualenv
setup, no pip install, no requirements.txt to maintain.

Design rationale (read this before changing things):

* Typed identifiers (VmId, CommitId, RepoName, RepoRef) prevent the three known
  flat-union footguns in the upstream API surface:
    - FromCommitVmRequest: a 3-way oneOf body that returns misleading
      "JSON parse failed" errors for empty / two-key inputs.
    - POST /vm/{vm_or_commit_id}/branch: polymorphic path that always reports
      "commit not found" on miss, regardless of caller intent.
    - bare tag_name (legacy org-scoped namespace, shadowed by repo_tags).
  The helper refuses to expose any of these footguns. branch_from(source) and
  from_commit(commit_id=..., ref=...) dispatch by python type, never by string
  shape.

* Three-envelope error normalization. The upstream API returns at least three
  different error body shapes (json ErrorResponse, json hybrid {vms:[],error:...},
  rust serde plain text, plain "403 Forbidden" gateway response). Every error
  surfaces as a single VersError subclass with status, message, and raw body
  preserved.

* Streaming endpoints actually stream. exec_stream() yields chunks via
  httpx.stream(); does not buffer. The auto-generated SDK calls .json() on
  these endpoints which defeats the purpose.

* 409 is never retried by the helper. The upstream 409s observed are all
  caller-side concerns (duplicate repo name, commit-with-active-vms) that
  retrying cannot fix.

* The two-phase pattern is the canonical workflow:
    phase 1: prepare_image(base, prep_steps, tag_as=...) -> RepoRef
    phase 2: branch_from(repo_ref) -> VmId, exec, preserve artifacts, pause
  See prepare_image() and branched_vm() context manager.

Author: Carter Schonwald
"""

from __future__ import annotations

import base64
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal, NewType, TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    import argparse

import httpx


# =====================================================================
# typed identifiers
# =====================================================================

# Real wrapper classes (not NewType, which collapses to str at runtime).
# Why: branch_from() needs to dispatch by python type (VmId vs CommitId vs
# RepoRef) at runtime to pick the correct typed upstream endpoint. NewType is
# erased and would force string-shape sniffing, which is exactly the footgun
# we're avoiding upstream.

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


_UuidIdT = TypeVar("_UuidIdT", bound="_UuidId")


class _UuidId(str):
    """Base for uuid-shaped id wrappers. Subclasses 'str' so existing string
    formatters work, but is a distinct python type for dispatch."""

    __slots__ = ()

    def __new__(cls: type[_UuidIdT], s: str) -> _UuidIdT:
        s = s.strip()
        if not _UUID_RE.match(s):
            raise ValueError(f"{cls.__name__} must be a UUID, got {s!r}")
        # cast: str.__new__ stub doesn't model TypeVar-bound subclass return,
        # but cls is a subclass of str so the call is sound at runtime.
        return cast(_UuidIdT, str.__new__(cls, s))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str.__repr__(self)})"


class VmId(_UuidId):
    """Identifies a live VM."""

    __slots__ = ()


class CommitId(_UuidId):
    """Identifies a frozen commit (filesystem + memory snapshot)."""

    __slots__ = ()


class DomainId(_UuidId):
    """Identifies a custom domain attached to a vm."""

    __slots__ = ()


class RepoName(str):
    """Repository name. Allowed chars: alphanumeric, dot, underscore, hyphen, slash."""

    __slots__ = ()

    def __new__(cls, s: str) -> "RepoName":
        s = s.strip()
        if not _REPO_NAME_RE.match(s):
            raise ValueError(f"RepoName allows [A-Za-z0-9._/-]+, got {s!r}")
        return super().__new__(cls, s)


class EnvVarKey(str):
    """POSIX shell identifier, <=256 chars."""

    __slots__ = ()

    def __new__(cls, s: str) -> "EnvVarKey":
        s = s.strip()
        if not _ENV_KEY_RE.match(s):
            raise ValueError(
                f"EnvVarKey must match ^[A-Za-z_][A-Za-z0-9_]*$, got {s!r}"
            )
        if len(s) > 256:
            raise ValueError(f"EnvVarKey exceeds 256 chars (got {len(s)})")
        return super().__new__(cls, s)


# Convenience constructors (lowercase aliases for caller ergonomics)
vm_id = VmId
commit_id = CommitId
domain_id = DomainId
repo_name = RepoName
env_key = EnvVarKey


@dataclass(frozen=True)
class RepoRef:
    """A repository reference of the form 'repo_name:tag_name'.

    This is the only repo-tag form the helper accepts as input for
    from_commit / branch_from. The legacy org-scoped bare tag_name is not
    exposed because (a) it lives in a separate namespace and (b) the upstream
    docs mark it legacy.
    """

    repo: RepoName
    tag: str

    def __post_init__(self) -> None:
        if not isinstance(self.repo, str) or not _REPO_NAME_RE.match(self.repo):
            raise ValueError(f"RepoRef.repo invalid: {self.repo!r}")
        if not self.tag or ":" in self.tag or "/" in self.tag:
            raise ValueError(
                f"RepoRef.tag must be non-empty and not contain ':' or '/': {self.tag!r}"
            )

    @classmethod
    def parse(cls, s: str) -> "RepoRef":
        """Parse 'repo_name:tag_name'. Only one ':' permitted."""
        if s.count(":") != 1:
            raise ValueError(
                f"RepoRef must be 'repo_name:tag_name', got {s!r}"
            )
        repo_str, tag = s.split(":", 1)
        return cls(repo=repo_name(repo_str), tag=tag)

    def __str__(self) -> str:
        return f"{self.repo}:{self.tag}"


# =====================================================================
# error taxonomy
# =====================================================================


class VersError(Exception):
    """Base class for all errors from this client."""

    status: int
    message: str
    raw_body: str
    method: str
    url: str

    def __init__(
        self,
        status: int,
        message: str,
        raw_body: str,
        method: str,
        url: str,
    ) -> None:
        self.status = status
        self.message = message
        self.raw_body = raw_body
        self.method = method
        self.url = url
        super().__init__(f"[{method} {url}] {status}: {message}")


class VersAuthError(VersError):
    """403 with no JSON body (gateway-level auth failure or missing api key)."""


class VersForbidden(VersError):
    """403 with JSON body (e.g., DELETE /vm/{nonexistent_uuid})."""


class VersNotFound(VersError):
    """404."""


class VersConflict(VersError):
    """409. Caller-side concern (duplicate name, commit-still-has-active-vms).
    Never retried by this client."""


class VersBadRequest(VersError):
    """400 with json body. Application-level validation failure."""


class VersValidationError(VersError):
    """400 / 422 with rust-serde plain-text body. Schema-level validation failure."""


class VersHybridBranchError(VersError):
    """The 'branch/by_vm' / 'branch/by_ref' hybrid envelope:
    {"vms":[], "error":"..."}. Detected explicitly because it would otherwise
    silently look like a successful zero-vm branch."""


class VersServerError(VersError):
    """5xx."""


class VersConfigError(Exception):
    """Configuration problem (no API key, bad base URL). Raised at construction
    or first request, not on each call."""


class VersCliUsageError(ValueError):
    """Command-line argument shape error. Used so argparse failures can still
    return the helper's JSON error envelope on stderr instead of bare argparse
    prose. Caller-side, never raised by API operations. Exit code 64 (EX_USAGE)."""


# =====================================================================
# response shapes (lightweight dataclasses, only the fields we use)
# =====================================================================


@dataclass(frozen=True)
class Vm:
    vm_id: VmId
    owner_id: str
    state: str
    created_at: str
    labels: dict[str, Any]  # undocumented in spec, present in reality


@dataclass(frozen=True)
class VmMetadata:
    vm_id: VmId
    owner_id: str
    state: str
    created_at: str
    deleted_at: str | None
    ip: str | None
    parent_commit_id: CommitId | None
    grandparent_vm_id: VmId | None


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    exec_id: str | None  # often absent (not just null) on sync exec

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class LogEntry:
    exec_id: str | None
    timestamp: str
    stream: Literal["stdout", "stderr"]
    data: bytes  # decoded from data_b64; user-side decoding to str is their call


@dataclass(frozen=True)
class LogPage:
    entries: list[LogEntry]
    next_offset: int | None
    eof: bool


@dataclass(frozen=True)
class Commit:
    commit_id: CommitId
    name: str
    description: str | None
    is_public: bool
    created_at: str
    parent_vm_id: VmId | None
    grandparent_commit_id: CommitId | None
    owner_id: str


@dataclass(frozen=True)
class Repo:
    repo_id: str
    name: RepoName
    description: str | None
    is_public: bool
    created_at: str


@dataclass(frozen=True)
class Tag:
    tag_id: str
    tag_name: str
    repo_name: RepoName
    commit_id: CommitId
    reference: RepoRef


@dataclass(frozen=True)
class Domain:
    domain_id: DomainId
    domain: str
    vm_id: VmId
    created_at: str = ""  # ISO-8601 timestamp from server


@dataclass(frozen=True)
class ForkResult:
    """Result of forking a public repo. Note that fork has a side effect:
    it creates a new VM too. Preserve or pause .vm unless the user separately
    authorizes VM termination."""
    ref: RepoRef
    vm: VmId
    commit: CommitId


# =====================================================================
# client
# =====================================================================


_DEFAULT_BASE_URL = "https://api.vers.sh"
_API_PREFIX = "/api/v1"


def _retry_status(s: int) -> bool:
    """Status codes worth retrying. Notable absence: 409 (caller-side conflicts
    that retries cannot fix) and 401/403 (auth issues that retries cannot fix)."""
    return s in (408, 429) or 500 <= s < 600


class Client:
    """Synchronous client for the vers.sh API.

    For most workflows use the high-level helpers (prepare_image,
    branched_vm context manager). Drop down to the per-endpoint methods when
    you need fine-grained control.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 3,
        owner_id_filter: str | None = None,
    ) -> None:
        api_key = api_key or os.environ.get("VERS_API_KEY")
        if not api_key:
            raise VersConfigError(
                "no api key configured: pass api_key=... or set VERS_API_KEY"
            )
        self._api_key = api_key
        # The api key format is <owner_uuid><64-hex-secret>. The first 36 chars
        # (with dashes) are the owner_id, which appears in API responses. We
        # remember it so we can default-filter list_vms() to "yours only".
        self._owner_id = owner_id_filter or api_key[:36]
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "authorization": f"Bearer {api_key}",
                "user-agent": "vers-py-helper/0.1",
                "accept": "application/json",
            },
        )

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._http.close()

    def close(self) -> None:
        """Release the underlying httpx connection pool. Equivalent to exiting
        the context manager. Safe to call multiple times."""
        self._http.close()

    @property
    def owner_id(self) -> str:
        """The api key's owner uuid (first 36 chars of the key, also visible
        in API responses)."""
        return self._owner_id

    # -------- core dispatch --------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        """Single request with retry, error normalization, and envelope
        detection. Returns parsed JSON on success, raises VersError on failure."""

        url = f"{_API_PREFIX}{path}"
        last_err: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.request(
                    method, url, json=json, params=params
                )
            except httpx.HTTPError as e:
                last_err = e
                if attempt < self._max_retries:
                    time.sleep(min(2**attempt * 0.25, 5.0))
                    continue
                raise VersError(
                    status=0,
                    message=f"transport error: {e!r}",
                    raw_body="",
                    method=method,
                    url=url,
                ) from e

            if resp.status_code < 400:
                return self._parse_success(resp, expect_json=expect_json)

            # 4xx / 5xx: maybe retry, otherwise raise the right subclass
            if _retry_status(resp.status_code) and attempt < self._max_retries:
                # honor Retry-After if present, capped at 30s
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = min(float(retry_after), 30.0)
                    except ValueError:
                        delay = min(2**attempt * 0.25, 5.0)
                else:
                    delay = min(2**attempt * 0.25, 5.0)
                time.sleep(delay)
                continue

            self._raise_for_response(resp, method, url)

        # unreachable, but mypy
        assert last_err is not None
        raise VersError(0, f"giving up: {last_err!r}", "", method, url) from last_err

    def _parse_success(self, resp: httpx.Response, *, expect_json: bool) -> Any:
        if not expect_json or not resp.content:
            return None
        ct = resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            return resp.json()
        # The vers API often omits Content-Type on success. If body looks like
        # JSON, parse it; else return raw text.
        text = resp.text
        if text and text[0] in "{[":
            return resp.json()
        return text

    def _raise_for_response(
        self, resp: httpx.Response, method: str, url: str
    ) -> None:
        """Normalize the upstream's three-or-more error envelopes into one
        of our typed exceptions, preserving status / message / raw body."""

        status = resp.status_code
        body = resp.text
        ct = resp.headers.get("content-type", "")

        # Plain-text errors (auth gateway, rust-serde validation)
        if not ct.startswith("application/json"):
            if status == 403:
                raise VersAuthError(
                    status, "auth failed (bad/missing api key)", body, method, url
                )
            if status in (400, 422):
                # rust-serde validation message; first line is the most useful
                msg = body.strip().split("\n", 1)[0][:300]
                raise VersValidationError(status, msg, body, method, url)
            if status == 404:
                # path-extractor uuid parse failure
                raise VersNotFound(status, body.strip()[:300], body, method, url)
            if 500 <= status < 600:
                raise VersServerError(
                    status, body.strip()[:300] or f"HTTP {status}", body, method, url
                )
            raise VersError(status, body.strip()[:300], body, method, url)

        # JSON error path
        try:
            data = resp.json()
        except Exception:
            data = {}

        # Hybrid envelope detection: {"vms":[], "error":"..."} on
        # branch/by_vm and branch/by_ref. Without explicit detection,
        # callers see Ok([]) and never know.
        if (
            isinstance(data, dict)
            and "vms" in data
            and "error" in data
            and not data.get("vms")
        ):
            raise VersHybridBranchError(
                status,
                str(data.get("error") or "branch failed (hybrid envelope)"),
                body,
                method,
                url,
            )

        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("message") or "")
        if not msg:
            msg = body[:300]

        if status == 400:
            raise VersBadRequest(status, msg, body, method, url)
        if status == 401:
            raise VersAuthError(status, msg, body, method, url)
        if status == 403:
            raise VersForbidden(status, msg, body, method, url)
        if status == 404:
            raise VersNotFound(status, msg, body, method, url)
        if status == 409:
            raise VersConflict(status, msg, body, method, url)
        if status == 422:
            raise VersValidationError(status, msg, body, method, url)
        if 500 <= status < 600:
            raise VersServerError(status, msg, body, method, url)
        raise VersError(status, msg, body, method, url)

    # -------- vm lifecycle --------

    def list_vms(self, *, owned_by_me: bool = True) -> list[Vm]:
        """List vms. By default filters to only vms owned by the api key's
        owner_id (the upstream API returns vms across multiple owner_ids
        within an organization view)."""
        data = self._request("GET", "/vms")
        vms = [
            Vm(
                vm_id=VmId(d["vm_id"]),
                owner_id=d["owner_id"],
                state=d["state"],
                created_at=d["created_at"],
                labels=d.get("labels", {}),
            )
            for d in data
        ]
        if owned_by_me:
            vms = [v for v in vms if v.owner_id == self._owner_id]
        return vms

    def list_vms_with_metadata(self, *, owned_by_me: bool = True) -> list[VmMetadata]:
        """Like list_vms but follows up with /metadata for each, so IPs are
        populated. N+1 calls; consider this when fleet sizes get large."""
        return [self.get_vm(v.vm_id) for v in self.list_vms(owned_by_me=owned_by_me)]

    def new_root(
        self,
        *,
        mem_mib: int,
        vcpu: int,
        fs_mib: int,
        wait_boot: bool = True,
    ) -> VmId:
        """Cold-boot a fresh VM with explicit resource dimensions.

        No helper defaults are provided because LLM callers tend to cargo-cult
        examples as policy. Pick dimensions from the actual task, say them to
        the user before allocation, and pass them explicitly. wait_boot=True
        (default) blocks until userspace is ready; wait_boot=False returns
        immediately with the VM in 'booting' state.
        """
        for name, value in (("mem_mib", mem_mib), ("vcpu", vcpu), ("fs_mib", fs_mib)):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        body = {
            "vm_config": {
                "mem_size_mib": mem_mib,
                "vcpu_count": vcpu,
                "fs_size_mib": fs_mib,
            }
        }
        params = {"wait_boot": "true" if wait_boot else "false"}
        data = self._request("POST", "/vm/new_root", json=body, params=params)
        return VmId(data["vm_id"])

    def get_vm(self, vm: VmId) -> VmMetadata:
        """Get vm metadata including IP. Use this rather than the (lighter-weight)
        list_vms when you need the IP, since /vms doesn't include it."""
        d = self._request("GET", f"/vm/{vm}/metadata")
        return VmMetadata(
            vm_id=VmId(d["vm_id"]),
            owner_id=d["owner_id"],
            state=d["state"],
            created_at=d["created_at"],
            deleted_at=d.get("deleted_at"),
            ip=d.get("ip"),
            parent_commit_id=CommitId(d["parent_commit_id"])
            if d.get("parent_commit_id")
            else None,
            grandparent_vm_id=VmId(d["grandparent_vm_id"])
            if d.get("grandparent_vm_id")
            else None,
        )

    def delete_vm(self, vm: VmId, *, skip_wait_boot: bool = False) -> None:
        """Explicit user-authorized VM termination.

        This method is intentionally direct API surface, not part of the
        LLM-facing CLI schema or any automatic cleanup path. Note: 403 (not
        404) is returned for non-existent UUIDs; VersForbidden is raised in
        that case.
        """
        params = {"skip_wait_boot": "true"} if skip_wait_boot else None
        self._request("DELETE", f"/vm/{vm}", params=params)

    def pause(self, vm: VmId) -> None:
        """Pause a vm. State + memory + processes preserved; resources released."""
        self._request("PATCH", f"/vm/{vm}/state", json={"state": "Paused"})

    def resume(self, vm: VmId) -> None:
        """Resume a paused vm."""
        self._request("PATCH", f"/vm/{vm}/state", json={"state": "Running"})

    def get_ssh_key(self, vm: VmId) -> tuple[str, int]:
        """Returns (private_key_pem, ssh_port). Treat as secret."""
        d = self._request("GET", f"/vm/{vm}/ssh_key")
        return d["ssh_private_key"], d["ssh_port"]

    # -------- exec --------

    def exec(
        self,
        vm: VmId,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        working_dir: str | None = None,
    ) -> ExecResult:
        """Run a command, wait for completion, return result. Synchronous."""
        body: dict[str, Any] = {"command": cmd}
        if env:
            body["env"] = env
        if working_dir:
            body["working_dir"] = working_dir
        d = self._request("POST", f"/vm/{vm}/exec", json=body)
        return ExecResult(
            exit_code=d["exit_code"],
            stdout=d["stdout"],
            stderr=d["stderr"],
            exec_id=d.get("exec_id"),
        )

    def exec_stream(
        self,
        vm: VmId,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        working_dir: str | None = None,
    ) -> Iterator[LogEntry]:
        """Streaming exec. Yields LogEntry chunks as they arrive. Generator —
        consume with a for loop. Closes the http stream on exhaustion or
        early break.

        Note: the upstream server's /exec/stream response shape is not
        documented as NDJSON or SSE; this method yields entries by parsing
        json lines. If the server changes shape, this needs to follow.
        """
        body: dict[str, Any] = {"command": cmd}
        if env:
            body["env"] = env
        if working_dir:
            body["working_dir"] = working_dir
        url = f"{_API_PREFIX}/vm/{vm}/exec/stream"
        with self._http.stream("POST", url, json=body) as resp:
            if resp.status_code >= 400:
                resp.read()
                self._raise_for_response(resp, "POST", url)
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                import json as _json

                try:
                    d = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                # Drop empty / EOF marker chunks (server emits a final empty
                # data_b64 chunk to signal exec completion). Caller cares
                # about content only.
                if not d.get("data_b64"):
                    continue
                yield LogEntry(
                    exec_id=d.get("exec_id"),
                    timestamp=d.get("timestamp", ""),
                    stream=d.get("stream", "stdout"),
                    data=base64.b64decode(d["data_b64"]),
                )

    def get_logs(
        self,
        vm: VmId,
        *,
        offset: int | None = None,
        max_entries: int | None = None,
    ) -> LogPage:
        """Read buffered exec logs. Use offset for pagination across calls."""
        params: dict[str, Any] = {}
        if offset is not None:
            params["offset"] = offset
        if max_entries is not None:
            params["max_entries"] = max_entries
        d = self._request("GET", f"/vm/{vm}/logs", params=params or None)
        entries = [
            LogEntry(
                exec_id=e.get("exec_id"),
                timestamp=e.get("timestamp", ""),
                stream=e.get("stream", "stdout"),
                data=base64.b64decode(e.get("data_b64", "")),
            )
            for e in d.get("entries", [])
        ]
        return LogPage(
            entries=entries,
            next_offset=d.get("next_offset"),
            eof=bool(d.get("eof", False)),
        )

    # -------- commits and branching (typed dispatch) --------

    def commit(
        self,
        vm: VmId,
        *,
        name: str | None = None,
        description: str | None = None,
        skip_wait_boot: bool = False,
    ) -> CommitId:
        """Snapshot a vm (filesystem + memory). Commit at a useful equilibrium —
        services started, caches warmed — not mid-startup."""
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if description:
            body["description"] = description
        params = {"skip_wait_boot": "true"} if skip_wait_boot else None
        d = self._request(
            "POST", f"/vm/{vm}/commit", json=body or None, params=params
        )
        return CommitId(d["commit_id"])

    def branch_from(
        self,
        source: VmId | CommitId | RepoRef,
        *,
        count: int = 1,
        skip_wait_boot: bool = False,
    ) -> list[VmId]:
        """Branch by python type, never by string shape. Always uses a typed
        upstream endpoint (by_vm / by_commit / by_ref). Never uses the
        polymorphic /vm/{vm_or_commit_id}/branch route, whose error messages
        misleadingly always say 'commit not found'."""
        params: dict[str, Any] = {}
        if count != 1:
            params["count"] = count
        if skip_wait_boot:
            params["skip_wait_boot"] = "true"

        if isinstance(source, RepoRef):
            path = f"/vm/branch/by_ref/{source.repo}/{source.tag}"
        elif isinstance(source, VmId):
            path = f"/vm/branch/by_vm/{source}"
        elif isinstance(source, CommitId):
            path = f"/vm/branch/by_commit/{source}"
        else:
            raise TypeError(
                f"branch_from source must be VmId, CommitId, or RepoRef; "
                f"got {type(source).__name__} (did you forget to wrap a "
                f"string in vm_id(...) or commit_id(...)?)"
            )

        d = self._request("POST", path, params=params or None)
        return [VmId(v["vm_id"]) for v in d.get("vms", [])]

    def from_commit(
        self,
        *,
        commit: CommitId | None = None,
        ref: RepoRef | None = None,
    ) -> VmId:
        """Restore a single vm from a commit. Exactly one of commit / ref
        required. Refuses to send empty or two-key bodies (the upstream's
        misleading 'JSON parse failed' is your reward for sending those)."""
        provided = [x for x in (commit, ref) if x is not None]
        if len(provided) != 1:
            raise ValueError(
                "from_commit requires exactly one of commit=..., ref=..."
            )
        if commit is not None:
            body = {"commit_id": str(commit)}
        else:
            assert ref is not None
            body = {"ref": str(ref)}
        d = self._request("POST", "/vm/from_commit", json=body)
        return VmId(d["vm_id"])

    # -------- repositories and tags --------

    def list_repos(self) -> list[Repo]:
        d = self._request("GET", "/repositories")
        return [_repo_from_dict(r) for r in d.get("repositories", [])]

    def get_repo(self, name: RepoName) -> Repo:
        d = self._request("GET", f"/repositories/{name}")
        return _repo_from_dict(d)

    def create_repo(
        self,
        name: RepoName,
        *,
        description: str = "",
    ) -> Repo:
        body = {"name": str(name), "description": description}
        d = self._request("POST", "/repositories", json=body)
        # Create response is light; refetch for full record.
        return self.get_repo(name)

    def delete_repo(self, name: RepoName) -> None:
        self._request("DELETE", f"/repositories/{name}")

    def tag(
        self,
        repo: RepoName,
        tag_name: str,
        commit: CommitId,
    ) -> RepoRef:
        """Create a new tag pointing at a commit."""
        body = {"tag_name": tag_name, "commit_id": str(commit)}
        self._request("POST", f"/repositories/{repo}/tags", json=body)
        return RepoRef(repo=repo, tag=tag_name)

    def list_tags(self, repo: RepoName) -> list[Tag]:
        d = self._request("GET", f"/repositories/{repo}/tags")
        return [_tag_from_dict(t, repo) for t in d.get("tags", [])]

    def fork(
        self,
        source_org: str,
        source_repo: str,
        source_tag: str,
        *,
        into_repo: RepoName | str | None = None,
        into_tag: str | None = None,
    ) -> "ForkResult":
        """Fork a public repo:tag into your namespace.

        IMPORTANT side effect: the server creates a NEW VM as part of the
        fork operation (snapshot of the forked source). The vm comes up
        running with an ip. If you only want the repo+tag and not the
        vm, delete the vm yourself after this call.

        Args:
            source_org: org that owns the source public repo (e.g. "hdr-is")
            source_repo: source repo name (e.g. "go")
            source_tag: source tag to fork (e.g. "latest")
            into_repo: target repo name in your org (default: source_repo)
            into_tag: tag in your new repo (default: source_tag)

        Returns:
            ForkResult with .ref (the new RepoRef), .vm (the side-effect
            VmId), and .commit (the new CommitId in your org).
        """
        body: dict[str, Any] = {
            "source_org": source_org,
            "source_repo": source_repo,
            "source_tag": source_tag,
        }
        if into_repo is not None:
            body["repo_name"] = str(into_repo)
        if into_tag is not None:
            body["tag_name"] = into_tag
        d = self._request("POST", "/repositories/fork", json=body)
        return ForkResult(
            ref=RepoRef.parse(d["reference"]),
            vm=VmId(d["vm_id"]),
            commit=CommitId(d["commit_id"]),
        )

    # -------- env vars (boot-time) --------

    def get_env(self) -> dict[str, str]:
        """Get the api key's boot-time env vars. These are written to
        /etc/environment in NEW vms; existing vms are not affected."""
        d = self._request("GET", "/env_vars")
        return dict(d.get("vars", {}))

    def set_env(
        self,
        vars: dict[str, str],
        *,
        replace: bool = False,
    ) -> dict[str, str]:
        """Merge (replace=False) or overwrite (replace=True) boot-time env vars."""
        for k in vars:
            env_key(k)  # validate
        body = {"vars": vars, "replace": replace}
        d = self._request("PUT", "/env_vars", json=body)
        return dict(d.get("vars", {}))

    def del_env(self, key: EnvVarKey) -> None:
        self._request("DELETE", f"/env_vars/{key}")

    # -------- domains --------

    def list_domains(self) -> list[Domain]:
        d = self._request("GET", "/domains")
        return [
            Domain(
                domain_id=DomainId(x["domain_id"]),
                domain=x["domain"],
                vm_id=VmId(x["vm_id"]),
                created_at=x.get("created_at", ""),
            )
            for x in d
        ]

    def create_domain(self, vm: VmId, hostname: str | None = None) -> Domain:
        body: dict[str, Any] = {"vm_id": str(vm)}
        if hostname:
            body["domain"] = hostname
        d = self._request("POST", "/domains", json=body)
        return Domain(
            domain_id=DomainId(d["domain_id"]),
            domain=d["domain"],
            vm_id=VmId(d["vm_id"]),
            created_at=d.get("created_at", ""),
        )

    def delete_domain(self, did: DomainId) -> None:
        self._request("DELETE", f"/domains/{did}")


# =====================================================================
# helpers
# =====================================================================


def _repo_from_dict(d: dict[str, Any]) -> Repo:
    return Repo(
        repo_id=d["repo_id"],
        name=RepoName(d["name"]),
        description=d.get("description"),
        is_public=bool(d.get("is_public", False)),
        created_at=d.get("created_at", ""),
    )


def _tag_from_dict(d: dict[str, Any], repo: RepoName) -> Tag:
    return Tag(
        tag_id=d.get("tag_id", ""),
        tag_name=d["tag_name"],
        repo_name=repo,
        commit_id=CommitId(d["commit_id"]),
        reference=RepoRef(repo=repo, tag=d["tag_name"]),
    )


# =====================================================================
# the two-phase pattern: workflow helpers
# =====================================================================


@contextmanager
def branched_vm(
    client: Client,
    source: VmId | CommitId | RepoRef,
    *,
    auto_pause: bool = True,
) -> Iterator[VmId]:
    """Context manager: branch from source, yield the VM, optionally pause on exit.

    Canonical phase-2 pattern:

        with branched_vm(c, base_image_ref) as vm:
            result = c.exec(vm, ['python', 'work.py'])
        # VM paused on exit unless auto_pause=False. No hidden termination.
    """
    vms = client.branch_from(source)
    if not vms:
        raise VersError(0, "branch returned no vms", "", "POST", "(branch)")
    vm = vms[0]
    try:
        yield vm
    finally:
        if auto_pause:
            try:
                client.pause(vm)
            except VersError:
                pass


def prepare_image(
    client: Client,
    *,
    base: RepoRef | CommitId | None,
    prep_steps: list[list[str]],
    tag_as: tuple[RepoName, str],
    description: str = "",
    mem_mib: int | None = None,
    vcpu: int | None = None,
    fs_mib: int | None = None,
    auto_pause: bool = True,
) -> RepoRef:
    """Phase-1 image preparation. Spin up a base, run prep steps, commit, tag.

    Args:
        base: a RepoRef or CommitId to start from, or None for a fresh new_root.
        prep_steps: list of commands (each a list of argv-style strings) to
            run sequentially. Aborts on first non-zero exit.
        tag_as: (repo_name, tag_name) for the resulting commit.
        description: commit description.
        mem_mib / vcpu / fs_mib: required only if base is None (fresh VM).
        auto_pause: pause the prep VM before returning or re-raising.

    Returns the new RepoRef. Leaves the prep VM paused by default; it does not
    hide VM termination inside success or failure cleanup.

    Note on ready-state discipline: each step's stdout/stderr is printed to
    the python logger; the commit happens AFTER all steps complete. The vm
    state at commit time is what every future branch from this tag inherits,
    so make sure your final step leaves the system in a useful equilibrium
    (services started, caches warmed) and not mid-startup.
    """
    import logging

    log = logging.getLogger("vers.prepare_image")
    repo, tag_name = tag_as

    if base is None:
        missing = [
            name for name, value in (
                ("mem_mib", mem_mib),
                ("vcpu", vcpu),
                ("fs_mib", fs_mib),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                "prepare_image(base=None) requires explicit fresh-VM dimensions: "
                + ", ".join(missing)
            )

    # Make sure the repo exists; create if not.
    try:
        client.get_repo(repo)
    except VersNotFound:
        client.create_repo(repo, description=description or f"created by prepare_image")

    # Materialize the prep VM.
    if base is None:
        assert mem_mib is not None and vcpu is not None and fs_mib is not None
        vm = client.new_root(mem_mib=mem_mib, vcpu=vcpu, fs_mib=fs_mib)
        log.info("phase-1 fresh vm: %s", vm)
    else:
        vms = client.branch_from(base)
        vm = vms[0]
        log.info("phase-1 branched from %r: %s", base, vm)

    try:
        for i, step in enumerate(prep_steps):
            log.info("step %d/%d: %s", i + 1, len(prep_steps), step)
            r = client.exec(vm, step)
            if not r.ok:
                raise VersError(
                    status=r.exit_code,
                    message=f"prep step {i+1} failed (exit={r.exit_code}): {step}",
                    raw_body=r.stderr or r.stdout,
                    method="POST",
                    url=f"/vm/{vm}/exec",
                )
        c = client.commit(vm, name=f"{repo}:{tag_name}", description=description)
        ref = client.tag(repo, tag_name, c)
        log.info("phase-1 done; tagged as %s -> commit %s", ref, c)
        return ref
    finally:
        if auto_pause:
            try:
                client.pause(vm)
            except VersError:
                pass


# ============================================================================
# CLI: `uv run vers.py <subcommand> ...`
# ============================================================================
#
# Subcommand path / id positional / json out. Every subcommand prints a JSON
# document to stdout on success; errors go to stderr with a JSON envelope and
# a nonzero exit code. Output is pretty-printed by default; pass --compact for
# single-line JSON (pipe-friendly).
#
# Examples:
#     uv run vers.py whoami
#     uv run vers.py vm list --mine
#     uv run vers.py vm get 3c9e74df-...
#     uv run vers.py vm new --mem-mib <MiB> --vcpu <N> --fs-mib <MiB>
#     uv run vers.py vm exec <uuid> -- ls -la /
#     uv run vers.py repo list
#     uv run vers.py tag list bases
#     uv run vers.py branch ref bases:python-v1 --count 4
#     uv run vers.py branch vm <uuid>
#     uv run vers.py branch commit <uuid>
#     uv run vers.py env set FOO=bar BAZ=qux
#
# The CLI is a thin shell over the typed Python helper. For complex flows
# (multi-step image prep, branched_vm context manager, exec_stream consumption)
# import vers from a script with its own PEP 723 header.

def _emit(value: Any, *, compact: bool) -> None:
    """Write a JSON document to stdout. value may be a dataclass, a list of
    dataclasses, a primitive, or already a dict."""
    import dataclasses as _dc
    import json as _json

    def _normalize(x: Any) -> Any:
        if _dc.is_dataclass(x) and not isinstance(x, type):
            return {k: _normalize(v) for k, v in _dc.asdict(x).items()}
        if isinstance(x, (list, tuple)):
            return [_normalize(i) for i in x]
        if isinstance(x, dict):
            return {str(k): _normalize(v) for k, v in x.items()}
        if isinstance(x, bytes):
            return x.decode("utf-8", errors="replace")
        return x

    payload = _normalize(value)
    if compact:
        print(_json.dumps(payload, separators=(",", ":"), default=str))
    else:
        print(_json.dumps(payload, indent=2, default=str))


def _cli_error(exc: BaseException, *, compact: bool) -> int:
    """Print a JSON error envelope to stderr; return an exit code."""
    import json as _json
    import sys as _sys

    if isinstance(exc, VersError):
        envelope: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "status": exc.status,
            "message": exc.message,
        }
        if exc.url is not None:
            envelope["request"] = f"{exc.method} {exc.url}"
        if exc.raw_body and len(exc.raw_body) <= 800:
            envelope["raw_body"] = exc.raw_body
        exit_code = 2
    elif isinstance(exc, (ValueError, TypeError)):
        envelope = {"error_type": type(exc).__name__, "message": str(exc)}
        exit_code = 64  # EX_USAGE
    else:
        envelope = {"error_type": type(exc).__name__, "message": str(exc)}
        exit_code = 1

    if compact:
        print(_json.dumps(envelope, separators=(",", ":")), file=_sys.stderr)
    else:
        print(_json.dumps(envelope, indent=2), file=_sys.stderr)
    return exit_code


def _schema_for_leaf(parser_obj: "argparse.ArgumentParser",
                     leaf_path: str) -> dict[str, Any]:
    """Emit a JSON-Schema-shaped dict describing the args of a leaf subparser.

    For LLM tool-use harness ingestion. Only the args actually accepted by
    this leaf appear; --json itself is documented as the alternative bulk-input
    form, not as a property."""
    import argparse
    schema: dict[str, Any] = {
        "leaf": leaf_path,
        "description": parser_obj.description
            or (parser_obj.format_help().split("\n")[0] if parser_obj.format_help() else ""),
        "properties": {},
        "required": [],
        "exactly_one_of": [],
    }
    for action in parser_obj._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.dest == "json_args":
            continue
        if action.dest in ("compact", "base_url"):
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue
        prop: dict[str, Any] = {"flag": "--" + action.dest.replace("_", "-")}
        if action.help:
            prop["description"] = action.help
        if action.choices:
            prop["enum"] = list(action.choices)
        if action.type is int:
            prop["type"] = "integer"
        elif action.type is float:
            prop["type"] = "number"
        elif isinstance(action, argparse._StoreTrueAction):
            prop["type"] = "boolean"
        else:
            prop["type"] = "string"
        if action.default is not None and not isinstance(
            action, argparse._StoreTrueAction
        ):
            prop["default"] = action.default
        schema["properties"][action.dest] = prop

    schema["required"] = list(parser_obj.get_default("_required") or [])
    schema["exactly_one_of"] = list(
        parser_obj.get_default("_exactly_one_of") or []
    )
    return schema


def _all_leaf_parsers(
    parser_obj: "argparse.ArgumentParser", path: str = ""
) -> list[tuple[str, "argparse.ArgumentParser"]]:
    """Walk an argparse parser tree, return (leaf_path, leaf_parser) pairs
    for every leaf subparser (one with a _handler default and no further
    subparsers)."""
    import argparse
    out: list[tuple[str, "argparse.ArgumentParser"]] = []
    has_handler = parser_obj.get_default("_handler") is not None
    has_subparsers = any(
        isinstance(a, argparse._SubParsersAction) for a in parser_obj._actions
    )
    if has_handler and not has_subparsers:
        out.append((path.strip(), parser_obj))
    for action in parser_obj._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subp in action.choices.items():
                out.extend(_all_leaf_parsers(subp, f"{path} {name}"))
    return out


def _build_parser() -> "argparse.ArgumentParser":
    """All-named-flags subcommand tree. Designed for LLM callers: every
    semantically-distinct argument is a named flag, no positionals, no
    bash-shorthand conventions like ``--`` separators or ``KEY=VAL`` pairs.
    One canonical form per operation. Tool-use schemas map onto this 1:1.

    Every leaf subcommand also accepts ``--json '<dict>'`` as an alternative
    bulk-input form. Keys mirror the flag names in snake_case (e.g. ``--vm-id``
    on the CLI is ``vm_id`` in the JSON dict). If both ``--json`` and named
    flags are provided, ``--json`` values win. Pass ``--json -`` to read the
    dict from stdin.

    This dual form means LLM tool-use schemas can be uniform across the entire
    surface: ``{"command": "vm new", "json": {...}}`` for every command.
    Required-arg validation happens in dispatch (after JSON merge), not in
    argparse itself.
    """
    import argparse

    def _add_json_arg(sp: "argparse.ArgumentParser") -> None:
        sp.add_argument(
            "--json", dest="json_args", default=None, metavar="DICT",
            help="alternative bulk-input form: a JSON object whose keys "
                 "mirror this subcommand's flags in snake_case. Use '-' to "
                 "read JSON from stdin. Supersedes named flags if both given.",
        )

    p = argparse.ArgumentParser(
        prog="vers.py",
        description=(
            "CLI for the vers.sh API. Reads VERS_API_KEY from the environment. "
            "Outputs JSON to stdout on success, JSON error envelopes to stderr. "
            "Exit codes: 0 ok, 2 API error, 64 usage error."
        ),
    )
    p.add_argument(
        "--compact", action="store_true",
        help="emit single-line JSON instead of indented",
    )
    p.add_argument(
        "--base-url", default=None,
        help="override base URL (default: VERS_BASE_URL env or "
             "https://api.vers.sh/api/v1)",
    )

    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    # ---- whoami ----
    p_whoami = sub.add_parser(
        "whoami", help="print owner_id derived from VERS_API_KEY",
    )
    _add_json_arg(p_whoami)
    p_whoami.set_defaults(_handler="whoami", _json_keys=[], _required=[])

    # ---- schema (machine-readable spec of the full CLI surface) ----
    p_schema = sub.add_parser(
        "schema",
        help="emit JSON Schema of every leaf subcommand. For LLM harnesses "
             "that want to generate tool-use schemas from this CLI directly.",
    )
    p_schema.add_argument(
        "--leaf", default=None,
        help="emit schema for one specific leaf (e.g. 'vm new'). Without "
             "--leaf, emits the full surface as a dict keyed by leaf path.",
    )
    _add_json_arg(p_schema)
    p_schema.set_defaults(_handler="schema", _json_keys=["leaf"], _required=[])

    # ---- vm group ----
    vm = sub.add_parser("vm", help="vm operations").add_subparsers(
        dest="vm_sub", required=True, metavar="<subcommand>"
    )

    p_vlist = vm.add_parser("list", help="list visible vms")
    p_vlist.add_argument("--owner", choices=("mine", "all"), default="all",
                         help="filter by owner (default: all visible)")
    _add_json_arg(p_vlist)
    p_vlist.set_defaults(_handler="vm_list", _json_keys=["owner"], _required=[])

    p_vget = vm.add_parser("get", help="get vm metadata (includes IPv6)")
    p_vget.add_argument("--vm-id", default=None)
    _add_json_arg(p_vget)
    p_vget.set_defaults(_handler="vm_get", _json_keys=["vm_id"], _required=["vm_id"])

    p_vnew = vm.add_parser("new", help="cold-start a new root VM")
    p_vnew.add_argument("--mem-mib", type=int, default=None)
    p_vnew.add_argument("--vcpu", type=int, default=None)
    p_vnew.add_argument("--fs-mib", type=int, default=None)
    p_vnew.add_argument("--wait-boot", choices=("true", "false"), default="true",
                        help="wait for boot (default: true)")
    _add_json_arg(p_vnew)
    p_vnew.set_defaults(
        _handler="vm_new",
        _json_keys=["mem_mib", "vcpu", "fs_mib", "wait_boot"],
        _required=["mem_mib", "vcpu", "fs_mib"],
    )

    p_vpause = vm.add_parser("pause", help="pause a running vm")
    p_vpause.add_argument("--vm-id", default=None)
    _add_json_arg(p_vpause)
    p_vpause.set_defaults(_handler="vm_pause", _json_keys=["vm_id"], _required=["vm_id"])

    p_vresume = vm.add_parser("resume", help="resume a paused vm")
    p_vresume.add_argument("--vm-id", default=None)
    _add_json_arg(p_vresume)
    p_vresume.set_defaults(_handler="vm_resume", _json_keys=["vm_id"], _required=["vm_id"])

    p_vexec = vm.add_parser(
        "exec",
        help='exec a command in a vm. --argv takes a JSON array, '
             'e.g. \'["sh","-c","echo hi"]\'.',
    )
    p_vexec.add_argument("--vm-id", default=None)
    p_vexec.add_argument("--argv", default=None,
                         help="command as JSON array of strings")
    _add_json_arg(p_vexec)
    p_vexec.set_defaults(
        _handler="vm_exec",
        _json_keys=["vm_id", "argv"],
        _required=["vm_id", "argv"],
    )

    p_vlogs = vm.add_parser("logs", help="get exec logs for a vm")
    p_vlogs.add_argument("--vm-id", default=None)
    p_vlogs.add_argument("--max-entries", type=int, default=None)
    p_vlogs.add_argument("--offset", type=int, default=None)
    _add_json_arg(p_vlogs)
    p_vlogs.set_defaults(
        _handler="vm_logs",
        _json_keys=["vm_id", "max_entries", "offset"],
        _required=["vm_id"],
    )

    p_vssh = vm.add_parser("ssh-key", help="get the vm's ssh public key")
    p_vssh.add_argument("--vm-id", default=None)
    _add_json_arg(p_vssh)
    p_vssh.set_defaults(_handler="vm_ssh_key", _json_keys=["vm_id"], _required=["vm_id"])

    # ---- repo group ----
    repo = sub.add_parser("repo", help="repository operations").add_subparsers(
        dest="repo_sub", required=True, metavar="<subcommand>"
    )

    p_rlist = repo.add_parser("list", help="list repositories")
    _add_json_arg(p_rlist)
    p_rlist.set_defaults(_handler="repo_list", _json_keys=[], _required=[])

    p_rget = repo.add_parser("get", help="get a repository")
    p_rget.add_argument("--name", default=None)
    _add_json_arg(p_rget)
    p_rget.set_defaults(_handler="repo_get", _json_keys=["name"], _required=["name"])

    p_rcreate = repo.add_parser("create", help="create a repository")
    p_rcreate.add_argument("--name", default=None)
    p_rcreate.add_argument("--description", default=None)
    _add_json_arg(p_rcreate)
    p_rcreate.set_defaults(
        _handler="repo_create",
        _json_keys=["name", "description"],
        _required=["name"],
    )

    p_rdel = repo.add_parser("delete", help="delete a repository (and tags)")
    p_rdel.add_argument("--name", default=None)
    _add_json_arg(p_rdel)
    p_rdel.set_defaults(_handler="repo_delete", _json_keys=["name"], _required=["name"])

    # ---- tag group ----
    tag = sub.add_parser("tag", help="tag operations").add_subparsers(
        dest="tag_sub", required=True, metavar="<subcommand>"
    )

    p_tlist = tag.add_parser("list", help="list tags in a repository")
    p_tlist.add_argument("--repo", default=None)
    _add_json_arg(p_tlist)
    p_tlist.set_defaults(_handler="tag_list", _json_keys=["repo"], _required=["repo"])

    p_tcreate = tag.add_parser("create", help="create a tag pointing to a commit")
    p_tcreate.add_argument("--repo", default=None)
    p_tcreate.add_argument("--tag", default=None)
    p_tcreate.add_argument("--commit-id", default=None)
    _add_json_arg(p_tcreate)
    p_tcreate.set_defaults(
        _handler="tag_create",
        _json_keys=["repo", "tag", "commit_id"],
        _required=["repo", "tag", "commit_id"],
    )

    # ---- commit ----
    p_commit = sub.add_parser("commit", help="commit a vm's current state")
    p_commit.add_argument("--vm-id", default=None)
    p_commit.add_argument("--name", default=None)
    p_commit.add_argument("--description", default=None)
    _add_json_arg(p_commit)
    p_commit.set_defaults(
        _handler="commit",
        _json_keys=["vm_id", "name", "description"],
        _required=["vm_id"],
    )

    # ---- branch (typed dispatch via mutually-exclusive flags) ----
    # NOTE: when --json is given, the mutex is enforced in _resolve_json
    # by checking that exactly one of (ref, vm_id, commit_id) is present
    # in the dict. argparse itself can't express "this group is mutex
    # unless --json is given," so we drop add_mutually_exclusive_group
    # and validate in dispatch.
    p_branch = sub.add_parser(
        "branch",
        help="branch a new vm. Specify exactly one source: --ref, --vm-id, "
             "or --commit-id.",
    )
    p_branch.add_argument("--ref", default=None,
                          help="repo:tag (e.g. bases:python-v1)")
    p_branch.add_argument("--vm-id", default=None,
                          help="branch from live vm")
    p_branch.add_argument("--commit-id", default=None,
                          help="branch from a commit uuid")
    p_branch.add_argument("--count", type=int, default=None,
                          help="server-side fan-out: create N branches")
    _add_json_arg(p_branch)
    p_branch.set_defaults(
        _handler="branch",
        _json_keys=["ref", "vm_id", "commit_id", "count"],
        _required=[],  # mutex enforced in dispatch
        _exactly_one_of=["ref", "vm_id", "commit_id"],
    )

    # ---- from-commit ----
    p_fc = sub.add_parser(
        "from-commit",
        help="instantiate a vm from a commit or repo:tag (alternative to "
             "branch). Specify exactly one source.",
    )
    p_fc.add_argument("--commit-id", default=None)
    p_fc.add_argument("--ref", default=None, help="repo:tag")
    _add_json_arg(p_fc)
    p_fc.set_defaults(
        _handler="from_commit",
        _json_keys=["commit_id", "ref"],
        _required=[],
        _exactly_one_of=["commit_id", "ref"],
    )

    # ---- env vars ----
    env = sub.add_parser(
        "env", help="environment variables (boot-time only on next vm)"
    ).add_subparsers(dest="env_sub", required=True, metavar="<subcommand>")

    p_elist = env.add_parser("list", help="list env vars")
    _add_json_arg(p_elist)
    p_elist.set_defaults(_handler="env_list", _json_keys=[], _required=[])

    p_eset = env.add_parser(
        "set",
        help='set env vars. --vars takes a JSON object: '
             '\'{"FOO":"bar","BAZ":"qux"}\'',
    )
    p_eset.add_argument("--vars", default=None,
                        help='JSON object of env vars to set')
    p_eset.add_argument("--mode", choices=("merge", "replace"), default="merge",
                        help="merge with existing env (default) or replace")
    _add_json_arg(p_eset)
    p_eset.set_defaults(
        _handler="env_set",
        _json_keys=["vars", "mode"],
        _required=["vars"],
    )

    p_edel = env.add_parser("delete", help="delete an env var")
    p_edel.add_argument("--key", default=None)
    _add_json_arg(p_edel)
    p_edel.set_defaults(_handler="env_delete", _json_keys=["key"], _required=["key"])

    # ---- domain ----
    domain = sub.add_parser("domain", help="domain operations").add_subparsers(
        dest="domain_sub", required=True, metavar="<subcommand>"
    )

    p_dlist = domain.add_parser("list", help="list domains")
    _add_json_arg(p_dlist)
    p_dlist.set_defaults(_handler="domain_list", _json_keys=[], _required=[])

    p_dcreate = domain.add_parser("create", help="bind a hostname to a vm")
    p_dcreate.add_argument("--vm-id", default=None)
    p_dcreate.add_argument(
        "--hostname", default=None,
        help="fully-qualified hostname (must have at least 2 parts, e.g. "
             "example.com)",
    )
    _add_json_arg(p_dcreate)
    p_dcreate.set_defaults(
        _handler="domain_create",
        _json_keys=["vm_id", "hostname"],
        _required=["vm_id", "hostname"],
    )

    p_ddel = domain.add_parser("delete", help="delete a domain binding")
    p_ddel.add_argument("--domain-id", default=None)
    _add_json_arg(p_ddel)
    p_ddel.set_defaults(
        _handler="domain_delete",
        _json_keys=["domain_id"],
        _required=["domain_id"],
    )

    return p


def _resolve_json(args: "argparse.Namespace") -> None:
    """If ``args.json_args`` is set, parse it and patch values onto args.
    JSON values supersede flag values. Validates keys against
    ``args._json_keys`` (rejects unknown keys to surface typos).

    JSON dict keys are snake_case, matching the helper's parameter names.
    They are translated to argparse attribute names (also snake_case, since
    argparse converts --vm-id to vm_id automatically).
    """
    import json as _json
    import sys as _sys

    raw = args.json_args
    if raw is None:
        return

    if raw == "-":
        raw = _sys.stdin.read()

    try:
        d = _json.loads(raw)
    except _json.JSONDecodeError as e:
        raise ValueError(f"--json must be a valid JSON object: {e}")

    if not isinstance(d, dict):
        raise ValueError(
            f"--json must be a JSON object (dict), got {type(d).__name__}"
        )

    allowed = set(getattr(args, "_json_keys", []))
    unknown = set(d.keys()) - allowed
    if unknown:
        raise ValueError(
            f"--json contains unknown keys for this subcommand: "
            f"{sorted(unknown)}. Allowed: {sorted(allowed)}"
        )

    for k, v in d.items():
        # Booleans and the wait_boot flag: argparse stores wait_boot as
        # str "true"/"false" via choices=, but --json may pass a real bool.
        # Normalize at use-site in dispatch; here we just patch raw.
        setattr(args, k, v)


def _full_cmd_path(args: "argparse.Namespace") -> str:
    """Reconstruct the full subcommand path the user invoked.
    e.g. ``args.cmd='vm', args.vm_sub='get'`` -> ``'vm get'``.
    For top-level commands without a sub (whoami, commit, branch, from-commit),
    returns just ``args.cmd``."""
    cmd = args.cmd
    sub = getattr(args, f"{cmd.replace('-', '_')}_sub", None)
    return f"{cmd} {sub}" if sub else cmd


def _validate_required(args: "argparse.Namespace") -> None:
    """After --json patching, check that ``args._required`` keys are non-None."""
    missing = [k for k in getattr(args, "_required", [])
               if getattr(args, k, None) is None]
    if missing:
        path = _full_cmd_path(args)
        flag_examples = ", ".join(
            f"--{k.replace('_', '-')} <value>" for k in missing
        )
        json_example = "{" + ", ".join(
            f'"{k}": <value>' for k in missing
        ) + "}"
        raise ValueError(
            f"missing required arg(s) for `{path}`: {missing}. "
            f"Provide as: {flag_examples}  "
            f"or via: --json '{json_example}'"
        )


def _validate_exactly_one(args: "argparse.Namespace") -> None:
    """Enforce mutex groups declared via ``args._exactly_one_of``."""
    keys = getattr(args, "_exactly_one_of", None)
    if not keys:
        return
    set_keys = [k for k in keys if getattr(args, k, None) is not None]
    path = _full_cmd_path(args)
    flag_form = " | ".join(f"--{k.replace('_', '-')} <value>" for k in keys)
    json_form = " | ".join(
        f'\'{{"{k}": <value>}}\'' for k in keys
    )
    if len(set_keys) == 0:
        raise ValueError(
            f"`{path}` requires exactly one of: {keys} (none were given). "
            f"Provide one of: {flag_form}  "
            f"or via --json: {json_form}"
        )
    if len(set_keys) > 1:
        raise ValueError(
            f"`{path}` requires exactly one of: {keys} (got {set_keys}). "
            f"Use only one of: {flag_form}"
        )


def _dispatch(args: "argparse.Namespace") -> int:  # noqa: C901
    """Run one subcommand. Returns exit code."""
    import json as _json

    compact = args.compact
    try:
        _resolve_json(args)
        _validate_exactly_one(args)
        _validate_required(args)

        # `schema` is special: introspects the parser, no API call needed.
        if args._handler == "schema":
            parser = _build_parser()
            leaves = dict(_all_leaf_parsers(parser))
            requested = args.leaf
            if requested:
                if requested not in leaves:
                    raise ValueError(
                        f"unknown leaf {requested!r}. Available: "
                        f"{sorted(leaves.keys())}"
                    )
                _emit(_schema_for_leaf(leaves[requested], requested),
                      compact=compact)
            else:
                _emit(
                    {path: _schema_for_leaf(p, path)
                     for path, p in leaves.items()},
                    compact=compact,
                )
            return 0

        # normalize wait_boot to bool (may be str "true"/"false" from cli or
        # bool from --json)
        if hasattr(args, "wait_boot") and args.wait_boot is not None:
            wb = args.wait_boot
            if isinstance(wb, str):
                args.wait_boot = wb.lower() == "true"

        kwargs: dict[str, Any] = {}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        with Client(**kwargs) as c:
            h = args._handler

            if h == "whoami":
                _emit({"owner_id": c.owner_id}, compact=compact)

            elif h == "vm_list":
                _emit(c.list_vms(owned_by_me=(args.owner == "mine")),
                      compact=compact)
            elif h == "vm_get":
                _emit(c.get_vm(vm_id(args.vm_id)), compact=compact)
            elif h == "vm_new":
                v = c.new_root(
                    mem_mib=args.mem_mib, vcpu=args.vcpu, fs_mib=args.fs_mib,
                    wait_boot=cast(bool, args.wait_boot),
                )
                _emit({"vm_id": v}, compact=compact)
            elif h == "vm_pause":
                c.pause(vm_id(args.vm_id))
                _emit({"paused": args.vm_id}, compact=compact)
            elif h == "vm_resume":
                c.resume(vm_id(args.vm_id))
                _emit({"resumed": args.vm_id}, compact=compact)
            elif h == "vm_exec":
                # argv may be a JSON array as a string (from --argv flag)
                # or already a list (from --json '{"argv": [...]}'), since
                # JSON decode unpacks the inner array natively
                argv_in = args.argv
                if isinstance(argv_in, str):
                    try:
                        argv_list = _json.loads(argv_in)
                    except _json.JSONDecodeError as e:
                        raise ValueError(
                            f"--argv must be a JSON array of strings; "
                            f"got unparseable: {e}"
                        )
                else:
                    argv_list = argv_in
                if (not isinstance(argv_list, list)
                        or not all(isinstance(x, str) for x in argv_list)
                        or not argv_list):
                    raise ValueError(
                        "argv must be a non-empty array of strings"
                    )
                _emit(c.exec(vm_id(args.vm_id), argv_list), compact=compact)
            elif h == "vm_logs":
                _emit(c.get_logs(
                    vm_id(args.vm_id),
                    offset=args.offset, max_entries=args.max_entries,
                ), compact=compact)
            elif h == "vm_ssh_key":
                _emit({"ssh_key": c.get_ssh_key(vm_id(args.vm_id))},
                      compact=compact)

            elif h == "repo_list":
                _emit(c.list_repos(), compact=compact)
            elif h == "repo_get":
                _emit(c.get_repo(RepoName(args.name)), compact=compact)
            elif h == "repo_create":
                _emit(c.create_repo(RepoName(args.name),
                                    description=args.description),
                      compact=compact)
            elif h == "repo_delete":
                c.delete_repo(RepoName(args.name))
                _emit({"deleted": args.name}, compact=compact)

            elif h == "tag_list":
                _emit(c.list_tags(RepoName(args.repo)), compact=compact)
            elif h == "tag_create":
                _emit(c.tag(RepoName(args.repo), args.tag,
                            commit_id(args.commit_id)),
                      compact=compact)

            elif h == "commit":
                _emit(c.commit(vm_id(args.vm_id),
                               name=args.name, description=args.description),
                      compact=compact)

            elif h == "branch":
                if args.ref is not None:
                    source: Any = RepoRef.parse(args.ref)
                elif args.vm_id is not None:
                    source = vm_id(args.vm_id)
                else:
                    source = commit_id(args.commit_id)
                result = c.branch_from(source, count=args.count)
                _emit(
                    {"vms": result} if isinstance(result, list)
                    else {"vm_id": result},
                    compact=compact,
                )

            elif h == "from_commit":
                if args.commit_id is not None:
                    v = c.from_commit(commit=commit_id(args.commit_id))
                else:
                    v = c.from_commit(ref=RepoRef.parse(args.ref))
                _emit({"vm_id": v}, compact=compact)

            elif h == "env_list":
                _emit(c.get_env(), compact=compact)
            elif h == "env_set":
                # vars may be a JSON string (from --vars) or a dict
                # (from --json '{"vars": {...}}')
                vars_in = args.vars
                if isinstance(vars_in, str):
                    try:
                        var_dict = _json.loads(vars_in)
                    except _json.JSONDecodeError as e:
                        raise ValueError(
                            f"--vars must be a valid JSON object: {e}"
                        )
                else:
                    var_dict = vars_in
                if not isinstance(var_dict, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in var_dict.items()
                ):
                    raise ValueError(
                        "vars must be a JSON object mapping string keys "
                        "to string values"
                    )
                _emit(c.set_env(var_dict, replace=(args.mode == "replace")),
                      compact=compact)
            elif h == "env_delete":
                c.del_env(env_key(args.key))
                _emit({"deleted": args.key}, compact=compact)

            elif h == "domain_list":
                _emit(c.list_domains(), compact=compact)
            elif h == "domain_create":
                _emit(c.create_domain(vm_id(args.vm_id),
                                      hostname=args.hostname),
                      compact=compact)
            elif h == "domain_delete":
                c.delete_domain(DomainId(args.domain_id))
                _emit({"deleted": args.domain_id}, compact=compact)

            else:
                raise ValueError(f"no handler for: {h}")

            return 0

    except (VersError, VersConfigError, ValueError, TypeError, KeyboardInterrupt) as e:
        return _cli_error(e, compact=compact)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Argparse failures (SystemExit from .parse_args) are
    intercepted and re-emitted as VersCliUsageError → JSON envelope on stderr,
    exit 64. Other failures handled by _dispatch's outer except."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse called .exit() — likely a usage error. Re-emit as JSON envelope.
        # If it was --help (exit 0), let it through unchanged.
        if exc.code == 0 or exc.code is None:
            raise
        return _cli_error(
            VersCliUsageError(f"argparse usage error (exit code {exc.code})"),
            compact=False,
        )
    return _dispatch(args)


if __name__ == "__main__":
    import sys
    sys.exit(main())
