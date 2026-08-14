# CERI Run 104 Controlled Replay Report

## Certification disposition

**ORIGINAL RUN 104:** immutable, historically affected. Its persisted rows were not updated, deleted, or represented as corrected.

**CONTROLLED REPLAY:** corrected/reproducible certification result. Status: **PASS**.

This replay used only persisted evidence available by the original cutoff. It did not perform provider acquisition and did not write lifecycle changes or alerts.

Replay identifier `run104-revision-lineage-recertification-v1` is retained in the database as an immutable preliminary execution. The reports designate `v2` as the authoritative certification because v2 expanded the original-lineage diagnostic; its corrected score snapshots are calculation-equivalent to v1.

## Replay provenance

- Replay database ID: `3`
- Replay identifier: `run104-revision-lineage-recertification-v2`
- Source run: `104`
- Original cutoff: `2026-08-14T10:35:32.145596+02:00`
- Git SHA: `3704430933cee6c3106bfdceb4cc2409af8a6989`
- Processor signature: `ceri-controlled-replay-v1:sha256:8d7ea2e3857ed5d35c6c74207755aa944097d87e3943fd709a31bd0fab85a69b`
- Configuration: `2026-08-13-run101-remediation-r1` / `b584211dc06332ec95c337f838e3403e3ba74ee67513c7d1206958ee902c2e22`
- Calculation version: `ceri-1.2.0+controlled-replay.run104-revision-lineage-recertification-v2`
- Schema version: `ceri-controlled-replay-schema-v1`
- Opportunity threshold: `60%`
- Opportunity weights: `{"catalysts": 0.15, "guidance": 0.15, "price_response": 0.05, "revision_acceleration": 0.1, "revision_breadth": 0.15, "revision_magnitude": 0.25, "surprise_trend": 0.15}`

## Population and materiality

- Original/replay snapshots: **177 / 177**
- New revision feature rows: **4248**
- Changed selected revision features: **1253**
- Selected features with value changes: **2**
- Atomic lineage-only refreshes: **1251**
- Tickers with changed selected revision features: **143**
- Tickers with Opportunity score changes: **2**
- Mean absolute score delta: **0.001721**
- Median absolute score delta: **0.000000**
- Maximum absolute score delta: **0.178571**
- Posture transitions: **0**

Changed-feature counts by metric: EPS_DILUTED=1253

The 1,253 replay differences are atomic selected-feature identity comparisons: 1,251 retain the same calculated value while refreshing stale or incomplete lineage, and two change calculated values. This is a broader lineage-selection measure than the prior audit's 525 non-reproducing selected-value references across 74 tickers; the historical finding remains unchanged.

### Changed snapshot fields (of 177)

| Field | Changed tickers |
|---|---:|
| Revision magnitude | 1 |
| Revision breadth | 2 |
| Revision acceleration | 1 |
| Surprise | 0 |
| Guidance | 0 |
| Catalysts | 0 |
| Price Response | 0 |
| Opportunity coverage | 0 |
| Opportunity score | 2 |
| Posture | 0 |
| Confidence | 1 |
| Event Risk | 0 |
| Evidence hash | 177 |

The complete 177-snapshot comparison and every changed selected revision feature are in `CERI_RUN104_ORIGINAL_VS_REPLAY.csv`.

## Required ticker traces

### MSGE

| Metric | Period | Window | Old stored % | Old lineage reproduced % | Replay % | Current | Baseline | Mode | Reason |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 30 | 0 | 0 | 104.224866 | 2.3686 | 1.1598 | SAME_PROVIDER_RELATIVE | STALE_VALUE_LINEAGE_PAIRING |
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 90 | 0.900901 | 0.900901 | 100.660793 | 2.3686 | 1.1804 | SAME_PROVIDER_RELATIVE | STALE_VALUE_LINEAGE_PAIRING |
| EPS_DILUTED | CURRENT_QUARTER | 7 | 50.909091 | 0 | 50.909091 | -0.135 | -0.275 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 30 | 50.909091 | 0 | 50.909091 | -0.135 | -0.275 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 90 | 50.909091 | 3.061224 | 50.909091 | -0.135 | -0.275 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 7 | N/A | N/A | N/A | 3.07 | 0 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 30 | N/A | N/A | N/A | 3.07 | 0 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 90 | N/A | N/A | N/A | 3.07 | 0 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |

