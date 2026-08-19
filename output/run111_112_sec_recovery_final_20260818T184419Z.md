# Runs 111/112 SEC Recovery — Final Operational Report

Completed 2026-08-18. Evidence comes from live PostgreSQL, worker process inspection, certification reports, and real batched workflows. Secrets are omitted.

## Decision

**SAFE FOR NEXT LARGE CERI RUN**

All runbook gates passed: abandoned work is non-reclaimable, the production worker is live under SEC ACTIVE, the current signature is bootstrapped for the full 193-ticker production universe, warm SEC reuse performs no filing downloads or parsing, the normalization table scans are removed, and both five- and 50-ticker workflows completed.

## Cleanup

| Run | Jobs before | Running cancelled | Queued cancelled | Jobs purged | Active after |
|---|---:|---:|---:|---:|---:|
| 111 | 54 | 2 | 7 | 54 | 0 |
| 112 | 115 | 0 | 114 | 115 | 0 |

- Cancellation used the supported job service. Stale jobs 30803 and 30805 were closed with their execution tokens; processing runs 24077 and 24078 were closed as `CANCELLED` with counters/checkpoints preserved.
- The 169 run-scoped background rows were removed in one scoped transaction after foreign-key inspection. Historical upload runs, ingestion runs, source records, SEC documents, extraction rows, sync states, and signature history were preserved.
- Final verification: no background row, worker ownership, finalizer, capture, change, or alert descendant remains for run 111 or 112.
- The embedded Uvicorn worker is disabled (`JOB_WORKER_ENABLED=false`). The external production worker remains live as `local-worker-1`, PID 1772, consuming `interactive`, `broker`, and `background`.

## SEC configuration

| Item | Before | After |
|---|---|---|
| Effective incremental mode | `OFF` in the dead worker | `ACTIVE` in the live worker |
| Processor signature | `sec-guidance:910cfd73179f55a7` | `sec-guidance:948beb114caa8da9` |
| Readiness policy | `REQUIRE_READY` | `REQUIRE_READY` |
| Certified target CIKs | 5 under the old signature | 193/193 under the current signature |
| SEC requests/sec | 8 | 8 |
| Database revision | 0046 | `0048_sec_guidance_normalization_performance` |

The production worker startup record also reports CERI enabled, batched workflow enabled, provider ingestion enabled, current Git SHA `1127a7b5ba1149e01d8921915a2b7914e82f8781`, and a dirty deployment tree containing this remediation.

## Processor correctness and SEC bootstrap

- The output-affecting guidance extractor revision was versioned from `guidance-regex-v1` to `guidance-regex-visible-text-v2`, producing a new processor signature. Old completion rows remain unchanged and cannot suppress current-signature work.
- Regression tests cover signature changes, old-signature isolation, readiness isolation, and same-signature warm reuse.
- The 193-ticker production bootstrap used the SEC-only certification path; it did not schedule normalization, features, capture, change detection, or alerts.
- Six legacy archive objects (`BELFA`, `COKE`, `FRO`, `ITIC`, `OPY`, `PKE`) returned 404 for `0001.txt`. The client now retries the canonical accession-named archive object. The retry processed 1,860 documents and inserted 28 records, proving these were not valid empty results.
- Missing CIKs were verified and persisted without conflicts: `HIFS` → `0002044671`, `MOG.A` → `0000067887`, and `NBN` → `0000811831`.

## Warm ACTIVE certification

The comparable cold/warm five-ticker gate used `AIZ`, `AMZN`, `CLBT`, `JPM`, and `SLDE`.

| Metric | Cold/current-signature SHADOW | Warm ACTIVE |
|---|---:|---:|
| Documents discovered | 408 | 408 |
| Filing downloads | 408 | 0 |
| Documents skipped | 0 | 408 |
| Parsing/extraction calls | 408 | 0 |
| SEC requests | 413 | 5 |
| Bytes | 324,447,711 | 4,980,013 |
| Elapsed | 228.209 s | 5.242 s |

The all-production-universe warm ACTIVE pass also passed: 193/193 tickers completed, 33,031 documents discovered and skipped, zero filing downloads, zero parsing/extraction calls, 193 submissions-only requests, 35,458,178 bytes, and 619.713 seconds.

