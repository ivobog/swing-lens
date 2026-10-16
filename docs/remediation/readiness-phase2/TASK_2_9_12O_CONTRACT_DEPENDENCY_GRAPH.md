# Task 2.9.12o Contract Dependency Graph

## Control-plane spine

```text
PipelineRun + immutable execution/configuration authority
  -> PipelineStep transaction boundary
  -> BackgroundJob root
  -> CERI child fan-out + PipelineDependency barrier
  -> fenced FULL_PIPELINE continuation
  -> decision-handoff publication
  -> Setup publication
  -> Winner publication
  -> terminal PipelineRun publication
```

`pipeline_runs`, `pipeline_steps`, `background_jobs`, `background_job_fanout_roots`, and
`pipeline_dependencies` carry the durable state between process boundaries. Effective
configuration records, execution-configuration anchors/bindings, execution root/generation, and
request/workflow keys fence every continuation. Cancellation or supersession is checked before a
write boundary; retry uses the durable idempotency keys rather than selecting latest evidence.

## Authoritative evidence flow

| Stage / producer | Authoritative inputs | Durable output | Identity, fingerprint, and consumer verification | Transaction / async / idempotency contract |
|---|---|---|---|---|
| Upload and raw ingestion | uploaded document and validated row projection | `upload_runs`, `raw_company_rows` | run ID, row ID/number, ticker, canonical raw-row fingerprint; downstream always binds the exact upload run | request/run identity owns mutation; raw rows are immutable after admission |
| SEC/source validation | raw rows and source-document knowledge at cutoff | validation disposition on the admitted raw projection | source-document known-at-or-before cutoff participates in the frozen source contract | same admission transaction; failure prevents preflight |
| Frozen market context | upload run, calendar/session resolution, provider cutoff | `market_calculation_contexts` | context ID, cutoff timestamp, completed session, calendar version, pipeline/run binding | created once for the pipeline; explicit ID is passed to all workers; no latest-context fallback |
| Transition preflight and run-start anchor | raw rows, discovered candidates, eligible prices, effective Setup configuration | `transition_preflight_plans.run_start_anchor_json` and fingerprint | v1/v2 canonical contract, ticker population, market context, source cutoffs, per-ticker immutable lineage; independently rebuilt at enqueue, handoff, and Setup | reservation/idempotency key; exact plan binding; no rewrite or version fallback |
| Fundamental scoring | admitted raw rows and frozen configuration | `fundamental_scores`, `core_calculation_evidence*` | upload run/ticker, producer/config signatures, certified source members | stage transaction; deterministic current projection and immutable evidence |
| IB acquisition | frozen ticker scope, contract resolution, market context | `ib_fetch_runs`, `ib_fetch_items`, `price_bars`, revisions/series versions | contract/ticker, session, observed-at cutoff, bar hash and revision; PIT selectors reject future or wrong-run data | durable child work and retries; run/context-scoped uniqueness |
| Technical scoring | eligible price series and technical source manifest | `technical_feature_artifacts`, `technical_source_manifests`, `technical_scores`, core evidence | context/cutoff/session, source-series fingerprint, engine/config identity | durable batch checkpoints; idempotent per run/ticker/context |
| Market regime | frozen eligible market series | `market_regime_snapshots` | run/pipeline/context, as-of session, evidence ID | stage-scoped atomic publication |
| Combined scoring | raw, fundamental, technical, and certified source evidence | `combined_results`, core evidence | exact source IDs/evidence IDs plus configuration identity; handoff validates retained graph | retry may replace a projection ID but cannot change immutable certified source evidence |
| Ranking and sector rotation | combined results, profiles, market regime | `ranking_results`, `sector_rotation_snapshots`, `sector_rotation_rows` | run/ticker/profile rank and frozen context; Setup checks each required artifact | stage transactions and deterministic ordering; idempotent profile/snapshot keys |
| CERI provider ingest | frozen CERI scope/context and offline deterministic provider records | CERI source, estimate, guidance, catalyst, and revision tables | workflow key, exact run/context/company/ticker, provider provenance and cutoff | durable fan-out jobs; root generation and cancellation fenced |
| CERI normalize/features | certified provider evidence | normalized records, feature/build-state rows | immutable evidence lineage, calculation/config version, run/context scope | child batch checkpoints, exactly-once request/workflow keys |
| CERI capture/certification | normalized/features and frozen CERI context | `ceri_score_snapshots` with non-null evidence IDs | completion barrier counts distinct tickers for exact `run_id` + `calculation_context_id`; count must equal distinct raw ticker population | `PipelineDependency` barrier queues one continuation; coalesced replay retains the same certified count |
| Decision handoff | consumed preflight plan, authorized upstream artifacts, certified CERI snapshots | `transition_decision_handoff_manifests` | plan/run/pipeline/context binding, run-start fingerprint, per-ticker decision manifests, artifact lineage, CERI evidence, canonical fingerprint | evidence is frozen and committed before continuation; durable certificate is independently rechecked by Setup |
| Setup source acquisition | exact plan/handoff, upload run, frozen context/tickers, upstream artifacts and price history | detached `RunSourceContext` value graph (read-only) | eager-loaded `RawCompanyRow.run`, exact source IDs, PIT context, effective Setup config | bounded child read Session commits/closes before CPU construction |
| Setup build and admission | detached context graph and historical Setup snapshots | in-memory `BuiltSnapshot`; then `setup_signal_snapshots` and evaluation evidence | source data hash, source lineage, configuration identity, run/context, handoff certificate; final locked authority confirmation before first mutation | intended: CPU work outside transaction, then atomic publication. **Residual defect:** parent progress write opens a transaction before uncheckpointed 100-member build |
| Setup lifecycle | canonical Setup snapshots | evaluation runs/evidence, transitions, episodes, lifecycle events, alerts | canonical selection, source/evidence IDs, frozen run/context/config | publication transaction; replay/current-selection constraints prevent duplicate authority |
| Winner publication | eligible Setup evidence and exact terminal lineage | `winner_prediction_snapshots`, evidence manifests/members, estimates/publication requests | run/ticker, Setup evidence, model version, eligibility and temporal decisions; rejects cross-run/stale sources | independently fenced publication request; duplicate authoritative publication rejected |
| Final pipeline publication | completed authoritative stages and counters | terminal `pipeline_runs.result_json` and completed `pipeline_steps` | result retains CERI/Setup/Winner counts, execution/configuration lineage, and status semantics | terminal transition is atomic; cannot be reached after cancellation, supersession, or failed invariant |

