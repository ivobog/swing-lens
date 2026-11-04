# MSI remediation implementation review — 2026-11-03

This document records the implementation and static registration review. Measured
test, benchmark and operational receipts are delivered separately in
`output/sector_refresh_remediation_20261103`; this document grants no financial,
golden, release or live-provider certification authority.

## Design and authority

NORMAL supervision adds exactly one `sector_refresh` worker, broker queue,
`<normal-worker-id>-sector` registration and separate metrics endpoint (default
9103). Its durable SQL claim predicate accepts only `IB_FETCH` with exact marker
`msi-sector-refresh-v1`; the general worker excludes only that operation.
Registration locks, activation floors, native leases, tokens, quiescence,
supervisor death fencing and recovery remain authoritative. CERTIFICATION keeps
its existing single worker. Acquisition remains a manual POST action.

Migration 0094 adds the worker profile, singleton historical gate and durable
request aliases. The gate serializes native historical acquisition, including
inline FULL_PIPELINE acquisition/capability probes, cache transport and IBMI
historical transport. Calculations execute outside the slot. The sector client
defaults to 30, distinct from pipeline 21 and health/capability range 22–29.
Custom installations must configure equally distinct IDs and metrics ports.

The shared weighted request ledger uses the stricter configured historical rate
and spacing, persists through handoffs and counts BID_ASK twice. Short independent
SQL transactions close before waiting, pacing or network IO. Cancellation reads
use native control transactions or independent cancellation-only Sessions; retry
metadata commits explicitly before network/backoff. Heartbeats retain leases
without advancing genuine progress. A live or ambiguously inspected process
cannot lose its socket reservation through a TTL/token change: acknowledged
disconnect or proven OS death is required before handoff. Foreign-host ownership
fails closed. An unacknowledged disconnect requires native worker recycling;
operators must not clear the reservation while its process can issue requests.

The frozen reviewed session is checked after slot acquisition and before provider
requests. Expiry preserves the original plan, fails the existing operation and
requires a new manual admission. No status read rebases or resubmits work.
Durable request aliases cover initial and coalesced admissions, including lost
responses and terminal replay. Cancel is CSRF/local-admin/NORMAL_ONLY, scoped to
the exact marker, activation boundary and original native job/fetch identity.

## Page and cached evidence

Queued, broker waiting, fetching, verification and terminal displays expose native
times, counters and safe reasons. A successful unchanged poll does not reset the
no-progress clock. At the configured threshold (default 300 seconds), automatic
polling stops with explicit check-status/cancel actions and retained job identity.
Browser timeouts do not cancel or declare failure of a native acquisition.

Panels retain previous results, source/cutoff and coloured STALE/session lag.
Independent requests have generation/version fences, navigation cleanup and
per-panel retry. Coverage releases shared reload controls while technicals can
continue. A terminal acquisition triggers one native cached reload; coverage and
analytical readiness are verified separately. Mixed cutoffs/bases stay visible.
Saved history retains the explicit authorized capture requirement.

Native current-panel budgets are 60 seconds (90 technical); browser budgets are 65/95
seconds. Saved-history fanout has a 30-second aggregate deadline and 10-second
subreads. SQL statements are bounded at five seconds. Six process-local backend
slots, including one per current panel family and two history slots, prevent an
aborted or hung request from spawning unbounded replacement work. Cooperative
checkpoints reject late results; capacity remains occupied until work exits.

Each panel batches its cohort with immutable Core rows under one receipt/session
cutoff, preserving the canonical revision projection and exact source manifests.
Technical history keeps its existing 756-session contract; charts keep the
existing 127-session envelope. Coverage/rotation retain full stored history to
preserve global preferred-basis semantics and provenance. A linear partition
replaces quadratic row-membership work. Only pure immutable bar fingerprints are
reused across requests (bounded LRU 32,768); all typed evidence values, receipt and
revision fields identify them. Configuration/calendar decisions and analytical
frames are recomputed under their native contracts, never reused by TTL.

