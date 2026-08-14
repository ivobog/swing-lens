# CERI Run 104 Confidence and Warning Audit

## Confidence edge cases

| Ticker | Opportunity / coverage | Confidence score / label / estimate coverage | Posture | Finding | Status |
|---|---|---|---|---|---|
| KTB | 4.238087 / 70% | 8.075 / High / 62.5% | Mixed | High evidence quality with a mixed directional score is allowed. KTB is not High Opportunity. | VALID_BY_DESIGN |
| PKE | 9.196430 / 60% | 5.375 / Low / 25% | Positive | Positive direction with sparse core estimate coverage is allowed. | VALID_BY_DESIGN |
| DBRG | 9.214286 / 70% | 5.875 / Low / 50% | Positive | Positive direction with lower evidence quality/completeness is allowed. | VALID_BY_DESIGN |

Confidence describes evidence completeness and quality, not score direction.
No correlation or label override was introduced. KTB is excluded from the High
Opportunity / Low Risk summary because Opportunity is below 7, not because of
Confidence.

## `estimate_coverage_low`

- Exact condition: usable revision feature slot coverage is below
  `revision.minimum_component_coverage_pct` (60%). The denominator is required
  metrics x configured period types x 7/30/90-day windows.
- Run 104 frequency: 175 of 177 snapshots.
- Confidence effect: coverage is also the `estimate_coverage` confidence
  subscore (coverage / 10, capped at 10) with weight 0.20. Separately, any
  warning caps an otherwise High label to Normal.
- Disposition: **VALID_BY_DESIGN** as an evidence-quality warning. Its high
  frequency is a provider-coverage observation, not proof that the warning is
  wrong. The UI now renders it as INFO and shows warning count, severity, and
  dominant warning instead of the global `Warnings present` label.

## Run 104 warning distribution

| Warning | Count |
|---|---:|
| guidance_rows_rejected | 29 |
| guidance_unavailable | 177 |
| catalysts_unavailable | 177 |
| estimate_coverage_low | 175 |
| price_response_unavailable | 39 |
| analyst_sample_sparse | 40 |
| revision_magnitude_unavailable | 2 |
| revision_acceleration_unavailable | 6 |
| opportunity_component_coverage_insufficient | 4 |
| surprise unavailable | 1 |

Full warning arrays remain available in detail/debug output.

## Price Response visibility

- Available: 138 of 177 tickers.
- Selected parent type: EARNINGS for all 138.
- Opportunity coverage contribution when available: 5 percentage points.
- Component value distribution: 2.5 (1), 3.0 (1), 3.5 (27), 4.0 (3), 4.5
  (14), 5.0 (7), 5.5 (13), 6.0 (3), 6.5 (16), 7.0 (5), 7.5 (23), 8.0
  (4), 8.5 (21).
- Unavailable first causes: `WINDOW_NOT_ELAPSED` 18;
  `PARENT_EVENT_INELIGIBLE` 21.

Available Price Response is rendered as a named Opportunity component in the
ticker detail UI with its value and evidence lineage. Disposition: **FIXED**.
