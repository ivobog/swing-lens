# Temporal / Preflight Systemic Defect Register — 2026-09-10

Baseline: `c59a6550a744bcb576ce17042f4bd4feb39337d9`

Remediated implementation: `2b392c641d17a36d56a985c26475025fa949a4a9`

Scope: frozen market context, transition preflight, technical reconstruction, lifecycle
canonicalization, CERI PIT evidence, persistence boundaries, retries, and concurrency.

Twelve systemic defects were found and fixed. No blocking defect remains. Production exposure
describes the risk or observed safety-gate outcome; it does not mean protected production data
was changed. Runs 148–152 and CERI snapshot 10929 were read only throughout certification.

## TPI-F001 — Offset-sensitive instant identity

- **Severity:** Critical
- **Invariant:** TPI-INV-001, 002, 005, 028, 030
- **Reproducer:** The latest canary cutoff serialized as
  `2026-09-09T19:55:33.682959+00:00` before persistence and
  `2026-09-09T21:55:33.682959+02:00` after PostgreSQL reload. The instants were equal but the
  candidate/evidence fingerprints differed.
- **Root cause:** Safety hashes consumed presentation-oriented `datetime.isoformat()` output.
- **Affected paths:** transition candidate fingerprints, technical reconstruction, frozen
  context and downstream evidence payloads.
- **Production exposure:** Plan 1 failed closed with `PRECONDITION_CHANGED`; no pipeline or job
  was created and no business state was mutated.
- **Remediation:** Added one aware-only UTC, fixed-microsecond serializer and passed typed
  datetimes into it.
- **Tests:** Exact latest-canary regression, timezone metamorphism, PostgreSQL timezone matrix.
- **Status:** FIXED / CERTIFIED

## TPI-F002 — Fragmented safety-hash serialization

- **Severity:** High
- **Invariant:** TPI-INV-038–043
- **Reproducer:** Inventory found independent uses of `json.dumps(default=str)`, locally sorted
  payloads, and incidental runtime conversion in lifecycle, CERI, technical, Winner, and
  preflight hashing.
- **Root cause:** No repository-wide canonical scalar/container contract.
- **Affected paths:** CERI source/snapshot/revision/price/feature hashes, technical artifact and
  score hashes, lifecycle hashes, Winner temporal manifests, preflight aggregates.
- **Production exposure:** Equivalent semantic values could hash differently across transport,
  ORM, timezone, or collection-order boundaries.
- **Remediation:** Consolidated new safety-critical hashing on `CanonicalEvidenceSerializer`;
  retained the Winner module as a compatibility wrapper over the shared implementation.
- **Tests:** Scalar/container contract, JSON roundtrip, dict/set permutations, cross-domain suite.
- **Status:** FIXED / CERTIFIED

## TPI-F003 — Mutable pointer state hidden by frozen cutoff

- **Severity:** High
- **Invariant:** TPI-INV-029, 044, 048
- **Reproducer:** Advance a session-canonical pointer after preflight but before enqueue. Filtering
  the administrative pointer by `created_at <= cutoff` could make verification observe no
  pointer instead of the current CAS precondition.
- **Root cause:** Mutable concurrency-control state was incorrectly treated as PIT evidence.
- **Affected paths:** transition candidate pointer lookup and enqueue verification.
- **Production exposure:** A stale plan could receive the wrong precondition comparison; the
  surrounding aggregate gate limited exposure but did not express the correct CAS contract.
- **Remediation:** Read the current pointer/admin row without a market cutoff; keep immutable
  snapshot and technical evidence PIT-frozen.
- **Tests:** Current-admin-state regression and pointer-race PostgreSQL tests.
- **Status:** FIXED / CERTIFIED

## TPI-F004 — Naive CERI receipt provenance assumed UTC

- **Severity:** High
- **Invariant:** TPI-INV-003, 014, 015
- **Reproducer:** Supply a source with a naive `retrieved_at` or `ingested_at` value.
- **Root cause:** The PIT helper silently attached UTC to naive database/application values.
- **Affected paths:** `app/services/ceri/pit_eligibility.py`.
- **Production exposure:** Ambiguous receipt provenance could be admitted as if its instant were
  proven.
