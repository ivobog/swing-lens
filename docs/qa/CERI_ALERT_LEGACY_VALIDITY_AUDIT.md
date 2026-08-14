# CERI Alert Legacy Validity Audit

## Method

Migration 0045 classified every persisted alert by joining the alert rule, underlying change event, comparison state, score snapshots, and current catalyst revision. History was not deleted. Invalid rows received status `INVALIDATED`, an explanation, and a timestamp; acknowledge/dismiss operations are no-ops for invalidated alerts.

## Results

| Classification | Count | Resulting status |
|---|---:|---|
| VALID_CURRENT | 0 | — |
| VALID_HISTORICAL | 0 | — |
| INVALID_LEGACY | 723 | INVALIDATED |
| ORPHANED | 0 | — |
| DUPLICATE | 0 | — |

Cause breakdown:

- 625 `OPPORTUNITY_UPGRADED` alerts were generated with `NO_PRIOR_COMPARABLE_SNAPSHOT`;
- 97 `OPPORTUNITY_UPGRADED` alerts crossed a model-version transition;
- one `RISK_ESCALATED` alert crossed a model-version transition.

All 723 retained valid database lineage: zero missing change events, zero missing rules, and zero missing event keys. Their failure is semantic validity, not referential integrity. None remains actionable or unread by default.

## Ongoing invariant

The database now requires non-null change-event and alert-rule lineage for every alert. Production alert creation rejects non-comparable changes and records rule/version, deterministic dedup identity, status, importance, signal class, created time, and cooldown metadata.
