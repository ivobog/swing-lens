# CERI Alert Dedup and Cooldown Audit

## Root cause

The old evidence field copied the global config label `cooldown_scope=event_revision` into every alert, including Opportunity transitions. The persisted event key itself used the change dedup key and was deterministic, so the defect was partly naming and partly architecture: dedup and delivery suppression were presented as one concept, and ticker-wide cooldown could suppress a genuinely new event revision.

## Correct identities

| Alert family | Dedup identity |
|---|---|
| Catalyst | rule ID + canonical event ID + event revision ID |
| Guidance | rule ID + guidance event/revision ID |
| Opportunity | rule ID + company + from/to score snapshot + opportunity change type |
| Risk | rule ID + company + from/to score snapshot + risk change type |

The canonical identity is serialized and SHA-256 hashed into the unique `event_key`. Reprocessing the same change converges on the same alert. A material new catalyst/guidance revision has a new identity and is not swallowed by a ticker-wide cooldown.

## Cooldown

Cooldown is now separate delivery policy: `cooldown_scope=rule_ticker` plus explicit `cooldown_sessions`. It may suppress repeated score-state notifications for the same ticker/rule, but it does not alter dedup identity and does not suppress a new canonical event revision.

Each alert evidence payload records `alert_rule`, `alert_rule_version`, `dedup_identity`, `dedup_identity_type`, `cooldown_scope`, and `cooldown_sessions`. Tests cover idempotent reruns, distinct material event revisions, and type-correct Opportunity/Risk identities.
