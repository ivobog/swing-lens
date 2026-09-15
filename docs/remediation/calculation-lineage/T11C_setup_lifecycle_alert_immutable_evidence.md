# T11C — Setup / Lifecycle / Alert Immutable Evidence

## 1. Baselines

| Baseline | Commit |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T11A | `5d2df19c0ef4ff3e7f49f17bd7889378021bfb31` |
| T11B-1 | `bef1abe285779c3aa278e3691a9438126f5ccedc` |
| T11B-2 | `f5bfa546ac5d03e641eb67473b21e7a04629cb23` |
| T11B-3 / integrated T11B | `3aee1c260f63c53f5fe25d85be2948283b48dbc1` |
| T11C starting HEAD | `3aee1c260f63c53f5fe25d85be2948283b48dbc1` |
| T11C branch | `codex/t11c-setup-lifecycle-alert-evidence` |

The starting worktree was clean, with no tracked changes or untracked files. Python was 3.12.2 and the pre-T11C Alembic head was `0078_ibmi_constituent_evidence`.

## 2. Scope

T11C adds immutable evidence to the remaining Setup, Lifecycle, and Alert state-machine paths. It deliberately does not redesign lifecycle business rules, implement original-context replay, establish full configuration authority, or perform the repository-wide historical selector campaign assigned to T11D. No production data was rewritten and no legacy evidence was fabricated.

## 3. Current-state architecture before T11C

Before T11C, Setup snapshots were append-like and lifecycle events recorded useful history, but lifecycle episodes, canonical snapshots, alert rules, and alert notification rows were mutable current-state records. Historical lifecycle decisions did not pin an immutable evaluation chain, alert decisions did not retain exact rule or predecessor evidence, and current projection changes could not by themselves prove what an older decision meant.

The pre-existing current-state objects remain useful serving projections. T11C separates those projections from evidence instead of replacing them.

## 4. Setup evidence architecture

Each newly certified `SetupSignalSnapshot` points to a `CoreCalculationEvidence` row of kind `SETUP`. Its frozen input/output envelope retains the Setup Calculation Identity, session, cutoff, calendar, setup/config version, output state, readiness/confidence, warnings, source/config fingerprints, behavioral liquidity and earnings-risk inputs, and metadata-only Combined/Ranking fields.

Exact Phase-2 evidence dependencies are pinned through `CoreCalculationEvidenceSource`: Technical, Fundamental where consumed, Combined, Ranking metadata, Regime, and Sector. PIT price provenance is frozen as exact bar-manifest evidence plus a series fingerprint. CERI is not included.

`persist_setup_evidence` reuses an exact deterministic result and fails if a declared upstream evidence pointer is missing. A legacy snapshot lacking evidence-complete lineage remains explicit legacy data and does not receive fabricated certification.

## 5. Lifecycle evaluation evidence

`SetupLifecycleEvaluationEvidence` is append-only and records every certified lifecycle evaluation, including no-transition evaluations. It pins the exact Setup evidence, prior evaluation and transition evidence, episode projection observed at evaluation time, session/cutoff/calendar, Calculation Identity, rule/config identity, input counters and conditions, output state, transition eligibility, reasons, and warnings.

Prior evidence must be compatible with ticker, timeframe, semantic family, and calculation time. A prior evaluation newer than the requested effective session fails closed. Exact retries reuse the deterministic evidence row; the same state under different evidence or context produces a different evidence identity.

## 6. Transition evidence chain

`SetupLifecycleTransitionEvidence` freezes `from_state`, `to_state`, effective session, triggering evaluation and Setup evidence, prior evaluation/transition links, rule/config identity, and reason codes. Transitions therefore form an addressable predecessor-linked chain. ORM mutation guards and restrictive foreign keys prevent supported update/delete paths from changing retained evidence.

Compatibility `SetupLifecycleEvent` rows now point to the immutable transition that justified them. Superseding an event's current-view marker does not alter the transition evidence.

## 7. Current lifecycle projection

`SetupLifecycleEpisode` remains a mutable current projection and now points to its latest certified evaluation and transition evidence. State, counters, terminal flags, and timestamps may advance for serving, but each certified advance first creates or resolves immutable evidence and then moves the projection pointers. Earlier evidence remains directly addressable.

