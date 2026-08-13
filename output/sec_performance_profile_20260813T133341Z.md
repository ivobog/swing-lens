# SwingLens SEC/CERI performance profile — 20260813T133341Z

## Executive summary

The isolated five-ticker first run took **228.27 s**; the identical fresh-provider repeat took **130.72 s**. The repeat issued **413 SEC requests**, downloaded **407 filing documents**, and transferred **310.12 MiB**. All **407** filing downloads repeated the same accession/document downloaded and parsed in Scenario A. The database itself could pre-identify only **112** of them because those had persisted guidance; SwingLens has no durable filing-processing ledger for zero-guidance documents.

**Single recommended next architecture:** Persistent accession/document-level incremental SEC ingestion. The fresh-provider repeat retained 100.0% of first-run filing downloads: all 407 accession/document pairs were downloaded and parsed in both runs, while the database could pre-identify only 112 guidance-producing documents before HTTP. A durable processing ledger (including zero-guidance outcomes) is the largest safe improvement because it removes network, pacing, parsing, heartbeat, and late dedup work without changing SEC selection or extraction semantics.

## Queue cleanup

Dry-run inventory at 2026-08-13T13:19:08.369029+00:00: **167 QUEUED**, **1 RUNNING**, **43 terminal**. The running job was 29749 (`CERI_NORMALIZE_BATCH`, run 97) on `local-worker-1`. The oldest queued age was 17.18 hours.

All 167 queued jobs were cancelled through `request_job_cancel`; job 29749 received cooperative cancellation and the worker completed normal cancellation. No worker was force-stopped and no records were deleted.

Final verification: **QUEUED=0**, **RUNNING=0** for runs 97–100.

| Run | Job type | Status | Count |
|---:|---|---|---:|
| 97 | CERI_FEATURE_BATCH | QUEUED | 4 |
| 97 | CERI_NORMALIZE_BATCH | COMPLETED | 12 |
| 97 | CERI_NORMALIZE_BATCH | QUEUED | 3 |
| 97 | CERI_NORMALIZE_BATCH | RUNNING | 1 |
| 97 | CERI_PROVIDER_INGEST_BATCH | COMPLETED | 23 |
| 97 | CERI_PROVIDER_INGEST_BATCH | PARTIAL | 5 |
| 97 | CERI_RUN_FINALIZE | QUEUED | 1 |
| 97 | FULL_PIPELINE | COMPLETED | 1 |
| 98 | CERI_FEATURE_BATCH | QUEUED | 8 |
| 98 | CERI_NORMALIZE_BATCH | QUEUED | 32 |
| 98 | CERI_PROVIDER_INGEST_BATCH | QUEUED | 64 |
| 98 | CERI_RUN_FINALIZE | QUEUED | 1 |
| 98 | FULL_PIPELINE | COMPLETED | 1 |
| 99 | CERI_FEATURE_BATCH | QUEUED | 4 |
| 99 | CERI_NORMALIZE_BATCH | QUEUED | 16 |
| 99 | CERI_PROVIDER_INGEST_BATCH | QUEUED | 32 |
| 99 | CERI_RUN_FINALIZE | QUEUED | 1 |
| 99 | FULL_PIPELINE | COMPLETED | 1 |
| 100 | FULL_PIPELINE | QUEUED | 1 |

## Runtime configuration

Values were read from the active settings/environment allowlist; no secrets were printed.

| Setting | Actual value |
|---|---:|
| `CERI_ENABLED` | `true` |
| `CERI_PROVIDER_INGEST_ENABLED` | `true` |
| `CERI_LEGACY_PIPELINE_SCHEDULING_ENABLED` | `false` |
| `CERI_BATCHED_WORKFLOW_ENABLED` | `true` |
| `CERI_PROVIDER_BATCH_SIZE` | `25` |
| `CERI_NORMALIZATION_BATCH_SIZE` | `50` |
| `CERI_FEATURE_BATCH_SIZE` | `50` |
| `CERI_BATCH_CHECKPOINT_INTERVAL` | `5` |
| `CERI_BARRIER_RETRY_SECONDS` | `5` |
| `CERI_RUN_CAPTURE_ENABLED` | `true` |
| `CERI_UI_ENABLED` | `true` |
| `CERI_ALERTS_ENABLED` | `true` |
| `CERI_ADMIN_ENABLED` | `true` |
| `CERI_BACKFILL_ENABLED` | `true` |
| `SEC_REQUESTS_PER_SECOND` | `5.0` |
| `SEC_HTTP_TIMEOUT_SECONDS` | `30` |
| `SEC_FORM4_ENABLED` | `true` |