Run 104 stored `+50.909091%` for the three current-quarter windows while its persisted selected lineage reproduces `0%`, `0%`, and `+3.061224%`. The fixed atomic replay independently reselects the cutoff-eligible current/baseline pair (`-0.135` versus `-0.275`) and that corrected pair reproduces `+50.909091%`. Thus the defect was the historical value/lineage pairing, not proof that the magnitude itself was invalid. The replay writes new feature IDs and does not repair the historical Run 104 rows.

### DHT

| Metric | Period | Window | Old stored % | Old lineage reproduced % | Replay % | Current | Baseline | Mode | Reason |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 7 | 11.817162 | N/A | 11.817162 | 3.6865 | 3.2969 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 30 | 23.687301 | N/A | 23.687301 | 3.6865 | 2.9805 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 90 | 36.093473 | N/A | 36.093473 | 3.6865 | 2.7088 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 7 | 32.840496 | N/A | 32.840496 | 1.0602 | 0.7981 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 30 | 77.379956 | N/A | 77.379956 | 1.0602 | 0.5977 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 90 | 87.149162 | N/A | 87.149162 | 1.0602 | 0.5665 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 7 | 3.260268 | N/A | 3.260268 | 1.8655 | 1.8066 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 30 | 7.034253 | N/A | 7.034253 | 1.8655 | 1.7429 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 90 | 10.084976 | N/A | 10.084976 | 1.8655 | 1.6946 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_QUARTER | 7 | 14.748907 | 14.613181 | 14.748907 | 0.7609 | 0.6631 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_QUARTER | 30 | 17.531665 | 17.392648 | 17.531665 | 0.7609 | 0.6474 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_QUARTER | 90 | 25.623246 | 25.267842 | 25.623246 | 0.7609 | 0.6057 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |

### ASC

| Metric | Period | Window | Old stored % | Old lineage reproduced % | Replay % | Current | Baseline | Mode | Reason |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| EPS_DILUTED | CURRENT_QUARTER | 7 | N/A | N/A | N/A | 0.62 | 0 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 30 | -43.636364 | N/A | -43.636364 | 0.62 | 1.1 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 90 | 5.084746 | N/A | 5.084746 | 0.62 | 0.59 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 7 | -4.093567 | N/A | -4.093567 | 0.82 | 0.855 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 30 | -4.093567 | N/A | -4.093567 | 0.82 | 0.855 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 90 | -20.38835 | N/A | -20.38835 | 0.82 | 1.03 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |

### COP

| Metric | Period | Window | Old stored % | Old lineage reproduced % | Replay % | Current | Baseline | Mode | Reason |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 7 | 7.955936 | N/A | 7.955936 | 10.3194 | 9.5589 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 30 | 10.259424 | N/A | 10.259424 | 10.3194 | 9.3592 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 90 | 8.829175 | N/A | 8.829175 | 10.3194 | 9.4822 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 7 | 7.052575 | N/A | 7.052575 | 2.6002 | 2.4289 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 30 | 9.307214 | N/A | 9.307214 | 2.6002 | 2.3788 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_QUARTER | 90 | 6.815101 | N/A | 6.815101 | 2.6002 | 2.4343 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 7 | 3.715019 | N/A | 3.715019 | 9.3357 | 9.0013 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 30 | 3.863869 | N/A | 3.863869 | 9.3357 | 8.9884 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 90 | 5.782174 | N/A | 5.782174 | 9.3357 | 8.8254 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |

### NVST

| Metric | Period | Window | Old stored % | Old lineage reproduced % | Replay % | Current | Baseline | Mode | Reason |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 7 | 7.057508 | N/A | 7.057508 | 1.5321 | 1.4311 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 30 | 6.982753 | N/A | 6.982753 | 1.5321 | 1.4321 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | CURRENT_FISCAL_YEAR | 90 | 7.177335 | N/A | 7.177335 | 1.5321 | 1.4295 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 7 | 5.694833 | N/A | 5.694833 | 1.6611 | 1.5716 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 30 | 5.587338 | N/A | 5.587338 | 1.6611 | 1.5732 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |
| EPS_DILUTED | NEXT_FISCAL_YEAR | 90 | 5.849742 | N/A | 5.849742 | 1.6611 | 1.5693 | SAME_PROVIDER_RELATIVE | ATOMIC_LINEAGE_REFRESH |

## P2 confidence-policy follow-up (not changed)

estimate_coverage_low remains INFO for 175/177 original tickers but continues to participate in warning-based High-to-Normal Confidence capping. This replay intentionally preserves the existing Confidence behavior; the warning-policy review remains separate.

## Reference-basis conclusion

The controlled replay is suitable to become the reference basis for the next live CERI run only because the hard lineage gate passed. The original Run 104 remains immutable, historically affected evidence and must not be relabeled as corrected.
