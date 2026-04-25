# Changelog

## v0.2.6 — 2026-04-25

- Hardened preservation semantics against small-model data loss:
  `branched_vm()` now pauses on exit by default instead of terminating the VM.
- `prepare_image()` now leaves the prep VM paused by default on success or
  failure; it no longer hides VM termination inside cleanup.
- Removed `vm delete` from the LLM-facing Python helper CLI schema. Direct
  `Client.delete_vm()` remains available only for explicit user-authorized VM
  removal.
- Required explicit `mem_mib`, `vcpu`, and `fs_mib` for fresh `new_root` /
  `vm new` creation. The helper no longer has task-dependent resource defaults.
- Replaced the fake-looking `bases:warm-v1` example with `<repo>:<tag>` and
  tightened retry/deletion notes in the empirical error catalog.
- Smoke test updated to assert that `vm new` rejects missing dimensions and
  that the helper CLI no longer advertises `vm delete`.

## 0.2.5 - 2026-04-25

Line audit pass against two named rules:

- **Rule A — patterns may not hardcode task-dependent numbers; examples may.**
  Patterns are generic guidance the LLM pattern-matches to; concrete
  numbers leak as defaults. Examples are illustrative; concrete
  numbers there are fine.
- **Rule B — irreversible operations on information are stated only via
  converse terms.** Preserve, keep, capture — never delete, drop,
  purge, deallocate. The irreversible operation may still happen, but
  always as a separate user-authorized action outside the autonomous
  loop.

### Changed

- `SKILL.md` rewritten end-to-end:
  - Frontmatter description switched from feature-list framing to
    use-case framing; explicit "Loading does not allocate VMs" boundary.
  - Added "Audience and design constraint" section stating the LLM-only
    readership.
  - Added "Standing rules" section codifying Rules A and B.
  - Added task → command lookup table at the top with placeholders.
  - Section 0 collapsed to operating loop + reach-gate, with the loop
    terminating at "preserved + left as-is."
  - Reach-gate language qualitative throughout (no "10 seconds", no
    "30 seconds", no "3 minutes of CPU").
  - Cost/quota section rewritten to "narrate qualitatively; LLMs are
    bad at arithmetic."
  - Operating rule 5 reframed: each VM ends with work product
    preserved; pause is the default end state; termination is a
    separate user-authorized action.
  - State machine diagram dropped the `delete → dead` arc.
  - Cookbook A and B switched to Rust-toolchain examples (rustup +
    cargo) so LLM consumers bias toward typed-language defaults when
    picking in-VM tooling.
  - Cookbook C uses caller-supplied `requested_branch_count`, not a
    hardcoded `count=8`.
  - Cookbook F's `hdresearch/ubuntu:latest` replaced with placeholder
    syntax; explicit instruction not to invent public refs.
  - Cookbook H ("hardened image for untrusted code") removed.
    Security is not a one-liner; cookbook entries imply it could be.
  - Anomaly catalog item 8 keeps the structural claim about IPv6,
    drops the literal IP address.
  - "What NOT to do" section: removed the two SDK-specific don'ts.
  - Supplementary-files list updated; SDK audit reference removed.

- `patterns.md`:
  - Invariant rewritten per Rule B.
  - Patterns 1, 2, 3, 4, 5 terminal steps reframed: preserve work
    product, leave VMs paused.
  - Pattern 8 ("Pause, commit, delete") rewritten as "End-of-session
    retention." The autonomous loop's options are pause / commit /
    commit+pause / publish. VM termination explicitly outside the
    autonomous loop.
  - Anecdata example tag (`bases:rust-buildbox-v1`) replaced with
    placeholder.

- `api-cheatsheet.md`:
  - Workflow patterns subsection cleansed of anecdata: specific
    VM dimensions, commit names, repo names, and counts replaced
    with placeholder syntax.

- `references/error_shapes.md`:
  - Anecdata at top removed (specific date, burner key reference,
    "carter's pre-existing 6 VMs").
  - Specific RFC3339 timestamp example removed; structural claim
    kept.
  - Literal IPv6 address removed; structural IPv6 claim kept.
  - "carter's call" reference replaced with neutral phrasing.

- `onboarding.md`:
  - "10 min to click" comment replaced with qualitative phrasing
    ("polling deadline; tune per environment").

### Fixed

Three real bugs caught during external review (`gpt5.5`) and confirmed
empirically before fix:

- `scripts/smoke_test.py` `check()` was silently passing predicates
  that returned `False` (only `AssertionError` caused failure). Now
  treats `result is False` as failure.
- The smoke test asserted `VersConfigError` was a `VersError` subclass.
  It is not (it is a separate `Exception` subclass for
  construction-time failures). Combined with the above, the bad
  assertion was never failing. Fixed both: API errors are tested as
  VersError-derived; `VersConfigError` is tested as a separate
  Exception subclass.
- The CLI dispatch's `except` clause did not catch `VersConfigError`,
  so missing-API-key invocations leaked a Python traceback to stderr
  instead of a JSON error envelope. Fixed: added to except tuple.

Two helper-side improvements:

- New `VersCliUsageError` class (subclass of `ValueError`) so
  argparse failures convert to JSON envelopes on stderr with exit
  code 64, matching the rest of the CLI's error contract.
- New defensive `sys.modules.pop("vers", None)` in smoke test to
  avoid stale module pickup under runpy reuse.

### Added

- 5 new CLI argparse-failure → JSON envelope tests in `smoke_test.py`
  (branch-no-source, branch-both-sources, vm-get-bad-key,
  vm-exec-empty-json, VersCliUsageError-is-ValueError).

### Removed

- `references/sdk_audit.md` dropped from the shipped distribution.
  The audit findings have a known short half-life (HD Research is
  expected to ship SDK fixes within ~1 week as of packaging date).
  Shipping content with a known short half-life would mislead LLM
  consumers who cannot cheaply verify currency.
- `MERGE_NOTES.md` dropped from the shipped distribution. It was
  operational scaffolding for the merge process, not content for the
  LLM consumer.

### Not done

- No live Vers API calls in this packaging pass. The local environment
  proxies `/api/v1/*` requests with policy-level deny; live verification
  must happen from an unrestricted environment.
- Public OpenAPI freshness was not re-fetched in this packaging pass.

## 0.2.4 - 2026-04-25

Merged distribution candidate from gpt5.5's external review of
`vers_skill_v0_2_3`.

### Added

- v4/source-repo reach gate: keep-local cases, reach-for-Vers cases,
  ambiguous allocation wording.
- Cost/quota/privacy rails.
- First-time auth boundary pointing to `onboarding.md`.
- Supplementary docs: `onboarding.md`, `api-cheatsheet.md`,
  `patterns.md`.
- Merge notes and manifest.

### Kept

- v0.2.3 `scripts/vers.py` helper and all-named-flag / JSON CLI design.
- v0.2.x empirical anomaly catalog and generated SDK audit references.
- v0.2.1 softened two-phase wording.