The canonical Setup selection likewise remains a current projection. Moving it from S1 to S2 does not change S1 or historical consumers that hold S1's evidence ID. `SignalAlertEvent` remains the mutable notification/current-state object and points to immutable alert-decision evidence where certified.

## 8. Repair semantics

Repair is classified as `CURRENT_STATE_REPAIR`, not original-context reconstruction. Certified repair uses the same upper-bounded evidence selection as ordinary lifecycle evaluation. An older-context repair cannot consume or overwrite newer lifecycle evidence; it fails closed. A correction at a valid later context creates new evaluation/transition evidence and advances the current projection while retaining the original decision.

Legacy episodes without a certified predecessor chain remain current-only. T11C does not invent a certified chain for them.

## 9. Replay semantics

Replay remains explicitly `CURRENT_RULES_RETROSPECTIVE`. Persisted replay produces separate lifecycle evaluation evidence marked `REPLAY`; it does not mutate the original transition chain or the current episode projection. This makes replay non-destructive without claiming it reproduces unavailable original rules or full historical context.

## 10. Alert rule evidence

`SignalAlertRuleEvidence` snapshots the exact decision-relevant payload of a mutable alert rule and its deterministic hash. Historical decisions reference this immutable snapshot, so an A1 decision under R1 remains an R1 decision after the current rule changes to R2. New decisions under R2 receive separate rule evidence.

This is narrowly scoped decision evidence, not full Phase-4 configuration authority.

## 11. Alert decision/suppression evidence

`SignalAlertDecisionEvidence` records `GENERATED`, `SUPPRESSED_COOLDOWN`, `SUPPRESSED_DEDUP`, and `INELIGIBLE` outcomes for evidence-complete candidates. It pins exact Setup, lifecycle evaluation/transition, alert-rule, decision session/cutoff/calendar, event semantics, and the applicable cooldown/dedup predecessor decision.

Generated `SignalAlertEvent` rows point to their immutable decisions. Acknowledgement, dismissal, and other notification state remain mutable current state without changing decision meaning. Deliberate certified suppressions are retained even when no alert event is emitted.

## 12. Cooldown/dedup temporal bounds

Certified cooldown and dedup lookup uses only prior `GENERATED` alert-decision evidence with `effective_session <= candidate effective_session`, matching ticker, timeframe, and semantic event. Exact predecessor evidence IDs are retained on suppression decisions. The compatibility event-key lookup is also upper bounded by the decision date. A future alert therefore cannot suppress a historical evaluation.

## 13. Legacy behavior

No backfill is performed. Evidence-incomplete Setup and transition/event records are exposed as `LEGACY_UNKNOWN`; current lifecycle and alert projections without certified pointers are exposed as `LEGACY_CURRENT`. They can continue to serve explicitly current/legacy views, but certified historical getters require evidence IDs and do not reinterpret legacy rows as certified history.

## 14. Negative dependencies

The refactor preserves the audited dependency graph:

- CERI → Setup: absent.
- CERI → Lifecycle: absent.
- CERI → Winner: absent.
- Setup → Winner: absent in production acquisition.
- Lifecycle → Winner: absent in production acquisition.
- Alert → Winner: absent.

Winner contains a pre-existing dormant optional `setup_lifecycle_features` DTO/extractor field, but no production repository path populates it; T11C does not change it. The shared canonical serialization helper imported by Setup is a utility, not a Winner data edge.

Combined score/decision and Ranking score/decision/profile remain metadata only. Combined `earnings_risk`, Technical, Regime, and Sector retain their existing behavioral/context roles. Counter semantics remain qualifying observations rather than being redesigned as consecutive trading sessions.

## 15. Schema/migrations

Migration `20260915_0079_setup_lifecycle_alert_evidence.py` advances Alembic from `0078_ibmi_constituent_evidence` to revision `0079_setup_lifecycle_alert_ev`. It:

- adds `SETUP` to core calculation evidence kinds;
- adds the immutable lifecycle evaluation and transition tables;
- adds immutable alert rule and decision evidence tables;
- adds Setup snapshot, lifecycle projection/event, and alert event evidence pointers;
- adds deterministic uniqueness/check constraints, indexes, and restrictive evidence foreign keys;
- performs no production rewrite or legacy backfill.

