# Technical v5.1 extension forensics

## Scope

The active deterministic threshold set is `balanced_v1`. The fixed
`conservative_v1` set is retained only as a sensitivity check. Both require agreement
from multiple decision-time inputs, except documented Stage/climax overrides. Raw
inputs, state, reasons, threshold-set ID, input signature, and aggregate outcome rows
are persisted in `output/technical_v51/extension_forensics.csv`.

## Extension-state outcomes

| extension_state | N | N_5d | N_10d | independent_dates | mean_return_1d | mean_return_3d | mean_return_5d | mean_return_10d | mean_MFE_5d | mean_MAE_5d | hit_rate_5d |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EXTENDED | 1528.0000 | 1161.0000 | 773.0000 | 9.0000 | 0.2275 | 0.4305 | 0.8292 | 1.7508 | 3.9518 | -2.8501 | 0.5693 |
| EXTREME | 181.0000 | 94.0000 | 65.0000 | 9.0000 | 0.3368 | 0.7226 | 1.3360 | 0.6241 | 3.9332 | -2.4396 | 0.6170 |
| HEALTHY | 1111.0000 | 658.0000 | 419.0000 | 9.0000 | 0.3910 | 1.4072 | 1.7012 | 4.0851 | 4.8155 | -2.2986 | 0.6489 |
| MODERATE | 3028.0000 | 2041.0000 | 1330.0000 | 9.0000 | 0.1609 | 0.6687 | 1.2484 | 2.8498 | 4.2431 | -2.6259 | 0.5762 |

## TS x Extension replication

| threshold_set_id | ts_threshold | extension_group | N | N_5d | N_10d | independent_dates | mean_return_1d | mean_return_3d | mean_return_5d | mean_return_10d | mean_MFE_5d | mean_MAE_5d | hit_rate_5d | mean_return_5d_ci_low | mean_return_5d_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| balanced_v1 | 7.0000 | LOW_EXTENSION | 1872.0000 | 1228.0000 | 811.0000 | 9.0000 | 0.0523 | 0.3859 | 0.7853 | 2.2508 | 3.8602 | -2.9056 | 0.5554 | 0.2850 | 1.2168 |
| balanced_v1 | 7.0000 | HIGH_EXTENSION | 1001.0000 | 754.0000 | 503.0000 | 10.0000 | 0.0482 | 0.1229 | 0.4190 | 0.9594 | 3.5820 | -2.9693 | 0.5318 | -0.4440 | 1.0110 |
| PERSISTED_EXTENSION_PERCENTILE | 7.0000 | LOW_EXTENSION_LT_P50 | 144.0000 | 97.0000 | 69.0000 | 9.0000 | 0.7283 | 1.5221 | 2.2941 | 5.1356 | 5.7135 | -3.0853 | 0.7320 | 0.8959 | 3.4031 |
| PERSISTED_EXTENSION_PERCENTILE | 7.0000 | HIGH_EXTENSION_GE_P80 | 1471.0000 | 1076.0000 | 706.0000 | 10.0000 | 0.0222 | 0.2424 | 0.5423 | 0.9370 | 3.3344 | -2.6126 | 0.5558 | -0.0219 | 1.0642 |
| balanced_v1 | 7.5000 | LOW_EXTENSION | 1169.0000 | 768.0000 | 525.0000 | 9.0000 | 0.0740 | 0.2800 | 0.5575 | 2.0416 | 3.7455 | -3.0560 | 0.5651 | -0.1708 | 1.2682 |
| balanced_v1 | 7.5000 | HIGH_EXTENSION | 803.0000 | 609.0000 | 407.0000 | 10.0000 | -0.0356 | 0.0111 | 0.2840 | 0.5145 | 3.3956 | -3.0788 | 0.5008 | -0.6821 | 0.9792 |
| PERSISTED_EXTENSION_PERCENTILE | 7.5000 | LOW_EXTENSION_LT_P50 | 58.0000 | 39.0000 | 31.0000 | 8.0000 | 1.5199 | 2.5579 | 3.2510 | 6.2134 | 6.4499 | -2.7914 | 0.8718 | 1.4835 | 5.4499 |
| PERSISTED_EXTENSION_PERCENTILE | 7.5000 | HIGH_EXTENSION_GE_P80 | 1112.0000 | 814.0000 | 547.0000 | 10.0000 | 0.0063 | 0.1804 | 0.4057 | 0.8051 | 3.2721 | -2.6844 | 0.5393 | -0.2219 | 0.9610 |
| balanced_v1 | 8.0000 | LOW_EXTENSION | 478.0000 | 307.0000 | 230.0000 | 9.0000 | -0.1764 | -0.4196 | -0.0711 | 1.8371 | 3.3196 | -3.6148 | 0.4984 | -0.8142 | 0.9757 |
| balanced_v1 | 8.0000 | HIGH_EXTENSION | 512.0000 | 394.0000 | 268.0000 | 10.0000 | -0.0014 | -0.0833 | 0.2560 | 0.1210 | 3.3150 | -2.9943 | 0.4797 | -0.5440 | 0.8340 |
| PERSISTED_EXTENSION_PERCENTILE | 8.0000 | LOW_EXTENSION_LT_P50 | 22.0000 | 13.0000 | 11.0000 | 6.0000 | 1.5413 | 2.4377 | 1.8630 | 4.6980 | 6.4021 | -1.3461 | 1.0000 | 0.8748 | 3.7755 |
| PERSISTED_EXTENSION_PERCENTILE | 8.0000 | HIGH_EXTENSION_GE_P80 | 600.0000 | 446.0000 | 312.0000 | 10.0000 | -0.0007 | -0.0941 | 0.2762 | 0.3892 | 3.1937 | -2.8694 | 0.5000 | -0.3283 | 0.8257 |

The sample has only 10 independent dates. These cohorts diagnose whether TS
inversion is concentrated in mature entries; they do not authorize a production
weight change or a claim of stable causality.

The original percentile definition gives LOW_EXTENSION_LT_P50 1.863%/4.698% at 5d/10d (N=13/11), versus HIGH_EXTENSION_GE_P80 0.276%/0.389% (N=446/312). The broader
multivariate state gives LOW_EXTENSION -0.071%/1.837% at 5d/10d (N=307/230), versus HIGH_EXTENSION 0.256%/0.121% (N=394/268). Therefore extension plausibly
explains part of TS inversion, but the finding is definition-sensitive and is not yet
a validated primary explanation.