- **Remediation:** Missing or naive receipt provenance now fails closed; required cutoff values
  must also be aware.
- **Tests:** CERI missing/naive provenance tests and clone replay.
- **Status:** FIXED / CERTIFIED

## TPI-F005 — CERI feature hash retained caller offset

- **Severity:** High
- **Invariant:** TPI-INV-001, 002, 028
- **Reproducer:** Rebuild the same frozen feature request using UTC and Europe/Zurich renderings
  of the cutoff.
- **Root cause:** The rebuild service called `isoformat()` before hashing, erasing datetime type
  semantics before canonicalization.
- **Affected paths:** CERI feature-rebuild input/output evidence.
- **Production exposure:** Cache/evidence identity could vary by caller or restored DB session
  timezone.
- **Remediation:** Pass typed temporal values to the shared serializer and normalize durable
  lineage payloads.
- **Tests:** CERI scoring timezone/order regression and cross-domain CERI suite.
- **Status:** FIXED / CERTIFIED

## TPI-F006 — Candidate bar evidence depended on query order

- **Severity:** Medium
- **Invariant:** TPI-INV-020, 028, 040
- **Reproducer:** Return the identical semantic raw-bar set in a different row order.
- **Root cause:** Candidate evidence hashed database iteration order.
- **Affected paths:** transition candidate evidence fingerprint.
- **Production exposure:** Query-plan changes could falsely stale a plan.
- **Remediation:** Sort the semantic bar evidence before canonical hashing.
- **Tests:** Evidence-query ordering metamorphism and 512 seeded scenarios.
- **Status:** FIXED / CERTIFIED

## TPI-F007 — Durable temporal payloads used presentation strings

- **Severity:** Medium
- **Invariant:** TPI-INV-009–012, 037
- **Reproducer:** Compare job/result/lineage payloads produced with equivalent non-UTC timezone
  renderings.
- **Root cause:** Durable pipeline, technical, lifecycle, and CERI payloads encoded caller-local
  ISO strings.
- **Affected paths:** pipeline jobs/results, technical lineage, lifecycle lineage, CERI batch and
  capture payloads.
- **Production exposure:** Worker restart or replay could receive representationally different
  context metadata.
- **Remediation:** Normalize semantic timestamps to the fixed UTC representation before durable
  serialization.
- **Tests:** Pipeline context roundtrip, restart/idempotency, session temporal suites.
- **Status:** FIXED / CERTIFIED

## TPI-F008 — Idempotency key lacked semantic request binding

- **Severity:** Medium
- **Invariant:** TPI-INV-034
- **Reproducer:** Reuse an existing preflight idempotency key with a different run, ticker set, or
  cutoff instant.
- **Root cause:** The lookup returned the existing plan without validating semantic equivalence.
- **Affected paths:** transition preflight creation.
- **Production exposure:** A caller error could reuse a plan prepared for a different request.
- **Remediation:** Validate run ID, normalized ticker set, and canonical cutoff instant before
  idempotent return; mismatches fail closed.
- **Tests:** PostgreSQL semantic-idempotency conflict test.
- **Status:** FIXED / CERTIFIED

## TPI-F009 — Terminal and mismatch failures were conflated

- **Severity:** Medium
- **Invariant:** TPI-INV-031–035
- **Reproducer:** Enqueue cancelled, expired, consumed, selection-key-changed, or context-mismatched
  plans and compare the emitted codes.
- **Root cause:** Several distinct state/precondition failures collapsed into broad stale or
  consumed errors.
- **Affected paths:** transition preflight verification and consume.
- **Production exposure:** The gate remained conservative, but operators could not determine the
  deterministic remediation path from its error.
- **Remediation:** Added explicit plan-state transitions and codes for `PLAN_CANCELLED`,
  `PLAN_EXPIRED`, `DUPLICATE_ENQUEUE`, `CONTEXT_MISMATCH`, and `SELECTION_KEY_MISMATCH`; preserved
  `PRECONDITION_CHANGED`, `STALE_PREFLIGHT`, and `INVARIANT_VIOLATION` for their exact domains.
- **Tests:** Terminal-state and error-taxonomy PostgreSQL tests.
- **Status:** FIXED / CERTIFIED

## TPI-F010 — Frozen context had no reusable formal fingerprint