## Normalization

| Metric | Before/run 111 | After |
|---|---:|---:|
| 50-ticker SEC normalization elapsed | Job 30805 still running after >7h19m at 14/50 tickers | Run 117: 3.428 s warm job wall; 0.364 s summed processing-run execution |
| Representative new-record workload | Pathological examples: 2,027 rows in 6,628,463 ms; 708 in 2,707,140 ms | 195/195 rows in 3.316 s, rollback-only |
| SQL SELECT count | Not captured before cleanup | 395 for the 195-record profile |
| Full-table guidance loads per record | Yes | 0 |
| Full company/alias loads per record | Yes | 0 |
| Slow repeated statement groups (>=100 ms aggregate) | Not captured | 4 |

The fix uses an indexed, bounded prior-guidance query; one identity snapshot per batch; indexed ID-only existence probes; bounded checkpoints; and the new indexes `(company_id, metric, period_type, effective_at, id)` and `(ingestion_run_id, id)`. PostgreSQL execution time improved from 3.687 ms to 0.143 ms for the prior-guidance sample and from 308.118 ms to 37.195 ms for the 2,574-row source-run scan.

## Controlled workflow validation

### Five tickers — run 113

All 13 jobs completed. Warm SEC discovered/skipped 488 filings with zero downloads and zero failures. SEC provider elapsed 19.081 seconds and SEC normalization elapsed 1.673 seconds. The complete workflow, including capture, change detection, and alert rebuild, completed successfully.

### Fifty tickers — run 117

All 17 jobs completed with no partial, failed, queued, or running work remaining. The total wall time through alert rebuild was 496.372 seconds; the CERI core through finalization was 455.640 seconds.

| Stage | Measured elapsed | Result |
|---|---:|---|
| Provider estimates (2 batches) | 104.716 s | Completed; 0 failures |
| Provider earnings (2 batches) | 62.971 s | Completed; 0 failures |
| Provider catalysts (2 batches) | 47.891 s | Completed; 0 failures |
| Provider SEC guidance (2 batches) | 184.270 s | 7,551 discovered/skipped; 0 downloads; 0 failures |
| Normalize estimates | 1.312 s execution; 301.533 s job wall including barrier deferral | 48 read/normalized; 0 quarantined/failed |
| Normalize earnings | 0.485 s execution; 242.791 s job wall including barrier deferral | 6 read/normalized; 0 quarantined/failed |
| Normalize catalysts | 0.461 s execution; 198.884 s job wall including barrier deferral | 3 read/normalized; 0 quarantined/failed |
| Normalize SEC guidance | 0.364 s execution; 3.428 s job wall | Completed; no new source rows on warm reuse |
| Feature batch | 36.941 s job wall | 50 tickers, 0 failures |
| Finalize + capture | 17.449 s | Completed |
| Change detection + alerts | 23.243 s | Completed |

Two preserved preliminary medium attempts exposed an EODHD share-class boundary for `MOG.A`: the provider requires `MOG-A.US`, while canonical records must retain `MOG.A`. Both request mapping and canonical record identity were corrected with regression coverage before run 117. No partial status was relabeled as success.

## Verification

- `tests/ceri`: 372 passed in 21.18 seconds after the final share-class fix.
- Focused background queue suite: 45 passed.
- PostgreSQL/integration coverage: 26 passed.
- Focused ruff over changed application, test, script, and migration paths: clean.
- `git diff --check`: clean apart from line-ending conversion warnings.
- A whole-repository ruff invocation still reports 24 pre-existing findings in older Alembic revisions; no unrelated historical migration was edited.

## Acceptance checklist

- [x] Runs 111/112 have zero active or reclaimable background jobs.
- [x] Forensic and canonical evidence is preserved.
- [x] Live production worker reports SEC ACTIVE.
- [x] Processor signature reflects current extractor behavior.
- [x] Old-signature state cannot suppress current-signature processing.
- [x] Current-signature readiness is 193/193 for the intended production universe.
- [x] Warm ACTIVE skips unchanged completed filings before filing HTTP and parsing.
- [x] Guidance and identity per-record table scans are removed.
- [x] PostgreSQL plans and indexes are verified.
- [x] Five- and 50-ticker validations pass.
