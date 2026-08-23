# Technical Scoring v5 EQ forensics

## Direct answer

**Does EQ materially improve timing among otherwise strong stocks?** Among the observed cohorts, EQ < 5 returned 0.264%/0.947% at 5d/10d (N=179/140), versus EQ >= 7 at 0.089%/1.663% (N=344/231).

**Answer:** not materially at 5d; only tentatively at 10d. This does not pass G5.

Risk Control, Execution Quality, Trigger Quality, RR Quality, Stop Geometry, Liquidity
and Stop Validity are evaluated separately in `output/technical_v5/eq_forensics.csv`.
The 1d/3d/5d/10d contract includes return, MFE, MAE, hit rate, defensible
target-before-stop and time-to-MFE/target where the stored path supports it.

This remains promising-but-insufficient evidence, not a promotion finding.

The named `V5_SECTOR_DATA_FIX` sensitivity removes a proven phantom risk penalty from
mapped sectors whose ETF history was absent. It is reported separately from the frozen
baseline and does not establish EQ or v5 superiority.
