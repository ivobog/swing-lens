# SwingLens SEC durable incremental ingestion implementation report

Generated: 2026-08-13

## Outcome

The durable SEC filing-document architecture is implemented and certified for AIZ, AMZN,
CLBT, JPM, and SLDE. The controlled warm ACTIVE pass discovered the same 407 documents but
performed zero filing downloads, zero parsing calls, and zero guidance extraction calls.

The repository default remains `SEC_DOCUMENT_INCREMENTAL_MODE=OFF`. SHADOW and ACTIVE were
enabled only through process-local certification settings.

## Phase 1: schema and state infrastructure

Files:

- `alembic/versions/20260813_0041_sec_incremental_documents.py`
- `app/models/ceri_tables.py`
- `app/models/__init__.py`
- `app/services/ceri/sec/processor_signature.py`
- `app/services/ceri/sec/state_service.py`
- `app/settings.py`
- `.env.example`

Implemented:

- Filing identity unique on `(cik, accession_number, document_name)`.
- Extraction identity unique on `(document_id, dataset, processor_signature)`.
- Explicit extraction status constraint.
- Transactional conditional claims with worker ID, execution token, heartbeat, lease expiry,
  retry eligibility, and stale-lease recovery.
- Token-fenced completion, retry, cancellation, and heartbeat transitions.
- Narrow SEC guidance processor signature.
- A per-CIK/signature sync certification boundary. This third table is required to prevent a
  cold or partially populated ACTIVE registry from being mistaken for genuinely new filings.

Database validation: Alembic head is `0041_sec_incremental_documents`; all three unique
constraints and supporting indexes were inspected in PostgreSQL.

## Phase 2: SHADOW coordination and atomic completion

Files:

- `app/services/ceri/sec/incremental_ingestion.py`
- `app/services/ceri/sec/provider.py`
- `app/services/ceri/sec/client.py`
- `app/services/ceri/orchestration.py`
- `app/services/ceri/batched_job_handlers.py`

Design:

- Existing SEC discovery, extraction, provider record IDs, payloads, evidence locators, and
  source-record storage remain authoritative.
- The SEC provider now exposes a document boundary without moving persistence into the HTTP
  client.
- A claim is committed before synchronous SEC I/O.
- After download/extraction, source-record writes and `COMPLETED_WITH_RECORDS` or
  `COMPLETED_NO_RECORDS` are committed in one transaction.
- Unknown HTTP, network, parser, extractor, and database failures become retryable; none become
  a negative cache.
- Cooperative cancellation becomes `CANCELLED` and stores no completion outcome.
- SHADOW records `would_skip` but still downloads and extracts.
- SEC request telemetry now includes request categories, status classes, bytes, pacing,
  backoff, retries, timeouts, and HTTP wait.

## Phase 3: SHADOW certification

Tickers: AIZ, AMZN, CLBT, JPM, SLDE.

| Metric | First SHADOW | Repeated SHADOW |
|---|---:|---:|
| Elapsed | 134.365 s | 125.216 s |
| SEC requests | 413 | 412 |
| Submissions requests | 5 | 5 |
| Filing downloads | 407 | 407 |
| Filing parses/extractions | 407 | 407 |
| Downloaded bytes | 325,190,762 | 324,395,135 |
| Guidance records | 2,834 | 2,834 |
| Deduplicated records | 2,834 | 2,834 |
| `would_skip` | 0 | 407 |

Output fingerprints matched exactly:

`d439e730e5a1b1c6e59d4da347e2c03036b109489ce7279be854dd30c8d1b9e5`

The source-record row count was 291,128 before and after certification.

## Phase 4: ACTIVE certification

| Metric | Warm ACTIVE |
|---|---:|
| Elapsed | 3.564 s |
| SEC requests | 5 |
| Submissions requests | 5 |
| Filing downloads | 0 |
| Filing parses/extractions | 0 |
| Documents discovered | 407 |
| Documents skipped before HTTP | 407 |
| Guidance records processed again | 0 |
| Bytes including submissions metadata | 4,981,818 |

