# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx",
# ]
# ///
"""Offline smoke test for vers.py.

Validates the type-wrapper layer + client-side guards. NO network, NO API key.

Run:
    uv run smoke_test.py

(With `uv` installed, this is the only command you need. The PEP 723 header
above declares the httpx dependency; uv resolves it into an ephemeral venv.
No pip install, no manual venv setup.)

This is the minimum check before considering any change to vers.py 'release-cleared'.
Suggested hook: run before packaging any vers_skill release.
"""

import sys
import traceback

sys.path.insert(0, ".")

# Defensive: if vers was imported in a prior runpy invocation in the same
# python process, drop it so we re-resolve from disk. Catches a class of
# false-pass where the test imports a stale module.
sys.modules.pop("vers", None)


def _expect_raise(exc_type, fn, *args, **kwargs):
    """Assert that calling fn(*args, **kwargs) raises exc_type."""
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(
            f"expected {exc_type.__name__}, got {type(e).__name__}: {e}"
        )
    raise AssertionError(f"expected {exc_type.__name__}, no exception raised")


_failures: list[tuple[str, str]] = []


def check(label, fn):
    try:
        result = fn()
        if result is False:
            raise AssertionError("check returned False")
        print(f"  ok: {label}")
    except AssertionError as e:
        _failures.append((label, str(e)))
        print(f"  FAIL: {label}: {e}")
    except Exception as e:  # noqa: BLE001
        _failures.append((label, f"{type(e).__name__}: {e}"))
        print(f"  ERROR: {label}: {type(e).__name__}: {e}")
        traceback.print_exc()


# A throwaway-but-syntactically-valid UUID used everywhere we need one.
NULL_UUID = "00000000-0000-0000-0000-000000000000"


