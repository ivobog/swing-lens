# Technical Scoring v5.1 overlay research report

## Executive verdict

**CONTINUE SHADOW**

V4 remains the production ranking baseline. V5 stays in shadow and disabled as the
default. This campaign evaluates only extension/maturity and trigger-state gates or
secondary sorts around v4.

## Frozen provenance

- Observations: 5,848
- Independent dates: 10 (2026-07-17 through 2026-08-21)
- V4 baseline: `4.0.0` / `2be86010063a1d2de93294ddd193c43fecb7c5149192bd4faffe0e5e149f0d73`
- V5 baseline: `5.0.0` / `ad813416d238476c98b2f03c94175396247be7a9c1a6bf5a65ae095018672ae4`
- Candidate version: `5.1.0-research.1`
- Candidate config hash: `20af962b672513fa396da2a3b528968497958561156be292b66c8b949e1e100b`
- Git commit at execution: `6b3ec7b19ecd8cc7d7a47be6d39a817fb444c9ae`
- Migration head: `0053_split_artifact_identity`
- Dataset version: `technical-v5-shadow-forensic-v2`
- Dataset signature: `04b0932e74b33abe663975f952cabe6ba369477f6f72879c726986329355bf4e`

`output/technical_v51/baseline_manifest.json` contains the immutable baseline records,
decision-date list, and input-signature coverage.

## Findings

The extension and trigger reports retain all 1d/3d/5d/10d outcome metrics, MFE, MAE,
hit rate, and decision-date-cluster bootstrap intervals. Candidate results additionally
include coverage, top 10%/20%, within-run rank correlation, paired date deltas, and
turnover versus v4. All mandatory V4/TS/setup/regime interactions and canonical action
transitions are exported.

At TS >= 8, the narrow persisted-percentile replication gives
LOW_EXTENSION_LT_P50 1.863%/4.698% at 5d/10d (N=13/11), versus HIGH_EXTENSION_GE_P80 0.276%/0.389% (N=446/312); the broader multivariate state gives
LOW_EXTENSION -0.071%/1.837% at 5d/10d (N=307/230), versus HIGH_EXTENSION 0.256%/0.121% (N=394/268). This supports an extension/maturity interaction but does not
show that TS inversion is primarily or stably caused by extension. Trigger state
reproduces the sharper clue: AT_TRIGGER -1.120%/1.076% at 5d/10d (N=72/46), versus FRESHLY_TRIGGERED 2.144%/5.064% (N=503/333).

The promotion sample target is 50 independent dates (80+ preferred); current coverage
is 10. Walk-forward plumbing is complete, but the present calibration/validation/
holdout slices are too small for promotion. Historical rows are accepted only with the
two certified reconstruction statuses, and state/rank assignment never reads forward
outcomes.

## Scope boundaries

- TS, SQ, EQ, Leadership, Residual Momentum, Stage, Danger State, and Sector RS remain
  available diagnostics.
- Sector RS is not used by M1/M2/M3.
- Danger labels remain visible; numeric danger caps do not affect primary candidates.
- G8 remains failed. No Winner, lifecycle, alert, SLSE, or ranking-profile migration was
  performed because no new correctness or contract issue was discovered.
- No production setting or scorer configuration was changed.
