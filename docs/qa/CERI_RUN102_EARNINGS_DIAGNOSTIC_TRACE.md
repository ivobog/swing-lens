# CERI Run 102 Historical Earnings / Surprise Trace

## Run 102 live observation

NVDA has historical-looking `ceri_earnings_actuals` rows, but their report dates
equal fiscal period dates and `actual_value`, provider consensus, and provider
surprise are all null. They are therefore not Surprise evidence. The page’s
“Earnings: 0d · available” described a fresh source, not eligible reported
earnings.

## Root cause

The EODHD calendar’s reported schema uses `report_date`, `actual`, `estimate`,
`percent`, and `before_after_market`. The adapter recognized only alternate
camel-case names such as `reportDate`, `epsActual`, and `epsEstimate`. It then
fell back to the fiscal `date` for report time. Separately, licensed storage
dropped `event_kind`, `acquisition_policy`, and report-time consensus semantics.

## Deterministic positive vertical

| Stage | Evidence/result |
|---|---|
| Provider row | `report_date=2026-08-01`, fiscal `date=2026-06-30`, `actual=1.2`, `estimate=1.0`, `percent=20` |
| Acquisition path | `REPORTED`; separate historical request window |
| Storage projection | retains actual, estimate, surprise, `event_kind`, acquisition policy, and consensus semantics |
| Normalized | actual `1.2`; report-time consensus `1.0`; provider surprise `20`; kind `REPORTED` |
| Eligibility | reported, actual present, PIT-safe report-time consensus |
| Surprise feature | absolute `0.2`; percentage `20`; positive |
| Surprise summary | average `20`; one positive reported event |
| Component | Surprise Trend available |

Zero-valued actual, estimate, and surprise are preserved. A future event with a
null actual remains `UPCOMING` and is excluded from Surprise. An estimate known
after the report cannot be selected as pre-report consensus. The configured
summary continues to select only the latest four eligible reported events.

Upcoming earnings continue to feed Event Risk; reported earnings feed Surprise
Trend. Existing malformed Run 102 rows cannot be promoted because the missing
actual/report-time values were not retained. A fresh targeted acquisition is
required after deployment.