Offline PostgreSQL DDL generation passes. The disposable PostgreSQL runtime test is present but skipped because local PostgreSQL authentication is unavailable. Runtime certification of the complete Phase-2 migration chain remains deferred to T11E.

## 16. Tests

| Lane | Result |
|---|---|
| T11C focused + Setup/Lifecycle/Alert subsystem | 270 passed, 1 skipped, 1 warning |
| T11A/T11B evidence regression | 27 passed, 5 skipped, 1 warning |
| Affected Technical/Combined/Ranking/Regime/Sector/pipeline systems | 368 passed, 1 warning |
| Phase-1 identity regression | 134 passed, 1 warning |
| Phase-0 temporal/domain/publication regression | 115 passed, 1 warning |
| Broad safe local lane | 2551 passed, 128 skipped, 125 deselected, 23 warnings |

The skipped focused test is the disposable PostgreSQL upgrade/constraint test. The warning is an environment deprecation warning from FastAPI/Starlette's test client. An initial broad run had one isolated PowerShell process-start timeout in `test_powershell_parses_readiness_payload_not_http_status`; its exact rerun passed, and the final post-edit broad rerun completed cleanly in 449.12 seconds. Broad-lane warnings are 21 Python 3.12 SQLite datetime-adapter deprecations, one Alembic configuration deprecation, and one FastAPI/Starlette test-client deprecation.

Focused tests cover Setup immutability and upstream pinning, canonical projection movement, lifecycle no-op and transition evidence, exact predecessor chains, current episode advancement, older repair failure, separate replay evidence, R1/R2 rule preservation, future-alert exclusion, cooldown and dedup suppression predecessors, same-state/different-identity behavior, exact retry idempotency, and legacy classification.

## 17. Static writer/read classification

| Path | Classification | Evidence conclusion |
|---|---|---|
| `snapshot_builder.py` | `IMMUTABLE_EVIDENCE_WRITE` input assembly | Freezes Setup identity, PIT price manifest, and source lineage; no CERI input. |
| `evaluation_service.py` | `IMMUTABLE_EVIDENCE_WRITE` + `CURRENT_PROJECTION_WRITE` orchestration | Persists Setup evidence before lifecycle/change detection. |
| `decision_evidence.py` Setup functions | `IMMUTABLE_EVIDENCE_WRITE` / `EVIDENCE_READ` | Deterministic append/reuse; no update/delete/upsert/merge. |
| `decision_evidence.py` lifecycle functions | `IMMUTABLE_EVIDENCE_WRITE` / `HISTORICAL_EVIDENCE_READ` | Exact upper-bounded prior chain; no mutable-latest historical selection. |
| `episode_service.py` ordinary evaluation | `IMMUTABLE_EVIDENCE_WRITE` then `CURRENT_PROJECTION_WRITE` | Creates evaluation/transition evidence before advancing episode/event projections. |
| `episode_service.py` observation-gap maintenance | `CURRENT_PROJECTION_WRITE` with immutable maintenance evidence | Current-context activity; old evidence is not rewritten. |
| `episode_service.py` older repair | `CURRENT_STATE_REPAIR` | Upper-bound violation fails closed; valid correction appends evidence. |
| `replay_service.py` | `CURRENT_RULES_RETROSPECTIVE` | Separate REPLAY evaluation evidence; original chain/projection unchanged. |
| `alert_service.py` rule/decision paths | `IMMUTABLE_EVIDENCE_WRITE` | Exact rule snapshots and generated/suppressed decisions. |
| `alert_service.py` certified cooldown/dedup | `HISTORICAL_EVIDENCE_READ` | Exact prior GENERATED decision, upper bounded by effective session. |
| `alert_service.py` legacy candidates | `LEGACY` | Existing current behavior retained without fabricated evidence. |
| `repository.py` event-key query | `CURRENT_READ` with temporal bound | Compatibility dedup read is bounded through the decision date. |
| `repository.py` episode/event updates | `CURRENT_PROJECTION_WRITE` | Mutates serving projection/current-version markers only. |
| `repository.py` one-time snapshot canonical fields | `CURRENT_PROJECTION_WRITE` / `LEGACY` | Compatibility canonical projection, separate from evidence. |
| `repository.py` rule upsert | `CURRENT_PROJECTION_WRITE` | Mutable current rule; every certified decision snapshots effective payload. |
| alert acknowledge/dismiss paths | `CURRENT_PROJECTION_WRITE` | Notification state only; immutable decision is unchanged. |
| Setup cleanup/purge paths | `CURRENT_PROJECTION_WRITE` / `OUT_OF_SCOPE` | Ordinary cleanup does not target evidence tables; restrictive FKs retain referenced evidence. |
| `query_service.py` | `CURRENT_READ` plus explicit `EVIDENCE_READ` status | Exposes evidence IDs and `CERTIFIED`, `LEGACY_CURRENT`, or `LEGACY_UNKNOWN`. |
| dormant Winner Setup DTO field | `OUT_OF_SCOPE` | Not populated by production acquisition and unchanged. |

