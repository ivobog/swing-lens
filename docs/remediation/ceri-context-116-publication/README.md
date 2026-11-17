# CERI publication integration

The user authorized publication to GitHub `main` on 2026-11-17 and explicitly
limited it to certified CERI work and prerequisite repairs. The unrelated dirty
primary checkout is excluded.

The certified source commit is `f0d986f287d367a3ba90c55dac4853efc82c0215`. The original
[implementation report](../ceri-context-116/IMPLEMENTATION_REPORT.md), 900-test run,
native 447-score certification and sealed delivery record the state at certification
time, before this separately authorized Git publication. Their historical statements
that changes were uncommitted and unpublished refer to that time. Their bytes remain
unchanged; the delivery archive SHA256 is `cab31cfd192487555d7b8a45b5ab42343cca86705de9ab8cb6f4187173020bb3`.

Integration preserves GitHub main `fad3e86129c40214dccf0082b2bd55ab41ece3f8`,
including its reviewed instrument-symbol changes, and the complete prerequisite
release history through `6d6952f`, retaining `7e113f7`. The two merge conflicts were
in the architecture registry and its generated document. Both reviewed inventories
were retained. Exact annotations cover three local set operations (including the
moved source-ID collector) and two existing fenced SEC replay transaction sites.
The architecture check passes with zero exceptions; no mutation authority or
financial eligibility rule was removed.

Application code merged without manual conflict resolution. Among the original
39 certified source files, only `app/services/pipeline_executor.py` differs from
the certified commit: GitHub main's 15 existing lines carrying the lifecycle cutoff
and handling blocked instrument preflight are retained. The other 38 are unchanged
in Git. The 900-test certification remains a statement about its original source;
the additional integration checks apply to this combined tree.

Focused integration validation passed **193 tests, zero failures, errors or skips**
in 295.86 seconds, with 28 dependency/Alembic
warnings. It covers pipeline execution, IB fetch planning/execution, instrument
symbols, session-calendar fixtures, no-order boundaries, architecture, recovery
fences, native CERI identity, SEC identity/succession, actual PostgreSQL CERI
capture/barrier/handoff, uniqueness/replay/downgrade, dedicated change authority,
and market-data readiness. Ruff passes. Logs, JUnit and hashes are retained here.

Publication does not execute a production migration, deployment or recovery.
GitHub Actions results are separate from these local checks.