- **Severity:** Medium
- **Invariant:** TPI-INV-008–013, 030
- **Reproducer:** The exact canary regression could test component hashes but lacked one canonical
  identity over every reserved context field.
- **Root cause:** Context equality was distributed across field comparisons and downstream hashes.
- **Affected paths:** market calculation context service and preflight tests.
- **Production exposure:** New consumers could omit a semantic field when establishing context
  identity.
- **Remediation:** Added `market_calculation_context_fingerprint` covering the persisted identity
  and all frozen semantic fields through the shared serializer.
- **Tests:** Exact offset regression and three-timezone PostgreSQL roundtrip.
- **Status:** FIXED / CERTIFIED

## TPI-F011 — Rejections lacked forensic mismatch dimensions

- **Severity:** Medium
- **Invariant:** TPI-INV-029 and observability contract
- **Reproducer:** Trigger pointer, evidence, technical, or key mismatch and inspect logs.
- **Root cause:** A single exception message did not expose which contract dimension diverged.
- **Affected paths:** transition preflight verification/consume.
- **Production exposure:** Diagnosis required manual payload comparison, extending canary and
  incident analysis.
- **Remediation:** Added structured, non-sensitive rejection/consume logs containing plan,
  context, run/pipeline, candidate class, selection keys, mismatch category, fingerprints, cutoff,
  and session where applicable.
- **Tests:** Preflight mismatch suite plus Ruff static validation.
- **Status:** FIXED / CERTIFIED

## TPI-F012 — In-process Alembic disabled forensic loggers

- **Severity:** Medium
- **Invariant:** TPI-INV-050 and observability contract
- **Reproducer:** Run an Alembic upgrade programmatically after application service modules have
  been imported, then emit a worker, preflight, lifecycle, or Winner forensic event. The default
  `logging.config.fileConfig` behavior set existing named loggers to `disabled=True`; ordered
  full-suite `caplog` regressions reproduced the silence.
- **Root cause:** `alembic/env.py` did not override `disable_existing_loggers=True`.
- **Affected paths:** In-process schema validation/migration followed by application forensic
  logging; production CLI migrations run in their own process but administrative embedding had the
  same latent risk.
- **Production exposure:** Safety decisions were not changed, but forensic evidence emitted later
  in a shared administrative process could be silently lost.
- **Remediation:** Alembic now calls `fileConfig(..., disable_existing_loggers=False)` so host
  application loggers remain enabled.
- **Tests:** Full non-slow ordered run plus focused worker/supervisor/Winner `caplog` regressions.
- **Status:** FIXED / CERTIFIED

## Mutation-oriented proof map

| Deliberate regression | Expected failing evidence |
|---|---|
| Remove UTC normalization | exact latest regression; timezone metamorphism; PG timezone matrix |
| Restore naive-UTC assumption | CERI missing/naive provenance test |
| Remove cutoff/revision eligibility | CERI and technical PIT suites; clone replay |
| Make map/set ordering unstable | canonical scalar/container and randomized-order tests |
| Select latest current evidence | post-cutoff evidence/revision tests |
| Create C2 at enqueue | reserved-context restart/idempotency and clone replay |
| Skip plan row lock/CAS | concurrent consumer and pointer-race tests |
| Commit pointer and ledger separately | both injected rollback tests |
| Permit duplicate enqueue | concurrent consume and duplicate replay |
| Reuse cancelled/expired plan | deterministic terminal-state tests |

## Adjacent workflow findings

| Workflow | Classification | Finding |
|---|---|---|
| Run Full Pipeline preflight | SAFE | Reuses the authoritative reserved context and canonical payload encoding. |
| IB Gateway readiness | OUT_OF_SCOPE_NON_BLOCKING | Operational readiness before enqueue; not evidence identity or a C1/C2 creator. |
| CERI active preflight | SAFE | Uses frozen context and PIT receipt/revision gates. |
| Winner maturation | SAFE | Shared temporal-manifest compatibility wrapper; PostgreSQL regression passed. |
| Market-data readiness/prewarm | SAFE | Operational preemption only; it does not replace the frozen context. |
| Technical process-pool fallback | SAFE | The current fallback passes the supplied market cutoff to sequential execution. |