Static searches found no supported update, delete, merge, bulk-update, or repair mutation of the four new evidence models. ORM guards reject update/delete, and restrictive FKs prevent cascaded loss. No unexplained `REMAINING_T11C_DEFECT` remains within the certified T11C paths.

## 18. Finding reconciliation

| Finding/invariant | T11C status | Rationale |
|---|---|---|
| SETUP-002 | REMEDIATED for certified paths | Older-context repair fails closed; valid corrections append evidence and cannot rewrite newer history/current state. |
| SETUP-004 | PARTIALLY_REMEDIATED | Lifecycle historical state is now a coherent immutable chain. Unrelated rule semantics, consecutive-session interpretation, and original configuration reconstruction are intentionally unchanged. |
| SETUP-006 | PARTIALLY_REMEDIATED | Replay is separate and non-destructive but remains honest current-rules retrospective, not original-context reconstruction. |
| SETUP-007 | REMEDIATED for temporal alert evidence; PARTIAL overall | Future alerts cannot affect historical cooldown/dedup and exact rule/predecessor evidence is retained. Full configuration authority remains later work. |
| SETUP-009 | REMEDIATED for certified history | Mutable canonical/current pointers can advance without changing referenced historical Setup, lifecycle, or alert meaning. |
| SETUP-010 | PARTIALLY_REMEDIATED | Maintenance now appends evaluation/transition evidence, but it is still current-context activity without a complete frozen historical MarketCalculationContext. |
| XINT-005 | REMEDIATED for certified Setup/Lifecycle/Alert; PARTIAL repository-wide | All new certified producers use immutable evidence; T11D must certify repository-wide readers/selectors. |
| XINT-008 | REMEDIATED for T11C prior/cooldown/dedup paths; PARTIAL repository-wide | The touched historical selectors are explicitly upper bounded. T11D remains the broader campaign. |
| INV-EVIDENCE-001 | ENFORCED across newly certified producers | Mutable projections are separate from immutable Setup/lifecycle/alert evidence. Repository-wide read enforcement remains partial. |
| INV-TRUTH-001 | ENFORCED for the certified Phase-2 evidence graph | Later truth, projection, canonical selection, and rule edits do not alter historical decision evidence. |
| INV-IDENTITY-001 | PRESERVED | Evidence identity/compatibility checks and Phase-1 regression remain green. |

## 19. Residual Phase-2 risks

- T11D must complete repository-wide historical-read enforcement and detect any mutable-current selector used as historical truth outside the T11C graph.
- T11E must run the complete Phase-2 migration chain and ORM/constraint round-trip against a disposable PostgreSQL instance; local authentication remains unavailable.
- Replay remains `CURRENT_RULES_RETROSPECTIVE`; full original-context replay is deferred.
- Full configuration authority, refresh-cycle identity, readiness propagation, and background target-scope freezing remain outside T11C.
- Legacy rows remain explicitly uncertified because no evidence is fabricated or broadly backfilled.

## 20. Final verdict

**PASS**, subject only to the explicitly deferred repository-wide T11D selector certification and T11E PostgreSQL runtime migration certification. T11C's certified Setup, Lifecycle, and Alert paths preserve immutable historical meaning while retaining separate mutable current projections. No production/runtime data mutation, production rewrite, or legacy backfill was performed.