## Five profiling tickers

| Ticker | Selection role |
|---|---|
| AIZ | High historical guidance/filing workload; recent guidance |
| AMZN | Medium/high workload; recent guidance; large representative issuer |
| CLBT | Lower record volume and little recent guidance (latest persisted guidance was in March 2026 before profiling) |
| JPM | Medium workload with a small number of guidance-producing accessions |
| SLDE | Low-volume workload with recent guidance |

The handler sorts tickers, so every run executed the same order: `AIZ, AMZN, CLBT, JPM, SLDE`. Scenario C is the dedicated detailed request/system trace. Scenario B used a new provider/client/workflow identity, matching a new provider job/run rather than reusing Scenario A's process-local HTTP cache.

## Actual execution path and lifecycle findings

`CERI_PROVIDER_INGEST_BATCH` → `execute_provider_ingest_batch_job()` → one shared `CeriIngestionService`/registry/provider per batch → `SecCeriProvider.fetch_guidance()` → `SecEdgarClient` → submissions discovery → synchronous filing downloads → `GuidanceExtractionService` → `CeriSourceRecordService.store_source_record()`.

The batched handler does not populate `CeriIngestionRequest.start` or `.end`. Therefore all selected 8-K, 10-Q, 10-K, 6-K, and 20-F entries in each SEC submissions `recent` array are inspected. The measured oldest/newest candidate dates appear in the ticker table below.

`SEC_FORM4_ENABLED=true` was verified, but Form 4 does not execute inside this guidance job: `SecCeriProvider.fetch_guidance()` selects only 8-K, 10-Q, 10-K, 6-K, and 20-F.

The provider batch stores completed-ticker/results checkpoint metadata after each ticker and performs the configured interval check every five ticker positions. There is no filing-level checkpoint or accession processing state; cancellation cannot be observed while the synchronous provider is still downloading/parsing one ticker.

One `SecCeriProvider` and `SecEdgarClient` are reused across tickers inside a provider batch. The ticker→CIK map and URL response cache are in memory on that client/provider. A new provider job constructs a new service/registry/provider/client, so neither cache survives jobs or pipeline runs. No persistent accession/document cache exists.

Current provider telemetry does not capture SEC detail: `_record_provider_telemetry()` only writes when a client exposes `stats()`, which `SecEdgarClient` does not. The existing request key coalesces only the same workflow identity; a new pipeline workflow gets a new ingestion request key.

## Where the time went

### First run (A)

| Component | Milliseconds | Percent of elapsed |
|---|---:|---:|
| ticker cik resolution ms | 24.1 | 0.0% |
| sec pacing sleep ms | 19,871.0 | 8.7% |
| company ticker map http ms | 312.4 | 0.1% |
| submissions http ms | 1,396.7 | 0.6% |
| filing http download ms | 135,022.3 | 59.1% |
| other http ms | 0.0 | 0.0% |
| retry backoff ms | 0.0 | 0.0% |
| filing discovery provider local ms | 26,686.2 | 11.7% |
| guidance parsing extraction ms | 21,435.8 | 9.4% |
| source record write ms | 0.0 | 0.0% |
| deduplication ms | 4,597.9 | 2.0% |
| db flush commit ms | 5,226.1 | 2.3% |
| ingestion run db ms | 19.1 | 0.0% |
| queue orchestration ms | 13,040.9 | 5.7% |
| other unclassified ms | 641.3 | 0.3% |
| **Total elapsed** | **228,273.8** | **100.0%** |

HTTP/network wait (aggregate view): **136,731.4 ms**. Unclassified time is explicit and includes small Python/handler bookkeeping not captured by the timed boundaries.

### Repeated run (B)

