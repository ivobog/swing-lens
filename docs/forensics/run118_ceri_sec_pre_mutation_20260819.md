# Run 118 CERI / SEC Pre-Mutation Forensic Snapshot

Observed at 2026-08-19 17:16 Europe/Zurich. This report was captured before the run-118 recovery or reliability-hardening changes. It records live PostgreSQL state, the checked-out repository, active process state, and persisted pipeline outputs.

## Identity and deployment

| Item | Observed value |
|---|---|
| Upload run | `118` |
| Pipeline run | `110` |
| Full-pipeline background job | `30993` |
| Checked-out Git SHA | `fcf8254d2c80e10ae733b48fe56d17fd3d2f5aaf` |
| Deployed processor signature | `sec-guidance:eed017654682a0c9` |
| Persisted active/certified signature | None; the schema has no processor promotion state |
| Live worker | `local-worker-1`, PID 17832, started 2026-08-19 15:45:30+02:00 |
| Worker startup Git SHA | `52e7e80f98bae5cb957b368e042e7cc3727ddb22` |
| Worker startup signature | `sec-guidance:eed017654682a0c9` |

The live worker is signature-compatible with the checkout but commit-stale. There is no durable active-signature fence to prevent a signature-incompatible worker from claiming work.

## Run and job state

- Upload run 118 is `COMPLETED` and contains exactly 180 raw rows / 180 distinct tickers.
- Pipeline run 110 is `FAILED` at `CERI_PROVIDER_INGEST`.
- Job 30993 is terminal `FAILED`, with `retry_count=4`, `max_retries=3`, and five recorded claims/attempts. It is not queued and cannot replay automatically.
- First incomplete/blocked stage: `CERI_PROVIDER_INGEST`.
- Later stages `CAPTURING_SETUP_SIGNALS`, `EVALUATING_SETUP_LIFECYCLES`, and `CAPTURING_WINNER_PREDICTIONS` remain `PENDING`.
- Existing pipeline control flow has durable per-stage rows but does not safely skip completed stages on retry; it replayed expensive stages and incremented their retry counters.
- A potential recovery checkpoint exists at `CERI_PROVIDER_INGEST`, subject to validating persisted upstream invariants and implementing safe resume semantics.

## Current v3 SEC readiness

Exact computed signature: `sec-guidance:eed017654682a0c9`.

| Classification | Count |
|---|---:|
| `READY` | 0 |
| `SIGNATURE_MISMATCH` | 136 |
| `SYNC_STATE_MISSING` | 30 |
| `CIK_MISSING` | 14 |
| `SEC_NOT_APPLICABLE` | 0 (no explicit applicability model exists) |
| Total | 180 |

The 136 signature-mismatch tickers have v2 bootstrap state for `sec-guidance:948beb114caa8da9`, not v3 state. The database contains 227 v2 sync-state CIKs and no v3 sync-state row.

### CIK missing

`AMP, ANET, ARQT, EDU, EE, HNGE, HRI, JAZZ, MNST, MTRN, PEN, PRLB, SF, TPC`

### CIK present but no sync state under any signature

`AGM, APG, ARMK, CCEP, CF, CHEF, CNR, CNS, CPK, CRDO, ECO, FORM, FTDR, FTI, GMED, IESC, ITT, KTB, LNC, LTH, MAX, MEDP, NVEC, ONTO, SON, STX, TENB, TER, THRM, WTM`

## Durable upstream outputs

| Output | Rows | Distinct tickers |
|---|---:|---:|
| Fundamental scores | 180 | 180 |
| Technical scores | 180 | 180 |
| Combined results | 180 | 180 |
| Ranking results | 900 | 180 |

These cardinalities match the 180-ticker run universe and the expected five ranking profiles. They establish a candidate resume boundary but do not alone prove every value-level invariant.

## CERI partial-work check

- Run-scoped background jobs: 1 (the `FULL_PIPELINE` parent only).
- Run-scoped `CERI_%` background jobs: 0.
- Therefore the failed preflight did not enqueue or partially commit a run-118 CERI provider batch.

## Incident conclusion before mutation

The original 136/180 failure was a correct fail-closed response to universe expansion under v2. The subsequent 0/180 failure was a correct exact-signature response after v3 deployment. The harmful behavior was orchestration: the deterministic prerequisite was represented as a generic runtime exception, discovered after expensive stages, automatically retried, and replayed completed work. No evidence indicates an SEC provider outage or partial CERI corruption.
