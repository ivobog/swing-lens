# CERI Changes and Alerts Requirement Traceability

The prompt referenced `(2)` DOCX filenames; the available byte sources were the matching unsuffixed SRS/SDD files in Downloads. Their paragraphs and tables were structurally extracted completely. LibreOffice was unavailable, so no visual-layout claim is made; semantics were reviewed from the document XML. Controlled Replay v2 remains an immutable lineage certification and was not treated as a normal lifecycle/alert run.

| Requirement | SRS/SDD basis | Implementation/evidence | Verification |
|---|---|---|---|
| Prior completed comparable snapshot | SRS FR-048; point-in-time history | explicit comparison state and evidence-contract version; migration 0045 | forensic comparison tests; comparability audit |
| Material revision/catalyst/guidance/risk feed | SRS FR-049 | central groups; eligible event/guidance gates; semantic DTO | exhaustive mapping and DTO tests |
| No duplicate same event revision | SRS FR-051, AC-13 | typed alert business identities; unique event key | idempotent and material-new-revision tests |
| Previous/current values and related event | SDD `GET /api/ceri/changes` | hydrated score/event/guidance DTO; technical metadata separated | AEIS 2362/2970 regression and event UI regression |
| Immutable point-in-time history | SRS/SDD auditability | additive columns; no deleted change/alert history; invalidation metadata | before/after row counts and legacy audit |
| Missing is not zero | SRS principles | `Unrated`, `Unavailable`, sufficiency labels; null-safe DTO | query and scoring suites |
| Canonical real event, provider as evidence | SRS catalyst semantics | canonical event/revision identity and issuer/materiality eligibility | all 5,665 NEW_CATALYST rows plus AIZ/STX traces |
| Accepted guidance only | SRS guidance evidence | `accepted_for_scoring is True` gate; guidance lineage | change detection tests and pair reconciliation |
| Opportunity and risk independent | SRS scoring principles | separate score/risk payloads; independent risk change logic | 177-company risk reconciliation |
| Importance distinct from signal | remediation requirement | `importance`, `signal_class`, Event Risk unchanged | mapping/dimension tests and migration audit |
| Deterministic Opportunity taxonomy | remediation requirement | config `score_delta=1.0`, upgrade boundary 7.5 | parameterized taxonomy tests |
| Alert rule/version and lineage | alert architecture requirement | rule/version evidence; non-null rule/change constraint | post-migration zero-failure lineage audit |
| Dedup separate from cooldown | remediation requirement | typed identity hash versus rule/ticker delivery window | alert dedup audit and tests |
| Invalid legacy alerts non-actionable | remediation requirement | validity classification, reason/time, `INVALIDATED` | 723-row legacy validity audit |
| Business-first Changes UI | SDD/API semantics | semantic summaries/cards, reference frame, filters | route/UI tests |
| Business-first Alerts UI | remediation requirement | required columns, technical details, invalid actions removed | route/UI tests |
| Controlled Replay v2 immutability | Run 104 recertification artifacts | existing replay service/artifacts preserved; new contract fields do not alter evidence hash | controlled replay tests in final 353-test suite |

## Artifact index

- `CERI_CHANGES_ALERTS_BASELINE.md`
- `CERI_CHANGE_COMPARABILITY_AUDIT.md`
- `CERI_CHANGE_GROUP_CLASSIFICATION_AUDIT.md`
- `CERI_CATALYST_CHANGE_FORENSICS.md`
- `CERI_ALERT_LEGACY_VALIDITY_AUDIT.md`
- `CERI_ALERT_DEDUP_AUDIT.md`
- `CERI_CHANGES_ALERTS_UI_SEMANTICS.md`
- `CERI_CHANGES_ALERTS_CERTIFICATION.md`
- this traceability matrix