| Component | Milliseconds | Percent of elapsed |
|---|---:|---:|
| ticker cik resolution ms | 68.9 | 0.1% |
| sec pacing sleep ms | 31,134.4 | 23.8% |
| company ticker map http ms | 227.2 | 0.2% |
| submissions http ms | 1,468.7 | 1.1% |
| filing http download ms | 56,188.6 | 43.0% |
| other http ms | 0.0 | 0.0% |
| retry backoff ms | 0.0 | 0.0% |
| filing discovery provider local ms | 13,353.8 | 10.2% |
| guidance parsing extraction ms | 13,082.1 | 10.0% |
| source record write ms | 0.0 | 0.0% |
| deduplication ms | 2,778.6 | 2.1% |
| db flush commit ms | 3,458.1 | 2.6% |
| ingestion run db ms | 8.8 | 0.0% |
| queue orchestration ms | 8,496.8 | 6.5% |
| other unclassified ms | 457.7 | 0.4% |
| **Total elapsed** | **130,723.8** | **100.0%** |

HTTP/network wait (aggregate view): **57,884.5 ms**. Unclassified time is explicit and includes small Python/handler bookkeeping not captured by the timed boundaries.

## First run vs repeated run

| Metric | First run | Repeated run | Difference |
|---|---:|---:|---:|
| Total elapsed ms | 228,273.822 | 130,723.835 | -97,549.987 |
| SEC requests | 413.000 | 413.000 | +0.000 |
| Filing downloads | 407.000 | 407.000 | +0.000 |
| Bytes downloaded | 325,186,982.000 | 325,186,982.000 | +0.000 |
| Pacing sleep ms | 19,870.967 | 31,134.449 | +11,263.482 |
| HTTP wait ms | 136,731.413 | 57,884.495 | -78,846.918 |
| Parsing time ms | 21,435.850 | 13,082.071 | -8,353.779 |
| DB time ms | 13,496.166 | 8,653.926 | -4,842.240 |
| Guidance records | 2,834.000 | 2,834.000 | +0.000 |
| Deduplicated records | 2,834.000 | 2,834.000 | +0.000 |

## SEC request volume

| Metric | First run | Repeated run |
|---|---:|---:|
| total | 413.000 | 413.000 |
| company ticker map | 1.000 | 1.000 |
| submissions | 5.000 | 5.000 |
| filing documents | 407.000 | 407.000 |
| other | 0.000 | 0.000 |
| http 2xx | 413.000 | 413.000 |
| http 403 | 0.000 | 0.000 |
| http 429 | 0.000 | 0.000 |
| http 5xx | 0.000 | 0.000 |
| timeouts | 0.000 | 0.000 |
| retries | 0.000 | 0.000 |
| pacing sleep ms | 19,870.967 | 31,134.449 |
| http wait ms | 136,731.413 | 57,884.495 |
| bytes downloaded | 325,186,982.000 | 325,186,982.000 |

## Per-ticker results

### First run (A)

| Ticker | Total ms | SEC calls | Filings considered | Downloads | Already known | HTTP ms | Pacing ms | Parse ms | DB ms | Guidance | Duplicates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AIZ | 145,761.3 | 214 | 212 | 212 | 72 | 100,457.4 | 11,036.0 | 8,547.6 | 10,116.0 | 1924 | 1924 |
| SLDE | 29,141.8 | 18 | 17 | 17 | 6 | 3,822.4 | 727.5 | 8,933.6 | 626.5 | 41 | 41 |
| AMZN | 23,701.4 | 89 | 88 | 88 | 25 | 12,270.3 | 5,753.6 | 1,717.6 | 1,270.1 | 436 | 436 |
| CLBT | 16,576.0 | 61 | 60 | 60 | 5 | 14,092.3 | 379.2 | 708.9 | 438.4 | 141 | 141 |
| JPM | 13,042.0 | 31 | 30 | 30 | 4 | 6,089.0 | 1,974.6 | 1,528.1 | 1,045.2 | 292 | 292 |

Candidate windows: AIZ 2014-07-23→2026-08-06; AMZN 2020-07-30→2026-07-31; CLBT 2021-08-31→2026-08-13; JPM 2025-09-12→2026-08-06; SLDE 2025-06-20→2026-07-30.

### Repeated run (B)

