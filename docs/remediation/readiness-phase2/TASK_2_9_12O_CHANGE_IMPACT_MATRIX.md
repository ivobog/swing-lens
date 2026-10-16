# Task 2.9.12o Change-Impact Matrix

## Baseline and scope

This impact analysis was recorded before Task 2.9.12o production changes.

- Branch: `codex/ceri-authority-remediation`
- Baseline HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- Baseline timestamp: `2026-10-15T13:51:43.6897352+02:00`
- Alembic head: `0092_phase2_eligibility_ledgers`
- Source-pin validation: `{"stale": []}`
- Existing dirty worktree and Tasks 2.9.12a-n evidence are preserved.

## Pre-implementation dependency review

| Candidate change | Producer contract | Direct callers / writers | Downstream consumers | Persisted / serialized dependency | Required tests before release |
|---|---|---|---|---|---|
| Add field-level run-start-anchor mismatch diagnostics without changing equality | `build_run_start_anchor_manifest` and `_verify_run_start_anchor_manifest` in `transition_preflight_plan_service` | Preflight creation; enqueue verification; decision-handoff freeze; Setup pre-mutation verification | Background-job deterministic classifier; pipeline failure roll-up; operator/JUnit failure evidence | `TransitionPreflightPlan.run_start_anchor_json`; canonical SHA-256; `BackgroundJob.result_json.failure_classification` | v1/v2 exact-hash tests; structural-diff unit tests; protected raw/context/config mutation negatives; continuation failure durability |
| Reconcile certified CERI count with pipeline summary | CERI completion barrier in `pipeline_service` | CERI child-workflow roll-up and continuation enqueue | Resumed pipeline result merge; diagnostic UI/API; decision handoff; Setup and Winner | `PipelineRun.result_json.ceri_certified_capture_count` and `ceri_score_snapshots`; child capture `score_snapshots` result | completion-barrier PostgreSQL test; resumed-continuation unit test; missing-capture negative; full cross-stage test |
| Clarify handoff-stage completion semantics | `_freeze_pipeline_handoff` / `_pipeline_step` | Initial and resumed full-pipeline execution | Pipeline progress page/API; Setup capture; Winner capture; recovery/replay | `PipelineStep.status`, `message`, `result_json`; immutable `TransitionDecisionHandoffManifest` | stage-state unit test; continuation failure test; GUI semantics test; restart/reuse test |
| Correct the proven anchor semantic inconsistency, if reproduction shows one | Versioned run-start-anchor schema and verifier | All three verifier boundaries listed above | Decision handoff, Setup, Winner identity binding, historical v1 projection | v1/v2 canonical JSON and fingerprints; no historical rewrite | exact field-diff reproduction; current v2 positive; historical v1 positive; mutation matrix; real cross-stage PostgreSQL certification |

## Non-change constraints

The following remain outside any permissible semantic relaxation: raw evidence content and
membership, ticker membership, frozen market context and PIT cutoffs, configuration identity,
execution-root and continuation authority, cancellation fencing, CERI certified capture,
Setup/Winner source provenance, atomic publication, and the 120-second transaction-age limit.

## Implemented changes and verified impact

The reproduction classified the failure as **G — asynchronous continuation/session-bound
consumer inconsistency**. The anchor schema and its equality rules were not changed.

