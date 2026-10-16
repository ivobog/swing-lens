# Phase 2 readiness and eligibility traceability

| Requirement | Policy | Producer | Consumer | Code / schema | Migration | Tests | Certification |
|---|---|---|---|---|---|---|---|
| A-01 Orthogonal dimensions | P1–P5 | All | All | `eligibility_policy.py` | 0092 | `test_eligibility_policy.py` | Gate A |
| A-02 Unknown warnings fail closed | P3 | All | All | `warning_taxonomy.py` | — | `test_warning_taxonomy.py` | Gate A |
| A-03 Legacy envelope compatibility | P1–P3 | All | Existing edges | producer/contextual eligibility | — | readiness regression suites | Gate A |
| B-01 Append-only consumer decisions | P1–P5 | All | Material edges | `consumer_eligibility_decisions` | 0092 | PostgreSQL ledger tests | Gate B |
| B-02 Winner acquisition decisions | P2 | Winner sources | Winner | `winner_acquisition_decisions` | 0092 | capture/idempotency tests | Gate B/D |
| B-03 Non-authoritative shadow decisions | P1/P2/P4 | Frozen source set | Shadow evaluator | `eligibility_shadow_evaluations` | 0092 | shadow fixture tests | Gate B |
| C-01 Preserve score arithmetic | P1 | Fundamental | All | existing ranker unchanged | — | score golden tests | Gate C |
| C-02 Consumer-specific Fundamental contract | P1 | Fundamental | Combined/Ranking/Setup/Winner | `fundamental_admission_policy.py` | — | omission matrix | Gate C |
| C-03 No unjustified threshold activation | P1 | Fundamental | All | shadow-only policy switch | — | Run 32/33 sensitivity | Gate C |
| D-01 Typed warning propagation | P3 | Fundamental/Technical/Combined/Ranking | Contextual edges | warning catalog + readiness | — | taxonomy/readiness tests | Gate D |
| D-02 Risk observations admitted to research | P2/P3 | Combined/Ranking | Winner | Winner acquisition policy | 0092 | Winner capture tests | Gate D |
| D-03 Every attempt has durable reason | P2 | Winner | Audit/API | acquisition ledger | 0092 | retry/concurrency tests | Gate D |
| E-01 Split status dimensions | P4 | Pipeline | Operations/CERI/UI | pipeline outcome summary | — | pipeline matrix | Gate E |
| E-02 Preserve legacy status behavior | P4 | Pipeline | Existing consumers | compatibility projection | — | state/recovery tests | Gate E |
| F-01 `PARTIAL` not contamination alone | P4 | Pipeline | CERI | integrity assessment | — | partial/failed incident tests | Gate F |
| F-02 Historical exclusions remain excluded | P4/P6 | CERI | Current queries | disposition-first eligibility | — | historical disposition test | Gate F |
| F-03 Confidence/actionability split | P5 | CERI | Research/change/alerts | `ceri/actionability.py` | — | confidence matrix | Gate F |
| G-01 Population-explicit API/UI | P2/P5 | Winner/CERI/Pipeline | Operator UI/API | routes/templates | — | route/view-model tests | Gate G |
| H-01 Frozen Run 32/33 differential | P6 | Historical evidence | Shadow only | test fixtures/exporter | — | fixture verification | Gate H |
| H-02 Historical immutability | P6 | All | All | read-only exporter + DB hashes | — | before/after fingerprint check | Gate H |

Activation state and exact test results are maintained in `IMPLEMENTATION_LEDGER.md`.
