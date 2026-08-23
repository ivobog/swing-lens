# Technical Scoring v5 TS forensics

## Frozen scope

The analysis uses `V5_BASELINE` (`ad813416d238476c98b2f03c94175396247be7a9c1a6bf5a65ae095018672ae4`) at Git HEAD
`e897625c6ffba4a0eb553e4a58fe132b6032ce6d`. No scoring weight or activation setting was changed.

## Diagnosis

**Is high TS selecting strong-but-late stocks?** Among the observed cohorts, low-extension returned 1.863%/4.698% at 5d/10d (N=13/11), versus high-extension at 0.276%/0.389% (N=446/312).

The result is conditional rather than a clean yes/no: compare extension, RSI, trigger,
Stage and setup cohorts in `output/technical_v5/ts_forensics.csv`. Component rows include
N, 5d/10d mean and median return, MFE/MAE, hit rate, within-run Spearman, top 10%/20%,
run-relative deciles, monotonicity and decision-date-cluster bootstrap intervals.

The campaign still has only 10 independent dates, so this
diagnosis does not authorize a TS weight or architecture change.
