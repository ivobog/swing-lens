# Technical Scoring v5 activation gate report

**Verdict: CONTINUE SHADOW**

| Gate | Status | Evidence |
|---|---|---|
| G1 correctness | PASS | PIT reconstruction; mapped-sector/no-ETF defect fixed and tested; one migration head |
| G2 data coverage | FAIL | only 10 dates/35 days; 10d outcomes only in Distribution; sector missing 48.75% |
| G3 ranking value versus v4 | FAIL | raw TCS top selections do not beat v4; TS materially worse |
| G4 danger-state validation | FAIL | matched danger outcomes are not consistently worse; several states absent |
| G5 Entry Quality validation | INSUFFICIENT | promising top-selection/10d signal, mixed 5d behavior and wide uncertainty |
| G6 robustness across slices | FAIL | results depend on short regime/date window; sector/setup heterogeneity |
| G7 out-of-sample validation | INSUFFICIENT | v4 remains ahead of all named v5 candidates on a two-date holdout |
| G8 downstream consumer safety | FAIL | ranking, lifecycle/alerts, Winner Evidence, SLSE contracts block default |

No v5 production-default or scoring-weight setting was changed.