Measured improvement versus the first SHADOW pass:

- 100% reduction in filing downloads.
- 100% reduction in filing parsing/extraction.
- 98.468% reduction in total SEC response bytes, including necessary submissions metadata.
- 97.348% reduction in elapsed time.
- 97.274% elapsed reduction versus the original 130.72-second repeated-run baseline.

Durable outcomes after certification:

- `COMPLETED_WITH_RECORDS`: 112 documents.
- `COMPLETED_NO_RECORDS`: 295 documents.
- Five certified CIK/signature sync boundaries.

## Phase 5: incremental sync versus backfill

- Normal ACTIVE sync requires a completed SHADOW/bootstrap certification for the exact CIK,
  dataset, and processor signature.
- A cold ACTIVE request fails before submissions or filing downloads rather than silently
  crawling history.
- SEC historical backfill requires explicit `start` and `end` dates.
- Bounded backfill uses the same filing/extraction state and may reuse completed documents.
- OFF intentionally restores the prior behavior for rollback; SHADOW intentionally performs
  the old work while populating and validating state.

## Phase 6: durable CIK reuse

All certification tickers now have stored CIKs in `ceri_companies`:

| Ticker | CIK |
|---|---|
| AIZ | 0001267238 |
| AMZN | 0001018724 |
| CLBT | 0001854587 |
| JPM | 0000019617 |
| SLDE | 0001886428 |

The first SHADOW pass required one company-ticker-map request; the repeated SHADOW and ACTIVE
passes reused stored CIKs and required none.

## Phase 7: tests and re-profile

New tests:

- `tests/integration/test_sec_incremental_ingestion_postgresql.py`
- `tests/ceri/test_sec_client_telemetry.py`
- additions to `tests/ceri/test_sec_guidance.py`

Commands and results:

- `pytest -q tests/ceri tests/test_migration_remediation.py tests/integration/test_sec_incremental_ingestion_postgresql.py`
  — 230 passed.
- Focused post-heartbeat SEC suite — 10 passed.
- Ruff checks — passed.
- `git diff --check` — passed (line-ending notices only).

Covered: identity and signature uniqueness, zero and nonzero guidance, timeout, 429, 5xx,
network/parser/extractor/database failures, cancellation, pre-commit crash rollback,
post-commit warm skip, stale lease recovery, concurrent claim rejection, version invalidation,
source-record idempotency, and cold-output parity.

## Acceptance criteria

| Criterion | Result |
|---|---|
| Durable CIK/accession/document identity | PASS |
| Version-aware extraction identity | PASS |
| Zero-guidance terminal state | PASS |
| Skip before filing HTTP | PASS |
| PostgreSQL claim/lease fencing | PASS |
| Atomic records plus completion | PASS |
| Conservative failure classification | PASS |
| Safe cancellation | PASS |
| OFF/SHADOW/ACTIVE rollout | PASS |
| Explicit bounded SEC backfill | PASS |
| Exact SHADOW output parity | PASS |
| Warm ACTIVE filing downloads = 0 | PASS |
| Warm ACTIVE parsing/extraction = 0 | PASS |
| Repeated bytes reduction >95% | PASS (98.468%) |
| Repeated elapsed reduction >80% | PASS (97.348%) |

## Remaining risks

- Processor-signature component versions must be bumped intentionally whenever any output-
  affecting parser, extractor, locator, or filing-selection behavior changes.
- A synchronous SEC request cannot heartbeat mid-request. The 900-second document lease is
  deliberately much longer than the bounded HTTP retry window, and is refreshed immediately
  after extraction before atomic persistence.
- ACTIVE still downloads submissions metadata once per ticker. The five requests transferred
  4.75 MiB and took 3.56 seconds in this run; submissions watermarks or conditional refresh
  should be considered only after reviewing this post-incremental baseline.
- Certification used the decisive five-ticker workload, not a larger production cohort.

## Artifacts

- `output/sec_incremental_certification_20260813T143420Z.json`
- `scripts/certify_sec_incremental_ingestion.py`