## Static review scope and preservation

Writer IDs 084/085 and transaction 32 are additive. All prior registry families,
exceptions, classifications, selectors and approval rules are retained. The
standalone lifecycle probe remains MANUAL_OPERATIONAL_RECOVERY / DENIED; only
its current source digests and review provenance change, not historical audit
identity or execution permission. New source/test associations claim STATIC_INTENT.
The independent MSI Playwright harness is not promoted to the catalog's
A04-specific browser fidelity.

Existing test changes isolate SDK/provider unit doubles from a newly durable
transport dependency, update measured query bounds, use the actual migration
head, supply native status/profile fields, and assert earlier scope expiry with
zero requests. Financial expected values, golden fixtures, source manifests,
authority, cancellation, rollback and publication protections are retained.
Native process, PostgreSQL, browser, golden and lifecycle results must be read
from their execution receipts, not inferred from this review.

## Activation and recovery

Production was intentionally stopped and is left stopped. Do not replay old job
1646; the read-only operational snapshot found it COMPLETED with 26/26 items.
Review the isolated patch, integrate it without overwriting unrelated production
work, then use `pwsh .\swinglens.ps1 start` at a supported safe boundary; the
lifecycle performs guarded migration/start and owns both workers. Inspect
`status -Json` and `diagnose`, sign in to Gateway, and admit a fresh manual
operation. Live concurrency requires an existing safe pipeline acquisition and
later-stage observation; automated doubles are not live certification.

Recovery uses native status/cancel, disconnect/death fencing and supervisor
reconciliation. Rollback first quiesces/stops both owned workers through the
supported lifecycle. Restore the prior application source/configuration; an
additive schema may remain. Downgrading to 0093 is optional only after owned
processes and active operations are conclusively stopped and operational alias/
pacing history is retained for review. Never reset runs, snapshots or prices.

The lineage inventory classifies only the two new control tables as operational.
An exact source-qualified dispatch retains the activation assessment's actual
TechnicalConsumerPolicy.evaluate edge, rather than unrelated same-name lifecycle
writers. Financial writer families, historical source pins and all unresolved
material-call refusal rules remain intact. The new worker-profile column is
deferred during ordinary ORM reads so pre-migration dead-generation retirement
can read schema 0093; native routing accesses it after guarded upgrade. Retirement
uses an exact-identity conditional update of legacy control columns, avoiding an
ORM flush that would make the persistence redactor load the pending new column.


The required comprehensive journey exposed Chromium timeouts capturing an
8,192-pixel clip beyond the viewport of a 55,396-pixel native JSON document.
Large-document evidence now scrolls and captures bounded viewport tiles, records
every document/viewport rectangle and restores scroll position. Navigation
insets keep repeated sticky headers outside interior clips. Normal-size captures
retain their prior path. Complete geometry coverage, native JSON rendering and
actual pixels on both axes are tested separately. No screenshot timeout,
financial assertion, golden expectation or original pipeline handler is relaxed.
These standalone renderer tests do not claim native-worker browser fidelity.

Renderer tests live under the existing native browser fixture scope so combined
suites share one session runtime. Their assertions and semantic AST are unchanged;
only package import grouping changes with the location.


The observed owned-pipeline load measurement took 50.356s for coverage and
77.573s for technicals, with unchanged full payload hashes and one price query
per panel. Current-panel budgets therefore use 60/65s server/browser, technicals
remain 90/95s, and native history remains 45s (browser fanout 30s/subreads 10s).
The clock-controlled deadline test keeps late-result rejection and capacity
release while allowing the measured 50.4s coverage case.

The paired real-writer fixture passed its pipeline/financial oracle, then its
post-pipeline publication probe correctly rejected price receipts newer than its
fixed prior-day 22:00 operation clock. Only that fixture now takes fresh monotonic
operation times after cached prices are flushed, retaining its due-session floor.
Every original assertion is unchanged; production price-knownness guards,
financial expected values, goldens, source pins and approval rules are untouched.


## Corrective follow-up — 2026-11-04

