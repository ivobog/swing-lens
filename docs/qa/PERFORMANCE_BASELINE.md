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
| M-05 harness commit | `13c87b1` |

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
| Post-M-05 complete regression | PASS | 1,140 passed, 1 skipped, 4 warnings in 151.66 s |
| Full default regression after harness | PASS | 1,096 passed in 126.14 s at Phase 1 checkpoint |
| Performance marker lane | PASS | 21 passed, 1,120 deselected in 2.56 s after M-05 |
| Chromium + Firefox browser smoke | PASS | 6 passed in 39.21 s after repeated-upload coverage |
| 1,000-ticker SLSE identity generation | PASS | Deterministic unique keys; test budget below 1.0 s |
| 500-row CERI export | PASS | Correct row count; test budget below 2.0 s |
| Export budget refusal | PASS | Streamed normal response; structured 413 for byte/row limit |
| Pipeline performance instrumentation | PASS | Deterministic step duration, p50/p95 calculation, and parity normalization |

The current performance tests are deterministic component/contract budgets. They do not yet
constitute a 50/250/1,000-ticker wall-clock benchmark of a fully populated end-to-end pipeline.

## M-05 Full Scale Profile

`uv run python scripts/qa/run_m05_scale.py` migrated a disposable database, inserted 757,512
deterministic bars for 1,002 symbols, and exercised actual upload, fundamental, technical, regime,
combined, sector, five-profile ranking, history, run-detail, and CSV-export paths. Market-data
planning was replaced only with a zero-request cached plan; no IB connection was made.

| Tickers | Pipeline / technical | Pipeline SQL | Pipeline peak RSS | Run detail p95 | Combined export p95 | DB growth | Evidence verdict |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 50 | 39.489 s / 36.873 s | 205 | 177,360,896 B | 987.611 ms | 286.014 ms | 2,236,416 B | exact counts |
| 250 | 187.454 s / 182.932 s | 605 | 214,355,968 B | 960.906 ms | 783.684 ms | 6,971,392 B | exact counts |
| 1,000 | 739.117 s / 724.678 s | 2,106 | 315,260,928 B | 2,837.804 ms | 2,287.843 ms | 27,172,864 B | exact counts |

The 1,000-ticker rendered/export request peak was 563,933,184 bytes. Every route returned 200 and
raw, fundamental, technical, combined, and five-profile ranking counts matched exactly. Pipelines
were `PARTIAL` because the deterministic evidence produced documented warning/low-confidence
states; no row was lost.

Threshold verdict: **FAIL, 7/10 passed**. History p95 passed at every size. DEF-010 remains open S2
because 250-ticker technical scoring missed 60 seconds, and the 1,000-ticker run detail and combined
export missed 1.5 seconds and 2 seconds. This CPU-bound local observation is not a universal capacity
guarantee.

## Restart and Soak Evidence

- Real web/worker/PostgreSQL restart: **PASS in 337.2 s**. A dedicated PostgreSQL 16 container held
  381,024 cached bars. Web restart, two stale-worker recoveries, PostgreSQL stop/start, readiness
  degradation/recovery, and coalescing passed with zero IB requests and exact 250-row evidence.
  Readiness degraded in 3,062.798 ms. DEF-011 fixed the stale-pool readiness delay.
- Two-cycle repeated-run shakedown: **SHAKEDOWN_PASS in 97.5 s**. Pipeline SQL remained 214 per
  cycle; final counts were 100 technical, 100 combined, and 500 rankings; active/stale jobs were
  zero; peak RSS was 191,356,928 bytes; database size grew from 13,670,079 to 30,619,327 bytes.

## Residual Performance Work

- Execute `run_m05_soak.py` for eight actual hours; the short shakedown is not duration evidence.
- Profile and optimize DEF-010 before approving scale targets or a 20% regression threshold.
- Continue to treat the CI performance lane as advisory until an approved stable baseline exists.

The CI performance lane is advisory and runs on schedule or manual dispatch. It publishes JUnit
evidence separately from blocking PR checks.
