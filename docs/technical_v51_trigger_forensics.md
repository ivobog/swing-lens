# Technical v5.1 trigger forensics

## Trigger-state outcomes

| trigger_state | N | N_5d | N_10d | independent_dates | mean_return_1d | mean_return_3d | mean_return_5d | mean_return_10d | mean_MFE_5d | mean_MAE_5d | hit_rate_5d |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| APPROACHING | 528.0000 | 348.0000 | 223.0000 | 8.0000 | 0.1308 | 0.6261 | 1.1412 | 2.5397 | 3.7677 | -2.3271 | 0.6494 |
| AT_TRIGGER | 126.0000 | 72.0000 | 46.0000 | 9.0000 | 0.1304 | -0.3273 | -1.1201 | 1.0763 | 2.8773 | -3.4057 | 0.3611 |
| BEYOND_TRIGGER | 1850.0000 | 1294.0000 | 815.0000 | 9.0000 | 0.2646 | 0.8283 | 1.2885 | 3.1577 | 4.5470 | -2.7522 | 0.5572 |
| EXTENDED_BEYOND_TRIGGER | 866.0000 | 670.0000 | 455.0000 | 10.0000 | 0.0155 | 0.2053 | 0.6138 | 1.4274 | 3.9123 | -3.0779 | 0.5343 |
| FRESHLY_TRIGGERED | 894.0000 | 503.0000 | 333.0000 | 9.0000 | 0.4622 | 1.3953 | 2.1438 | 5.0635 | 5.0472 | -2.4120 | 0.6819 |
| INVALIDATED | 513.0000 | 383.0000 | 304.0000 | 9.0000 | 0.1777 | 0.6434 | 1.3629 | 1.9680 | 3.9206 | -2.4026 | 0.6371 |
| NEAR | 469.0000 | 308.0000 | 169.0000 | 8.0000 | 0.2616 | 0.5450 | 1.0384 | 1.8182 | 3.5848 | -2.2085 | 0.6006 |
| NOT_APPLICABLE | 403.0000 | 276.0000 | 171.0000 | 9.0000 | 0.2179 | 0.9001 | 1.1275 | 1.2741 | 4.2912 | -2.3908 | 0.5978 |
| TOO_FAR_BELOW | 199.0000 | 100.0000 | 71.0000 | 9.0000 | 0.1165 | 0.8687 | 1.2929 | 3.4852 | 4.3445 | -2.5823 | 0.5400 |

## AT_TRIGGER versus FRESHLY_TRIGGERED

| trigger_state | N | N_5d | N_10d | independent_dates | mean_return_1d | mean_return_3d | mean_return_5d | mean_return_10d | mean_MFE_5d | mean_MAE_5d | hit_rate_5d |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AT_TRIGGER | 126.0000 | 72.0000 | 46.0000 | 9.0000 | 0.1304 | -0.3273 | -1.1201 | 1.0763 | 2.8773 | -3.4057 | 0.3611 |
| FRESHLY_TRIGGERED | 894.0000 | 503.0000 | 333.0000 | 9.0000 | 0.4622 | 1.3953 | 2.1438 | 5.0635 | 5.0472 | -2.4120 | 0.6819 |

The observation and diagnostic rows in `output/technical_v51/trigger_forensics.csv`
compare distance ATR, volume confirmation, strong-close ratio, breakout volume, gap
behavior, next-day follow-through, same-day EMA20/SMA50 extension, setup type, Stage,
regime, v4, TS, SQ, and EQ. Numeric diagnostics use fixed within-sample quartile slices
for description only; trigger bands remain frozen.

The aggregate comparison gives AT_TRIGGER -1.120%/1.076% at 5d/10d (N=72/46), versus FRESHLY_TRIGGERED 2.144%/5.064% (N=503/333). Gap-exhaustion behavior is available
for every row; the persisted debug payload did not contain numeric gap-up magnitude, so
that field is retained as explicitly missing instead of being reconstructed from a
later price revision.
