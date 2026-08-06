# Performance Baseline

Execution date: 2026-08-06 (Europe/Zurich)

## Machine and Runtime

| Item | Observed value |
| --- | --- |
| Host | `NEWLAPTOP` |
| OS | Windows 10 Home, version 2009, build 19045 |
| CPU | Intel Core i7-7500U @ 2.70 GHz |
| Memory | 17,061,072,896 bytes (approximately 15.9 GiB) |
| Project Python | CPython 3.12.2 |
| PostgreSQL | PostgreSQL 16 Compose server; PostgreSQL 18.3, 64-bit Windows client tools |
| Browser engines | Playwright Chromium 151.0.7922.34; Firefox 153.0 |
| Baseline commit | `de5c78cdb91f4fca98f3c3eaf0cd303583d7dac6` |

These are local observations, not universal latency or capacity guarantees. The plan's PostgreSQL
16 baseline was used through the local Compose service; client utilities were PostgreSQL 18.3.

## Executed Results

| Check | Result | Evidence |
| --- | --- | --- |
| Full non-browser regression with coverage | PASS | 1,098 passed in 188.63 s; 82.9% branch-aware application coverage |
| Final complete regression | PASS | 1,109 passed in 128.36 s, including populated restore |
| Post-live-fix complete regression | PASS | 1,112 passed, 1 skipped in 78.21 s |
| Post-DEF-004 complete regression | PASS | 1,113 passed, 1 skipped in 118.52 s |
| Post-DEF-005 complete regression | PASS | 1,115 passed, 1 skipped in 146.65 s |
| Full default regression after harness | PASS | 1,096 passed in 126.14 s at Phase 1 checkpoint |
| Performance marker lane | PASS | 21 passed, 1,083 deselected in 5.33 s |
| Chromium + Firefox browser smoke | PASS | 6 passed in 39.21 s after repeated-upload coverage |
| 1,000-ticker SLSE identity generation | PASS | Deterministic unique keys; test budget below 1.0 s |
| 500-row CERI export | PASS | Correct row count; test budget below 2.0 s |
| Export budget refusal | PASS | Streamed normal response; structured 413 for byte/row limit |
| Pipeline performance instrumentation | PASS | Deterministic step duration, p50/p95 calculation, and parity normalization |

The current performance tests are deterministic component/contract budgets. They do not yet
constitute a 50/250/1,000-ticker wall-clock benchmark of a fully populated end-to-end pipeline.

## Residual Performance Work

- Run 50, 250, and 1,000-ticker upload, scoring, full-pipeline, rendering, filtering, pagination,
  and export profiles against disposable PostgreSQL with scripted IB latency.
- Capture p50/p95, throughput, query counts, peak RSS, CPU, database growth, and artifact sizes.
- Execute an eight-hour repeated-run soak with worker/process restarts.
- Establish an approved baseline before enforcing the plan's 20% regression threshold.

The CI performance lane is advisory and runs on schedule or manual dispatch. It publishes JUnit
evidence separately from blocking PR checks.