## Producer/consumer boundaries reviewed for Task 2.9.12o

```text
build_run_start_anchor_manifest
  -> TransitionPreflightPlan.run_start_anchor_json/fingerprint
  -> verify before enqueue
  -> freeze_transition_decision_handoff_manifest
  -> TransitionDecisionHandoffManifest
  -> verify_transition_decision_manifests_before_mutation
  -> confirm_transition_decision_verification_before_mutation
  -> Setup persistence
  -> Setup lifecycle
  -> Winner evidence acquisition/publication
```

The producer and verifier intentionally remain independent. They share the versioned canonical
schema but the verifier reconstructs from authoritative rows; it does not trust or recompute and
overwrite the stored hash.

## Failure and status semantics

- **Stage computation complete:** the stage body produced its candidate evidence.
- **Evidence committed/frozen:** the immutable handoff row is durable.
- **Authority verified:** stored payload/fingerprint and reconstructed evidence agree.
- **Continuation accepted:** the downstream consumer locked and confirmed the same authority pair.
- **Terminally complete:** all required downstream publication and invariant checks passed.

The handoff stage now exposes these phases in its result/message rather than presenting committed
evidence as downstream terminal acceptance. The remaining Setup transaction failure occurs after
handoff evidence is committed but before Setup authority acceptance/publication completes.

## Verification coverage

- v1/v2 exact canonical verification and wrong-version rejection
- raw-row, membership, PIT-context, configuration, anchor, run-binding, execution-root, producer,
  stale-source, cross-run, cancellation/supersession, missing-CERI, and duplicate-publication
  negatives
- real PostgreSQL CERI barrier, Setup source-session, locked handoff confirmation, Setup and Winner
  consumers
- restart/coalesced continuation behavior and GUI/API handoff semantics
- Chromium-10 realistic browser path
- full Chromium-100 transaction observer, which exposed the remaining Setup boundary defect

