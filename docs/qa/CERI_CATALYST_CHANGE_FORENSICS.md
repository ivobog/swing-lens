# CERI Catalyst Change Forensics

## Population audit

All **5,665** stored `NEW_CATALYST` rows were joined to their canonical event revision:

- 4,848 had `issuer_relevance` other than true;
- 817 were issuer-relevant but had null materiality;
- zero met the corrected trader eligibility boundary;
- zero lacked a catalyst-revision reference.

Thus the historical population is raw source-arrival leakage. Rows are retained, but default trader queries exclude them. Forensic queries can use `include_ineligible=true`.

New detection requires an issuer-relevant canonical revision with meaningful materiality or explicit binary eligibility. It distinguishes source arrival, normalization, canonical identity, eligibility, and lifecycle. A first-seen `COMPLETED`, `CANCELLED`, or `RESOLVED` observation is not a resolution change; resolution requires a prior canonical lifecycle revision.

## Named revision traces

The requested numbers are `ceri_catalyst_event_revisions.id` values.

| Ticker / revision | Canonical event | Stored classification | Forensic classification |
|---|---:|---|---|
| AIZ 5518 | 5184 | EARNINGS / earnings; Assurant quarterly dividend; ANNOUNCED; issuer relevant; materiality null | Distinct provider article, but category is wrong and it is an ineligible source-only observation. No alert. |
| AIZ 5519 | 5185 | REGULATORY / regulator; Manhattan Life acquisition; COMPLETED; issuer relevant; materiality null | Distinct real-world acquisition article, not a duplicate of 5518. First-seen completed state is not a resolution; source-only/ineligible. No alert. |
| STX 5539 | 5205 | GUIDANCE / guidance; “AI Stock Nobody…”; ANNOUNCED; materiality null | Distinct generic commentary, not a company catalyst; source-only/ineligible. No alert. |
| STX 5540 | 5206 | EARNINGS / earnings; broad live market coverage; ANNOUNCED; materiality null | Distinct article, generic market coverage; source-only/ineligible. No alert. |
| STX 5541 | 5207 | GUIDANCE / outlook; surging estimate commentary; ANNOUNCED; materiality null | Distinct analyst/editorial update, not issuer guidance; source-only/ineligible. No alert. |
| STX 5542 | 5208 | EARNINGS / earnings; retrospective $1,000 investment article; ANNOUNCED; materiality null | Distinct historical commentary, not a current catalyst; source-only/ineligible. No alert. |

Each row has a different provider record, content hash, idempotency key, subject key, canonical event, and revision. They are therefore not exact or clustered duplicates of one real-world event. The apparent duplication is repeated irrelevant editorial coverage caused by an overly permissive ingestion-to-change boundary.

## Corrected event payload

Accepted events expose category, subtype, subject, announced/effective/expected dates, lifecycle status, direction, materiality, confidence, eligibility reason, canonical event ID, and event revision. The first fields are primary business meaning; IDs/hashes remain technical metadata. Event UI never renders a synthetic `N/A -> N/A` pair.
