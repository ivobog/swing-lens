# Phase 2 frozen shadow results

The evaluator used only read-only fixture exports from the authoritative database.
It did not update Runs 32 or 33, append historical decisions, create Winner
predictions, call a provider, or execute a pipeline.

| Frozen run | Fixture fingerprint | Fundamental → Combined / Ranking proposed | Complete warning-bearing Combined prospective readiness | CERI research display if integrity-eligible | CERI change detection | CERI alert/recommendation |
|---|---|---:|---:|---:|---:|---:|
| 32 | `1bf88b6d9235b65a2ddcce5468e592fc37d647f51951f8c29c6d95d1aff00a73` | 171 included; 87 undecided | 0 affected | 258 | 256 | 0 |
| 33 | `554f41a6ae6dc3e68286dbfbf9c034c0a69d5d4d7e63710d95321e109e86b0c1` | 83 included; 35 undecided | 51 become `READY` prospectively | 118 | 116 | 0 |

Fundamental admission remains shadow-only. Missing critical, high, or medium
fields, parse failures, absent component support, invalid field accounting, and
unknown/blocking warnings remain undecided. Low-priority gaps are proposed only
for score-only Combined and Ranking edges when every configured component has
some evidence; Setup and Winner dependencies remain uncertified.

The warning result is prospective only. The 51 Run 33 artifacts retain their
stored historical `DEGRADED` envelopes in the fixture and database.

Among those 51 observations, 29 contain only `INVESTMENT_RISK`, six only
`STRATEGY_RESTRICTION`, and 16 contain both. They are spread across all ten
descriptive score deciles (low to high: 5, 5, 6, 12, 7, 2, 5, 3, 4, 2) and 13
sectors; the largest groups are Electronic technology (9), Technology services
(6), and Health technology, Retail trade, Energy minerals, and Industrial
services (5 each). These deciles are descriptive population bins, not admission
thresholds. None of the 51 has an existing Winner prediction, so no historical
outcome is available for an unbiased calibration comparison. They remain out of
training; manufacturing outcomes or inferring them from later data is forbidden.

Run 33's 116 `Low` and two `Insufficient` CERI observations remain non-actionable.
Low confidence is research-visible and change-detectable; insufficient confidence
is research-visible only. Existing explicit historical exclusions remain effective.

No promoted deployment or full canary is claimed by this artifact.