def main() -> int:
    print("=== imports ===")
    check("vers module imports", lambda: __import__("vers"))

    import vers
    from vers import (
        Client,
        VmId,
        CommitId,
        RepoName,
        RepoRef,
        EnvVarKey,
        DomainId,
        vm_id,
        commit_id,
        env_key,
        VersError,
        VersAuthError,
        VersForbidden,
        VersNotFound,
        VersConflict,
        VersBadRequest,
        VersValidationError,
        VersHybridBranchError,
        VersServerError,
        VersConfigError,
        VersCliUsageError,
    )

    print("\n=== type wrappers (id types) ===")
    check(
        "vm_id(NULL_UUID) returns a VmId instance and is a str subclass",
        lambda: isinstance(vm_id(NULL_UUID), VmId) and isinstance(vm_id(NULL_UUID), str),
    )
    check(
        "commit_id(NULL_UUID) returns a CommitId instance",
        lambda: isinstance(commit_id(NULL_UUID), CommitId),
    )
    check(
        "VmId and CommitId are distinct classes",
        lambda: VmId is not CommitId,
    )
    check(
        "VmId rejects a non-UUID string at construction",
        lambda: _expect_raise(ValueError, vm_id, "not-a-uuid"),
    )
    check(
        "CommitId rejects a non-UUID string at construction",
        lambda: _expect_raise(ValueError, commit_id, "definitely not"),
    )
    check(
        "env_key('PATH') round-trips",
        lambda: env_key("PATH") == "PATH" and isinstance(env_key("PATH"), EnvVarKey),
    )

    print("\n=== RepoRef parsing ===")
    check(
        "RepoRef.parse('bases:python-v1') yields repo='bases', tag='python-v1'",
        lambda: (
            RepoRef.parse("bases:python-v1").repo == "bases"
            and RepoRef.parse("bases:python-v1").tag == "python-v1"
        ),
    )
    check(
        "RepoRef.parse('a:b:c') REJECTS (too many colons)",
        lambda: _expect_raise(ValueError, RepoRef.parse, "a:b:c"),
    )
    check(
        "RepoRef.parse('notag') REJECTS (no colon)",
        lambda: _expect_raise(ValueError, RepoRef.parse, "notag"),
    )
    check(
        "RepoRef str round-trip preserves form",
        lambda: str(RepoRef.parse("foo:v1")) == "foo:v1",
    )

    print("\n=== client construction & guards (NO NETWORK) ===")
    fake_key = NULL_UUID + "-fake-key-not-real-do-not-use"
    c = Client(api_key=fake_key)

    check(
        "owner_id derived from api_key's leading uuid",
        lambda: c.owner_id == NULL_UUID,
    )
    check(
        "Client.close() exists and is callable",
        lambda: c.close(),
    )

    # The dispatch guards must fire CLIENT-side, before any HTTP call.
    # If these network-out, the offline smoke test will hang; the test runner
    # would catch that via timeout in CI.
    check(
        "from_commit() with no args raises ValueError before network",
        lambda: _expect_raise(ValueError, c.from_commit),
    )
    check(
        "from_commit(commit=, ref=) with both raises ValueError before network",
        lambda: _expect_raise(
            ValueError,
            c.from_commit,
            commit=commit_id(NULL_UUID),
            ref=RepoRef.parse("a:b"),
        ),
    )
    check(
        "branch_from(raw str) raises TypeError before network",
        lambda: _expect_raise(TypeError, c.branch_from, "just-a-string"),
    )
    check(
        "branch_from(int) raises TypeError before network",
        lambda: _expect_raise(TypeError, c.branch_from, 42),
    )

    print("\n=== exception hierarchy ===")
    check(
        "all API errors descend from VersError",
        lambda: all(
            issubclass(e, VersError)
            for e in (
                VersAuthError,
                VersForbidden,
                VersNotFound,
                VersConflict,
                VersBadRequest,
                VersValidationError,
                VersHybridBranchError,
                VersServerError,
            )
        ),
    )
    check(
        "VersConfigError is a separate construction-time error, NOT a VersError subclass",
        lambda: (
            issubclass(VersConfigError, Exception)
            and not issubclass(VersConfigError, VersError)
        ),
    )

    c.close()  # idempotent

    print("\n=== CLI parser construction & introspection ===")
    from vers import _build_parser, _all_leaf_parsers, _schema_for_leaf
    parser = _build_parser()

    check(
        "parser builds without error",
        lambda: parser is not None,
    )
    check(
        "every leaf subcommand has an _all_leaf_parsers entry",
        lambda: len(_all_leaf_parsers(parser)) >= 20,
    )

    leaves = dict(_all_leaf_parsers(parser))
    check(
        "expected leaves present",
        lambda: all(k in leaves for k in (
            "whoami", "schema",
            "vm list", "vm get", "vm new", "vm exec",
            "vm pause", "vm resume", "vm logs", "vm ssh-key",
            "repo list", "repo get", "repo create", "repo delete",
            "tag list", "tag create",
            "commit", "branch", "from-commit",
            "env list", "env set", "env delete",
            "domain list", "domain create", "domain delete",
        )),
    )
    check(
        "every leaf carries _handler, _json_keys, _required defaults",
        lambda: all(
            p.get_default("_handler") is not None
            and p.get_default("_json_keys") is not None
            and p.get_default("_required") is not None
            for _, p in _all_leaf_parsers(parser)
        ),
    )
    check(
        "schema_for_leaf produces a JSON-shaped dict for vm new",
        lambda: (
            (s := _schema_for_leaf(leaves["vm new"], "vm new"))
            and "properties" in s
            and "mem_mib" in s["properties"]
            and s["properties"]["mem_mib"]["type"] == "integer"
            and s["properties"]["mem_mib"]["flag"] == "--mem-mib"
            and set(["mem_mib", "vcpu", "fs_mib"]).issubset(set(s["required"]))
        ),
    )
    check(
        "schema_for_leaf records exactly_one_of for branch",
        lambda: (
            (s := _schema_for_leaf(leaves["branch"], "branch"))
            and set(s["exactly_one_of"]) == {"ref", "vm_id", "commit_id"}
        ),
    )
    check(
        "schema_for_leaf records pty boolean for vm exec",
        lambda: (
            (s := _schema_for_leaf(leaves["vm exec"], "vm exec"))
            and "pty" in s["properties"]
            and s["properties"]["pty"]["type"] == "boolean"
            and "pty" not in s["required"]
        ),
    )

    print("\n=== --json bulk-input dispatch (no network) ===")
    from vers import _resolve_json, _validate_required, _validate_exactly_one
    import argparse as _argparse

    def _ns(**kw):
        n = _argparse.Namespace()
        for k, v in kw.items():
            setattr(n, k, v)
        return n

    check(
        "_resolve_json with json_args=None is a no-op",
        lambda: (_resolve_json(_ns(json_args=None, _json_keys=["x"])), True)[1],
    )
    check(
        "_resolve_json patches values onto args",
        lambda: (
            (n := _ns(json_args='{"vm_id":"abc"}', _json_keys=["vm_id"], vm_id=None)),
            _resolve_json(n),
            n.vm_id == "abc",
        )[2],
    )
    check(
        "_resolve_json rejects unknown keys with helpful message",
        lambda: _expect_raise(
            ValueError,
            _resolve_json,
            _ns(json_args='{"typo":1}', _json_keys=["vm_id"]),
        ),
    )
    check(
        "_resolve_json rejects non-object JSON",
        lambda: _expect_raise(
            ValueError,
            _resolve_json,
            _ns(json_args='[1,2,3]', _json_keys=[]),
        ),
    )
    check(
        "_resolve_json rejects unparseable JSON",
        lambda: _expect_raise(
            ValueError,
            _resolve_json,
            _ns(json_args='not json at all', _json_keys=[]),
        ),
    )
    check(
        "_validate_required passes when all required args set",
        lambda: (
            _validate_required(_ns(
                cmd="vm", vm_sub="get",
                _required=["vm_id"], vm_id=NULL_UUID,
            )),
            True,
        )[1],
    )
    check(
        "_validate_required raises with leaf path + remediation hint",
        lambda: _expect_raise(
            ValueError,
            _validate_required,
            _ns(cmd="vm", vm_sub="get",
                _required=["vm_id"], vm_id=None),
        ),
    )
    check(
        "_validate_exactly_one passes with exactly one source",
        lambda: (
            _validate_exactly_one(_ns(
                cmd="branch", _exactly_one_of=["ref","vm_id","commit_id"],
                ref="r:t", vm_id=None, commit_id=None,
            )),
            True,
        )[1],
    )
    check(
        "_validate_exactly_one rejects zero sources with remediation",
        lambda: _expect_raise(
            ValueError,
            _validate_exactly_one,
            _ns(cmd="branch", _exactly_one_of=["ref","vm_id","commit_id"],
                ref=None, vm_id=None, commit_id=None),
        ),
    )
    check(
        "_validate_exactly_one rejects multiple sources",
        lambda: _expect_raise(
            ValueError,
            _validate_exactly_one,
            _ns(cmd="branch", _exactly_one_of=["ref","vm_id","commit_id"],
                ref="r:t", vm_id=NULL_UUID, commit_id=None),
        ),
    )
    check(
        "_wrap_for_pty wraps argv through util-linux script",
        lambda: vers._wrap_for_pty(["python3", "-q"])
        == ["script", "-q", "-e", "-c", "python3 -q", "/dev/null"],
    )
    check(
        "_wrap_for_pty rejects empty argv",
        lambda: _expect_raise(ValueError, vers._wrap_for_pty, []),
    )

    print("\n=== CLI argparse-failure → JSON envelope (exit 64) ===")

    def _run_cli(argv: list[str]) -> int:
        """Invoke the CLI in-process. Returns the exit code. Stdout/stderr
        are not captured here; the smoke is interested in the exit code."""
        import io
        import contextlib
        # capture stdout/stderr to keep test output readable
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            return vers.main(argv)

    check(
        "branch with no source flag → exit 64",
        lambda: _run_cli(["branch"]) == 64,
    )
    check(
        "branch with both --vm-id and --commit-id → exit 64",
        lambda: _run_cli(
            ["branch", "--vm-id", NULL_UUID, "--commit-id", NULL_UUID]
        ) == 64,
    )
    check(
        "vm get with --json containing unknown key → exit 64",
        lambda: _run_cli(
            ["vm", "get", "--json", '{"typo_key": "x"}']
        ) == 64,
    )
    check(
        "vm exec with --json '{}' (missing both vm_id and argv) → exit 64",
        lambda: _run_cli(
            ["vm", "exec", "--json", '{}']
        ) == 64,
    )
    check(
        "vm exec with --json pty as string → exit 64",
        lambda: _run_cli(
            ["vm", "exec", "--json",
             '{"vm_id":"%s","argv":["sh","-lc","true"],"pty":"true"}' % NULL_UUID]
        ) == 64,
    )
    check(
        "vm new without explicit dimensions → exit 64",
        lambda: _run_cli(["vm", "new"]) == 64,
    )
    check(
        "VersCliUsageError is a ValueError subclass (catchable as such)",
        lambda: issubclass(VersCliUsageError, ValueError),
    )

    print(f"\n=== summary: {len(_failures)} failure(s) ===")
    for label, msg in _failures:
        print(f"  - {label}: {msg}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