| Ticker | Total ms | SEC calls | Filings considered | Downloads | Already known | HTTP ms | Pacing ms | Parse ms | DB ms | Guidance | Duplicates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AIZ | 74,387.8 | 214 | 212 | 212 | 72 | 31,004.0 | 15,963.5 | 8,309.8 | 5,930.6 | 1924 | 1924 |
| AMZN | 23,814.0 | 89 | 88 | 88 | 25 | 12,600.7 | 5,619.6 | 1,742.2 | 1,275.7 | 436 | 436 |
| CLBT | 14,272.6 | 61 | 60 | 60 | 5 | 6,068.9 | 6,281.6 | 567.3 | 450.1 | 141 | 141 |
| JPM | 12,052.8 | 31 | 30 | 30 | 4 | 5,138.7 | 2,233.1 | 1,521.6 | 852.9 | 292 | 292 |
| SLDE | 6,140.9 | 18 | 17 | 17 | 6 | 3,072.2 | 1,036.7 | 941.1 | 144.5 | 41 | 41 |

Candidate windows: AIZ 2014-07-23→2026-08-06; AMZN 2020-07-30→2026-07-31; CLBT 2021-08-31→2026-08-13; JPM 2025-09-12→2026-08-06; SLDE 2025-06-20→2026-07-30.

## Dedicated request/system trace (Scenario C)

Scenario C took **202.54 s**, made **413** SEC requests, downloaded **407** filing documents, and transferred **310.12 MiB**. Its 413 chronological request rows are the companion trace CSV.

## Filing-volume detail (repeated run)

| Ticker | Submissions rows | 8-K | 10-Q | 10-K | 6-K | 20-F | Filtered other forms |
|---|---:|---:|---:|---:|---:|---:|---:|
| AIZ | 1000 | 163 | 37 | 12 | 0 | 0 | 788 |
| AMZN | 1002 | 63 | 19 | 6 | 0 | 0 | 914 |
| CLBT | 332 | 0 | 0 | 0 | 55 | 5 | 272 |
| JPM | 25727 | 26 | 3 | 1 | 0 | 0 | 25697 |
| SLDE | 194 | 12 | 4 | 1 | 0 | 0 | 177 |

## Ticker interpretation

- **AIZ:** Dominated the repeat at 74,387.8 ms because it downloaded 212 selected filings spanning 2014-07-23 to 2026-08-06 and emitted 1924 records—all deduplicated.
- **AMZN:** Downloaded 88 filings and emitted 436 duplicate records; network+pacing accounted for 18,220.3 ms.
- **CLBT:** Downloaded 60 filings, but only 5 were database-known guidance documents before HTTP; it represents little recent guidance despite continued document scanning.
- **JPM:** Its submissions array contained 25,727 rows, but form filtering selected 30; those still produced 292 duplicate records.
- **SLDE:** Was the lowest-volume/fastest repeat ticker with 17 downloads and 41 duplicate records. Its slower first-run parse time was not reproduced in B or C and is treated as runtime variance, not a stable bottleneck.

## Slowest parsed filing documents (repeated run)

| Ticker | Accession | Form | Size | Parse ms | Candidate paragraphs | Guidance records |
|---|---|---|---:|---:|---:|---:|
| AIZ | 0001267238-26-000010 | 10-K | 5.31 MiB | 779.3 | 46 | 46 |
| AIZ | 0001267238-25-000008 | 10-K | 4.86 MiB | 470.7 | 46 | 46 |
| JPM | 0001628280-26-008131 | 10-K | 12.33 MiB | 441.1 | 136 | 136 |
| JPM | 0001628280-26-054343 | 10-Q | 10.98 MiB | 414.9 | 49 | 49 |
| AIZ | 0001267238-26-000041 | 10-Q | 2.24 MiB | 369.7 | 8 | 8 |
| JPM | 0001628280-25-048859 | 10-Q | 10.96 MiB | 329.9 | 58 | 58 |
| SLDE | 0001193125-26-083277 | 10-K | 6.60 MiB | 302.8 | 25 | 25 |
| AIZ | 0001628280-19-001767 | 10-K | 8.02 MiB | 297.0 | 89 | 89 |
| AIZ | 0001267238-26-000027 | 10-Q | 1.83 MiB | 284.2 | 8 | 8 |
| JPM | 0001628280-26-029344 | 10-Q | 8.74 MiB | 264.6 | 49 | 49 |

