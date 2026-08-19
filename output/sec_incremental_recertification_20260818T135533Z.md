# SEC Incremental Re-Certification

Generated: `2026-08-18T14:03:22.567714+00:00`
Processor signature: `sec-guidance:948beb114caa8da9`
Tickers: `AIZ, AMZN, CLBT, JPM, SLDE`

| Scenario | Mode | Discovered | Filing downloads | Skipped | Parsing calls | SEC requests | Bytes | Elapsed (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| shadow_first | SHADOW | 408 | 408 | 0 | 408 | 413 | 324447711 | 228.208608 |
| shadow_repeat | SHADOW | 408 | 408 | 0 | 408 | 413 | 324448042 | 233.945824 |
| active_warm | ACTIVE | 408 | 0 | 408 | 0 | 5 | 4980013 | 5.242481 |

## Checks

- [x] shadow_output_parity
- [x] shadow_record_count_parity
- [x] repeat_would_skip_all_discovered
- [x] active_zero_filing_downloads
- [x] active_zero_parsing
- [x] active_zero_extraction_records
- [x] active_skipped_all_discovered
- [x] repeated_bytes_reduction_gt_95pct
- [x] repeated_elapsed_reduction_gt_80pct

Overall: **PASS**

## Processor-boundary review

The certified incremental implementation originated at commit `2d3e4f1`. Review through current commit `1127a7b` found no post-certification changes in `sec/client.py` or filing-selection policy. Commit `305814a` changed `sec/guidance_extractor.py` and `sec/provider.py` in output-affecting ways: visible-HTML filtering, hard-negative filtering, paragraph boundaries, withdrawn-claim behavior, full-year detection, range unit/currency extraction, and provider payload currency/unit fields. Those changes are not byte-for-byte or semantically equivalent to v1.

`GUIDANCE_EXTRACTOR_VERSION` was therefore advanced from `guidance-regex-v1` to `guidance-regex-visible-text-v2`. The parser, evidence-locator, and filing-selection constants were left unchanged because their components did not change. The resulting current signature is `sec-guidance:948beb114caa8da9`; old `sec-guidance:910cfd73179f55a7` rows remain untouched for audit.

## Certification result

- Cold current-signature SHADOW persisted 408 successful extraction rows: 408 downloads/parses, 2,033 records, and no failures.
- Repeated SHADOW reported 408/408 `would_skip` and reproduced the exact output fingerprint `b64e121e30b289f81e8996a48391cbe2abfc3f48a6bc8badc584c7223b9f2d1d`.
- Warm ACTIVE discovered and skipped 408/408 documents before filing HTTP, made five submissions requests, downloaded no filings, called parsing/extraction zero times, and completed in 5.242481 seconds.