The three requested acceptance areas now pass in the isolated worktree. Full
evidence and limitations are retained in
`output/sector_refresh_followup_20261104/REPORT.md` under the production checkout.
This document updates the earlier measurements; historical receipts stay intact.

The real refresh controller reproduced the unresolved-old-reload, stop/resume,
new-queued-job race. Generation/job tokens now fence every delayed completion
mutation and render, including reload resolution and rejection. The latest
combined JavaScript run passes 47 cases. Independent panels retry only explicit
transient capacity responses within the existing deadline; exact observation
identity preserves keyboard focus across unrelated panel completion.

Pre-fix profiles identified repeated copying of large DataFrame receipt metadata,
with about 1.7 million deepcopy calls. Intermediate transforms now exclude that
metadata and restore the full manifest at the owned output boundary. Technical
rows visited once use direct canonical fingerprints; other panels retain a typed,
bounded 32,768-entry pure-value cache. Caller frames/attributes remain independent.

Eighteen controlled trials compare baseline, exact submitted patch and corrected
candidate, three repetitions per variant in ordinary and native-load scenarios.
All 90 complete response payloads agree within their panel family. Corrected
median cold technical wall time is 12.60s versus 12.06s baseline; concurrent
technical wall is 18.50s versus 22.76s. During actual native worker load it is
27.36s versus 57.53s. Ordinary process CPU is 70.59s versus 79.58s. Corrected RSS
is 273.50 MiB versus 165.68 baseline and 336.23 submitted. Warm CPU/wall ranges
remain broad and the worst corrected loaded read is 52.21s. Longer deadlines
are not used as performance evidence. The controlled comparison does not show
a repeatable material technical slowdown, with the documented memory tradeoff.

Native general and constrained workers completed in the same disposable database.
Durable broker waiting and handoff were observed; actual receipt/revision times
overlap the financial pipeline. The admitted 60-session recent refresh produced
1,560 revisions (240 SPY/QQQ); all 26 series' latest fresh closes changed +7 while
frozen sources, business results and authority relationships matched the control.
Native publication reached PUBLISHED, six groups and one evidence row. Ten rows
per fundamental/technical/combined/CERI/Winner family and 50 rankings were retained.

The native publication path exposed an invalid authority rebind outside its
supporting semantic writer transaction. Required binding now goes through the
existing generation writer; the callback validates exact retained authority/scope.
No writer allowlist, proof rule, formula or owner approval policy was relaxed.
Thirty-five Winner regressions pass. The final one-test retained-receipt suite
passes native generation/completion validators against the SHA-bound actual dumps,
including recomputed statistics, full proofs and refresh-item/revision linkage.
Initial outer test failures from a continuation-local CERI counter and an incorrect
full-backfill count remain recorded; corrected checks use actual certified rows
and the unchanged native planner's admitted window. They are not hidden as passes.

Deterministic external responses, fixture pacing (60/minute, zero spacing) and
synthetic later business sessions for cohort maturity limit this evidence to
controlled native execution. Actual wall-clock intervals establish overlap;
no operation clock was backdated or financial handler replaced. Per-execution
CERI identities and cohort proofs are validated before their context-specific
digests are normalized for comparison. Raw evidence and numerical values remain.

Latest focused history/keyboard (11), lineage (36) and fingerprint/PIT (30) checks
pass. Unchanged passing cases from earlier affected/browser runs are reused with
their original failures and reruns described in the report, not summed into a
new all-pass cumulative count. Exact final patch/file identities and clean apply
round trips are retained in PATCH_METADATA.json beside the report.

All 45 historical seals, 402 financial rows, 541 relationships and 46 original
forensic file hashes are preserved. The final production state at 19:51:07 UTC
is stopped, with no active app jobs/listeners and schema still 0093. No production
migration, live acquisition, activation, commit, push or merge occurred. The patch
is ready for review and controlled activation planning. Live IB concurrency and
production freshness remain NOT VERIFIED until a separately authorized exercise.
