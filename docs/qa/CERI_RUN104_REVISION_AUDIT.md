# CERI Run 104 Revision Audit

## Scoring semantics

Raw revision percent is `(current - baseline) / abs(baseline) * 100`, subject to
the existing near-zero and sign/currency/provider gates. Opportunity revision
magnitude uses positive revision values only, averages the available values,
and caps the resulting component at 10. Negative raw revisions are preserved
in detail output but contribute zero to magnitude. There is no winsorization
of stored raw revision percent. Breadth is `(upward - downward) / (upward +
downward)` and is an independent provider field; it is not inferred from the
consensus-level move.

## Required extreme cases

The representative row below is the largest selected EPS move for each ticker.
All providers are EODHD; comparison mode is `SAME_PROVIDER_RELATIVE`. Legacy
rows with null normalized baseline IDs are traced to the persisted provider
`eps_trend_{7,30,90}d` source fields.

| Ticker | Period / window | Current | 7d / 30d / 90d provider baselines | Analysts; up/down | Raw % | Scoring transform | Classification | Status |
|---|---|---:|---|---|---:|---|---|---|
| DBRG | NEXT_QUARTER / 7d, FPE 2026-12-31 | 0.6700 | 0.2800 / 0.2800 / 0.2800 | 1; 1/1 | +139.285714 | positive, component capped after averaging | VALID_LARGE_MOVE | VALID_BY_DESIGN |
| DHT | CURRENT_QUARTER / 90d, FPE 2026-09-30 | 1.0600 | 0.7981 / 0.5977 / 0.5675 | 4; 2/0 | +87.149162 | positive, component capped after averaging | OTHER: value/lineage mismatch elsewhere and on selected rows | BLOCKER |
| MSGE | CURRENT_QUARTER / 7d/30d/90d, FPE 2026-09-30 | -0.4750 | -0.4750 / -0.4750 / -0.4900 | 7; 3/0 | stored +50.909091 each; reproducible 0 / 0 / +3.061224 | positive and capped, but based on stale lineage | NORMALIZATION_BUG | BLOCKER |
| TER | NEXT_QUARTER / 90d, FPE 2026-12-31 | 2.0137 | 1.2933 / 1.2787 / 1.2270 | 15; 3/1 | +64.115729 | positive, component capped after averaging | VALID_LARGE_MOVE | VALID_BY_DESIGN |
| AVT | CURRENT_QUARTER / 90d, FPE 2026-09-30 | 2.6325 | 1.9050 / 1.8875 / 1.8800 | 4; 1/0 | +40.026596 | positive, component capped after averaging | VALID_LARGE_MOVE | VALID_BY_DESIGN |
| FORM | CURRENT_QUARTER / 7d, FPE 2026-09-30 | 0.8629 | 0.6243 / 0.6243 / 0.6243 | 7; 0/0 | +38.218805 | positive, component capped after averaging | VALID_LARGE_MOVE | VALID_BY_DESIGN |

New normalized features include persisted `known_at` and reference timestamps.
Legacy source-rehydrated rows expose null feature-level `known_at`; their
source observation and provider source record remain visible. This is why the
lineage exemption is explicit rather than silently claiming a normalized
baseline row exists.

## Magnitude/Breadth divergence cases

| Ticker | Representative magnitude / breadth | Finding | Status |
|---|---|---|---|
| CNR | CURRENT_QUARTER 7d -39.470118%; +1.00 (1/0) | Provider consensus-level history and analyst direction counts are distinct fields; trace reconciles. | VALID_BY_DESIGN |
| OSCR | CURRENT_QUARTER 30d +10.441176%; -1.00 (0/2) | Valid provider-semantic divergence. | VALID_BY_DESIGN |
| HCI | CURRENT_QUARTER 7d +22.393822%; -1.00 (0/1) | Valid provider-semantic divergence. | VALID_BY_DESIGN |
| ASC | CURRENT_QUARTER 30d -43.636364%; +1.00 (1/0) | The divergence itself is valid, but other selected ASC revision rows fail lineage reproduction. | BLOCKER |
| COP | NEXT_QUARTER 90d +11.943203%; -0.384615 (4/9) | Direction fields can legitimately diverge, but selected legacy values do not reconcile to current lineage. | BLOCKER |
| NVST | CURRENT_QUARTER 7d +4.542807%; -1.00 (0/1) | This row is valid; other selected legacy NVST rows fail lineage reconciliation. | BLOCKER |
| BURL | CURRENT_QUARTER 30d 0.000000%; +1.00 (16/0) | Consensus level was unchanged while provider analyst-direction counts were positive. | VALID_BY_DESIGN |

`REVISION_DIRECTION_DIVERGENCE` was not added as a score gate. The detail trace
already presents magnitude and Breadth together, and rejecting divergent
signals would incorrectly discard legitimate provider semantics. It remains a
reasonable future non-blocking diagnostic.

## Conclusion

No scoring threshold, weight, cap, comparability rule, currency gate, or PIT
rule was changed. The production fix is limited to atomic lineage copying and
diagnostic visibility. Run 104 requires a controlled rebuild before these
historical selected values can be certified.
