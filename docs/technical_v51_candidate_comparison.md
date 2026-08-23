# Technical v5.1 candidate comparison

## Frozen candidate architecture

- `M0_V4`: unchanged v4 control.
- `M1A/M1B`: filter EXTREME or EXTENDED+EXTREME; `M1C` uses extension only inside
  fixed 0.5-point v4 bands.
- `M2`: v4 bands, trigger eligibility, then fixed trigger preference.
- `M3`: v4 bands, EXTREME exclusion, then extension and trigger preference.
- `M4`: fixed TS 6.0/6.5/7.0 eligibility gates followed by v4.
- `V5_BASELINE`: immutable frozen comparison control.

No additive overlay score, danger cap tuning, sector RS ranking input, or production
activation is present. Each candidate also has a label-only danger-exclusion
sensitivity, except the frozen v5 control.

## Holdout top 20%

| candidate_id | coverage | candidate_count | independent_dates | mean_return_5d | median_return_5d | mean_return_10d | median_return_10d | mean_MFE_5d | mean_MAE_5d | hit_rate_5d | paired_mean_delta_5d | candidate_turnover_vs_v4 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M0_V4 | 1.0000 | 871.0000 | 2.0000 | 2.3206 | 1.9332 | 2.7632 | 2.1901 | 4.0433 | -2.0807 | 0.7929 | 0.0000 | 0.0000 |
| M1A_V4_FILTER_EXTREME | 0.9610 | 837.0000 | 2.0000 | 2.2787 | 1.9108 | 2.8392 | 2.6286 | 4.0315 | -2.1184 | 0.7868 | -0.0228 | 0.0286 |
| M1B_V4_FILTER_EXTENDED_EXTREME | 0.5901 | 514.0000 | 2.0000 | 1.9711 | 1.9446 | 2.1315 | 2.0158 | 3.5061 | -2.4894 | 0.7889 | -0.4790 | 0.3571 |
| M1C_V4_EXTENSION_SECONDARY | 1.0000 | 871.0000 | 2.0000 | 2.0517 | 1.9089 | 2.9313 | 2.6286 | 4.0174 | -2.2273 | 0.7638 | -0.3371 | 0.2318 |
| M2_V4_TRIGGER | 0.6429 | 560.0000 | 2.0000 | 1.8484 | 1.9108 | 2.7975 | 1.9739 | 3.5606 | -2.0197 | 0.7595 | -0.4185 | 0.6727 |
| M3_V4_EXTENSION_TRIGGER | 0.6395 | 557.0000 | 2.0000 | 1.6079 | 1.7013 | 2.7441 | 1.9739 | 3.5952 | -2.1648 | 0.6986 | -0.5646 | 0.6933 |
| M4_TS_GATE_6.0_V4 | 0.7830 | 682.0000 | 2.0000 | 2.1963 | 1.9513 | 2.5656 | 2.5246 | 3.9668 | -2.2617 | 0.7963 | -0.1891 | 0.2286 |
| M4_TS_GATE_6.5_V4 | 0.6487 | 565.0000 | 2.0000 | 2.4356 | 2.3191 | 2.5702 | 2.7268 | 4.0670 | -2.2796 | 0.8161 | -0.0730 | 0.3786 |
| M4_TS_GATE_7.0_V4 | 0.5293 | 461.0000 | 2.0000 | 2.3854 | 2.3646 | 2.9466 | 2.9674 | 4.1020 | -2.3374 | 0.7945 | -0.1506 | 0.4786 |
| V5_BASELINE | 1.0000 | 871.0000 | 2.0000 | 1.7333 | 1.7044 | 2.0399 | 1.3553 | 3.7967 | -2.3358 | 0.7231 | -0.5061 | 0.6010 |

The time split is chronological and the policies above are config-hashed before any
evaluation. With only 10 independent dates, holdout estimates are descriptive.

**Research verdict: CONTINUE SHADOW**
