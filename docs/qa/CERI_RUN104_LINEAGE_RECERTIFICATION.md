# CERI Run 104 Lineage Recertification

## Hard certification gate

**PASS: every selected revision feature in the corrected replay reproduces from its persisted selected lineage.**

- Original selected revision-feature references: **2118**
- Selected replay revision-feature references: **2109**
- Original immutable state hash before: `75cc005eabf67c6aab721c6b263a1d9230efc6369742e41bbe6780f7e4eb82f9`
- Original immutable state hash after: `75cc005eabf67c6aab721c6b263a1d9230efc6369742e41bbe6780f7e4eb82f9`
- Original cutoff retained: `2026-08-14T10:35:32.145596+02:00`
- Fresh provider acquisition: **none**
- Existing Run 104 updates/deletes: **none**
- New lifecycle rows / alerts: **none / none**

## Invariant results

| Invariant | Result |
|---|---|
| `all_177_snapshots_represented` | **PASS** |
| `no_duplicate_tickers` | **PASS** |
| `coverage_equals_available_weights` | **PASS** |
| `selected_component_lineage_or_aggregate_exemption` | **PASS** |
| `selected_revision_values_reproduce` | **PASS** |
| `missing_not_zero_unchanged` | **PASS** |
| `sec_literal_true_acceptance_unchanged` | **PASS** |
| `opportunity_threshold_60_unchanged` | **PASS** |
| `event_risk_independent` | **PASS** |
| `pit_cutoff_unchanged` | **PASS** |
| `original_run_immutable` | **PASS** |
| `no_lifecycle_rows_written` | **PASS** |
| `no_alerts_written` | **PASS** |
| `config_hash_unchanged` | **PASS** |
| `source_run_provenance` | **PASS** |

The aggregate Surprise component carries an explicit `AGGREGATE_DERIVED_FROM_PERSISTED_EARNINGS_LINEAGE` exemption where it has no direct selected component ID. All other available selected components require lineage.

## Historical finding versus replay result

The prior audit identified 2,118 selected revision-feature references, 525 non-reproducing selected values, and 74 affected tickers in ORIGINAL RUN 104. This recertification does not erase that finding. It creates parallel replay features and snapshots, then requires every selected replay feature to reproduce.

The original 2,118 references resolve to 2,109 unique `company/metric/period-slot/window` identities: nine identities were duplicated once in the historical selection. The replay has one selected feature for each of those 2,109 identities; this accounts for the reference-count difference without an omission.

## P2 retained follow-up

estimate_coverage_low remains INFO for 175/177 original tickers but continues to participate in warning-based High-to-Normal Confidence capping. No confidence-policy behavior was changed in this task.
