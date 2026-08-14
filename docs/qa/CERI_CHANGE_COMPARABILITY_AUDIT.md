# CERI Change Comparability Audit

## Source-of-truth rule

The SRS requires material changes since the prior **completed comparable snapshot**. The original implementation selected a chronologically prior snapshot but persisted no evidence-contract identity. It therefore treated remediation-driven availability changes as market changes.

## Explicit states

Production now classifies every score comparison as exactly one of:

- `COMPARABLE`
- `MODEL_VERSION_TRANSITION`
- `CONFIG_TRANSITION`
- `EVIDENCE_CONTRACT_TRANSITION`
- `NO_PRIOR_COMPARABLE_SNAPSHOT`

Classification order is calculation version, config hash, then evidence-contract version. A non-comparable state records the reference snapshot for forensic context but emits no trader change and cannot generate an alert. New snapshots use `ceri-evidence-contract-v2`; the configuration version is `2026-08-14-changes-alerts-remediation-r1`.

## Run 102 to Run 104 reconstruction

Both runs reported the same visible identity before remediation:

- calculation version `ceri-1.2.0`
- config version `2026-08-13-run101-remediation-r1`
- config hash `b584211dc06332ec95c337f838e3403e3ba74ee67513c7d1206958ee902c2e22`
- hash schema `ceri-canonical-json-v2`

Nevertheless, migration 0043 rehydrated same-provider relative EPS evidence between the runs. Run 102 had 177/177 Unrated; Run 104 had 173 rated and four Unrated. Revision component availability changed as follows:

| Component | Run 102 available | Run 104 available | Selected 7/30/90d evidence |
|---|---:|---:|---|
| Revision magnitude | 0 | 175 | 706 features per window in Run 104 |
| Revision breadth | 0 | 177 | 706 features per window in Run 104 |
| Revision acceleration | 0 | 171 | 706 features per window in Run 104 |
| Surprise trend | 0 | 176 | derived from newly usable lineage |
| Price response | 0 | 138 | newly eligible parent lineage |

This is an **evidence-contract transition**, not a price/estimate revision observed between two comparable market states. Consequently, percentage-point deltas, breadth deltas, and acceleration deltas are not defined for trader change classification. The configured thresholds (`revision_pct_points=2.0`, `acceleration_delta=0.01`) must not be applied across this boundary.

## `BECAME_RATED` wave

All **179** stored rows were remediation/version transitions:

| State | Count |
|---|---:|
| EVIDENCE_CONTRACT_TRANSITION | 173 |
| MODEL_VERSION_TRANSITION | 6 |

The exact Run 102 -> 104 pair contained 66 `BECAME_RATED` rows, all reclassified `EVIDENCE_CONTRACT_TRANSITION`. None is a trader upgrade or alert-eligible event. The root cause was an unversioned evidence-contract implementation change combined with comparison logic that checked only model/config identity.

## Post-migration population

Stored changes remain immutable in count (8,632) and are annotated: 6,324 comparable, 1,298 no-prior, 837 model transitions, and 173 evidence-contract transitions. Default Changes queries exclude non-comparable rows; forensic callers may explicitly include them. Snapshot/change IDs remain under Technical details.
