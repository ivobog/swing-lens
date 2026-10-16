# SwingLens Task 2.9.12p CERI Contract Reconciliation

## Result

**CERI regression status: CLOSED for the reported incident.** No accessible pre-task evidence or
the new execution contains a CERI failure. The latest actual failure is downstream Setup
transaction lifetime. This status does not certify the entire pipeline or Phase 2.9 release.

## Production dependency audit

| Contract boundary | Evidence reviewed | Result |
|---|---|---|
| Frozen run/context authority | Exact upload/pipeline workflow, retained fixture/provider hashes, authority tests | Pass |
| Provider acquisition | Deterministic provider workflow completed | Pass |
| Source identity/normalization | Batched normalize and feature workflow completed | Pass |
| Revision/feature evidence | Jobs 26 and 27 completed 50 members each | Pass |
| Feature build state | 100 progress checkpoints reached | Pass |
| CERI finalization | Job 28 completed | Pass |
| Certified capture | Job 29 completed | Pass |
| Exact-membership barrier | Continuation job 32 was admitted once | Pass |
| Continuation acquisition | Workflow `pipeline:1:ceri-continuation`, current generation | Pass |
| Result roll-up | Targeted barrier tests cover positive and incomplete paths | Pass |
| Decision handoff | Latest execution completed handoff before Setup | Pass |
| Setup consumer | Reached with valid handoff; transaction lifetime later failed | CERI contract pass; downstream failure |
| Winner consumer | Not reached in Chromium-100 | Uncertified at full scale |

No retained evidence indicates missing captures, cross-run substitution, unauthorized duplicate
capture, stale continuation, cancellation-fence violation, false CERI completion, or invalid retry.
The disposable E2E database was deleted after evidence export, and the numeric certified count was
not included in the export. The successful exact-membership barrier is retained as evidence that
the required population was satisfied; no numeric count is fabricated.

## Mandatory non-interference proof

`tests/integration/test_ceri_batched_workflow_v2.py::test_ceri_authority_is_unchanged_by_downstream_operational_metadata`
runs the production batched CERI workflow twice on independently migrated PostgreSQL databases with
identical frozen time and deterministic inputs. One execution receives unrelated downstream
Setup, decision diagnostic, GUI, pipeline, and Winner operational metadata.

The test compares stable semantic authority rather than generated IDs or timestamps:

- provider/dataset/provider-record source identities, content hashes, and idempotency keys;
- frozen market context cutoff/session/calendar/readiness identity;
- exact certified ticker membership;
- CERI snapshot evidence keys and evidence payload/calculation fingerprints;
- immutable source evidence keys and authorized lineage;
- score/feature output parity;
- continuation job type, workflow, run, and calculation-context bindings.

Result: **1 passed in 23.14 seconds** during development and passed again in the clean 203-test
targeted JUnit. No unexplained CERI financial-evidence difference was observed.

## Negative and lifecycle coverage

The final targeted selection includes:

- incomplete certified-capture rejection;
- partial child roll-up without downstream continuation;
- exact-once restart-safe completion;
- parent failure fencing of queued/running siblings;
- cross-run and wrong-context rejection;
- post-CERI continuation without upstream replay;
- historical v1 anchor fail-closed behavior;
- later market-bar and later CERI-source-document rejection;
- stale generation, delayed child completion, cancellation, and continuation lock tests.

The native targeted JUnit contains 203 passed, 0 failed, 0 errors, and 0 skipped tests.

## Authority-preservation statement

No CERI formula, normalization rule, exact membership rule, source cutoff, immutable evidence row,
frozen configuration, PIT semantic, continuation authority, or historical record was changed.
Generated database IDs and timestamps were deliberately excluded from cross-database equality;
canonical semantic identities were compared instead.