| Change | Producer contract | Downstream consumers reviewed | Persisted / serialized effect | Verification |
|---|---|---|---|---|
| Eager-load `RawCompanyRow.run` in `SetupLifecycleSourceLoader` | A `RunSourceContext` returned from the bounded read transaction is a detached, build-ready value graph | `SetupLifecycleSnapshotBuilder.build`, `_source_lineage`, `build_run_context_snapshots`, pre-mutation decision reconstruction, live Setup capture | No schema or hash change; restores the already-required `source_run_status` lineage input | New real-PostgreSQL bounded-session regression; full transition-preflight matrix; 10-member real pipeline target |
| Reject per-ticker reconstruction failures before anchor comparison | Setup pre-mutation verifier receives both built rows and a bounded failure map | durable job classifier, pipeline failure roll-up, failure-evidence exporter | New deterministic code `DECISION_MANIFEST_RECONSTRUCTION_FAILED`; diagnostics retain category/digest only | unit classification/redaction tests; deterministic non-retryable job tests; negative mutation matrix |
| Add digest-only canonical structural differences | Run-start anchor verifier remains an independent reconstruction and exact canonical equality check | pre-enqueue verification, handoff freeze, Setup pre-mutation verification, background-job diagnostics | No anchor payload or fingerprint change; failure metadata gains exact canonical paths and source origins | exact-path unit test; controlled Chromium-10 failure evidence; v1/v2 PostgreSQL tests |
| Publish certified CERI count into `ceri_score_snapshots` on both fresh and replayed completion | Completion barrier counts distinct ticker rows for the exact run/context with non-null immutable evidence | resumed pipeline merge, pipeline API/GUI result, handoff/Setup/Winner diagnostics | `PipelineRun.result_json.ceri_score_snapshots` now equals `ceri_certified_capture_count` for the certified population | restart-safe/exactly-once PostgreSQL barrier test, missing-capture negative, resumed pipeline and real cross-stage tests |
| Record handoff completion and continuation acceptance separately | Handoff stage completion means manifest evidence committed and independently verified; Setup acceptance is a later state | initial/resumed executor, pipeline status API, progress templates/JavaScript, Setup and Winner tail | `PipelineStep.result_json` gains `completion_semantics`, `authority_state`, `continuation_state`, and consumer acceptance; GUI-visible message states the phase | executor state test, progress/API consumer review, continuation/restart tests, real cross-stage execution |
| Persist failure export outside disposable execution cleanup | Certification failure exporter | operator/JUnit diagnostics and post-cleanup forensic review | Sanitized anchor/source/job/step evidence is written to an execution-ID directory under readiness evidence as well as the native run directory | exporter readability assertions; controlled failures retained in native JUnit; final certification failure path reviewed |
| Bind read-only handoff verification to a durable certificate and re-confirm under lock | `verify_transition_decision_manifests_before_mutation` and `confirm_transition_decision_verification_before_mutation` | Setup publication, background continuation, decision handoff, cancellation/supersession fencing | Adds an in-memory certificate fingerprint over the complete durable plan/handoff pair; no schema or canonical contract change | unit certificate tests; real PostgreSQL unchanged/tampered authority tests; negative execution-authority matrix |
| Bound expensive verification reads and reduce verification checkpoint batches to five members | Setup capture and transition verifier | run-start reconstruction, artifact-lineage serialization, temporal-lineage validation, progress observers | Read-only child transaction commits at progress boundaries; final parent lock remains mandatory | 5 focused batch tests; bounded Session unit tests; PostgreSQL ownership tests; 163-test cross-stage selection |
| Avoid expired ORM refreshes after stage commit | pipeline executor progress and Setup initial/resume paths | pipeline progress, Setup capture, worker continuation | Uses SQLAlchemy identity and cached scalar upload-run ID; no persisted contract change | `expire_on_commit=True` unit and PostgreSQL tests; Chromium-10 sentinel |
| Preserve independent failure statuses after full-system execution | Task 2.9.12o reporting | release gate, operators, future remediation | Native failing JUnit and execution-owned logs retained; no release XML fabricated | final Chromium-100 failed the 120-second invariant; release suite correctly not run |

## Explicitly unchanged contracts

- `transition-run-start-anchor-v1` and `transition-run-start-anchor-v2` field sets,
  canonical ordering, and SHA-256 equality are unchanged.
- No stored preflight or handoff manifest is rewritten.
- No financial formula, source-selection predicate, PIT cutoff, C1 configuration, mutation
  authority, execution token, cancellation fence, or publication transaction is relaxed.
- The source-loading change adds one bounded eager-load query; it does not combine the preflight
  creation and independent-verification transactions or change the 120-second limit.

## Residual impact discovered by Chromium-100

The latest full execution proved one unclosed dependency not covered by the focused matrix:
`SetupLifecycleSnapshotCaptureService` reports `SOURCE_CONTEXT` with the parent pipeline Session
and then calls the CPU-heavy 100-member builder without its available checkpoint callback. The
parent transaction remains open across that build and violates the 120-second invariant. No final
remediation was applied after this finding, so the source tree is honestly reported as not fully
certified.