## Repeated filing work

The controlled trace proves **407 of 407** repeat downloads (100.0%) were the same accession/document pairs downloaded in Scenario A, and **407** were parsed again. Their filing request+pacing cost was **87,323.1 ms**, parsing cost was **13,082.1 ms**, and record deduplication cost was **2,778.6 ms**: **103,183.8 ms** (78.9%) of repeat elapsed. Independently, the database could identify **112 of 407** (27.5%) before HTTP because they had previously produced guidance. Their directly measured request+pacing cost was **38,117.6 ms**, parsing cost was **12,468.3 ms**, and record-level deduplication cost was **2,778.6 ms**. The conservative measured repeated-work lower bound is **53,364.5 ms** (40.8% of repeat elapsed).

Record-level deduplication is not filing-level avoidance: it occurs after the document HTTP request, parsing, extraction, cancellation heartbeat, and source-record lookup. `previously_processed` and `document_hash_known` remain `null` in the filing detail because the schema has no such ledger. Thus the known-repeat number above excludes repeated zero-guidance documents and is a lower bound.

## Slowest ticker

`AIZ` was slowest on the repeated run at **74,387.8 ms**. It considered 212 selected filings, downloaded 212, made 214 SEC calls, spent 31,004.0 ms in HTTP, 15,963.5 ms pacing, 8,309.8 ms parsing, and 5,930.6 ms in measured DB work.

## CPU / I/O classification

Classification: **I/O-bound**. Mean profiler-process CPU was 29.6% of one logical core; repeat-run HTTP+pacing+backoff was 89.02 s. Dedicated trace-run mean PostgreSQL working set was 879.33 MiB. See JSON system samples for system CPU, available RAM, worker RSS, and PostgreSQL RSS.

## Root-cause ranking (repeated run)

| Bottleneck | Measured impact | Evidence | Recommended action |
|---|---:|---|---|
| Repeated historical downloads/parsing | 103,183.8 ms (78.9%) | 407 of 407 filing documents were downloaded and parsed in both A and B | Persist accession/document processing state and skip known work before HTTP |
| Filing discovery/provider local | 13,353.8 ms (10.2%) | Measured provider-local remainder after request, pacing, resolution, and parsing | Reprofile after filing-level incremental state reduces candidate processing |
| Queue/orchestration | 8,496.8 ms (6.5%) | Measured cooperative cancellation heartbeat/check overhead | Keep current design unless post-incremental profiling shows material impact |
| Database | 3,466.9 ms (2.7%) | Measured source lookup/write, flush, commit, and run lifecycle time | Only investigate queries if still material after request elimination |
| Necessary metadata HTTP/pacing | 1,695.9 ms (1.3%) | Residual company-map/submissions request time after subtracting repeated filing request/parse work | Reassess after persistent filing-level state removes unnecessary calls |
| Other/unclassified | 457.7 ms (0.4%) | Explicit reconciliation remainder | Refine instrumentation only if material |

## Recommended next architecture

**Persistent accession/document-level incremental SEC ingestion**

The fresh-provider repeat retained 100.0% of first-run filing downloads: all 407 accession/document pairs were downloaded and parsed in both runs, while the database could pre-identify only 112 guidance-producing documents before HTTP. A durable processing ledger (including zero-guidance outcomes) is the largest safe improvement because it removes network, pacing, parsing, heartbeat, and late dedup work without changing SEC selection or extraction semantics.

The safe design target is: sync submissions metadata → discover accessions/documents → consult durable processing state → download/extract only unseen or explicitly stale documents → persist processing outcome (including zero-guidance) → continue normal CERI source-record handling. This task did not implement that change.

## Request trace and detailed filing evidence

The chronological Scenario C request trace is in the companion CSV. It contains only request categories, not raw URLs or secrets. The JSON contains per-document accession, document, form, filing date, known-before-download status, download/parse flags, sizes, timings, and guidance counts.

No 25-ticker validation was run: the five-ticker evidence already showed identical 407-document request volume across fresh-provider A/B runs and was sufficient to determine the scaling mechanism.
