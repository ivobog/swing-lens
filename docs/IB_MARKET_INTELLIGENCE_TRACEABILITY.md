# IB Market Intelligence Traceability

This implementation matrix maps the authoritative SRS families to the SDD design and concrete SwingLens components.

| Requirement family | SDD design | Implementation | Verification |
| --- | --- | --- | --- |
| IBMI-FR-001..020 | Shared flags, run/evidence models, budgets, diagnostics | Settings/YAML, semantic models, request items, immutable features, revisions, job keys | Calculation, adapter, security, migration and persistence tests |
| IB1-FR-001..010 | Typed `BID_ASK`, rolling medians and grades | Historical adapter, `calculate_liquidity`, run-detail/overview overlay | Tight/wide/invalid/outlier/stale/determinism tests |
| IB2-FR-001..010 | `FEE_RATE`, tick 236, bounded components | Historical/live adapters, `calculate_short_pressure` | Easy/elevated/rising/tight/unavailable/not-shortable tests |
| IB3-FR-001..010 | Typed HV/IV, IV/HV, optional CERI premium | Volatility calculation and point-in-time CERI hook | IV above/equal/below HV, zero/missing/entitlement/CERI-bound tests |
| IB4-FR-001..010 | Generic ticks 100/101/105, confirmation labels | Live manager and options calculation | Ratios, observed-zero, unavailable and abnormality tests |
| IB5-FR-001..012 | Parameter cache, presets, scan cancellation, candidate merge | Scanner adapter/orchestration/query/export | Adapter cancellation boundary, deterministic merge and migration coverage |
| IB6-FR-001..010 | Immutable bins and derived zones | Histogram adapter, raw bin tables, histogram calculation/detail API | Unimodal/tied peaks/sparse/above/below/determinism tests |
| IB7-FR-001..020 | Separate Flex HTTPS, idempotent fills, FIFO episodes, research linkage | Flex client/parser/import, journal/research matcher/analytics | TEXT/XML, token redaction, partial fills/exits, reversal, idempotent DB tests |
| NFR-001..015 | Bounded local architecture, restart-safe jobs, privacy, explainability | Existing PostgreSQL/worker, bounded budgets, hashes/masking, reason/warning codes | Migration, CERI regression, no-order static test, dedicated suite |

The core OHLCV `PriceBar`, technical scoring, fundamental scoring, and ranking weights are not modified. Intelligence is exposed as independently versioned context; optional future ranking adjustments must remain configured, bounded, and explicit.
