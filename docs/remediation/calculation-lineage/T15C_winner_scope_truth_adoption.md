# T15C — Winner target-scope, refresh, and truth-revision adoption

## 1. Executive verdict

PASS. All 15 assigned Winner operation families adopt explicit scope/refresh semantics; WIN-008 closes. WIN-006 remains truthful PARTIAL for birth bars without revision rows.

## 2. Baselines

Started at T15B commit `0c8e5664c52ff19d80d30f33c64ade8772c955a8`, tree `8ef7a81f8392810786ada89560847a4359a393cd`, migration head `0082_pipeline_ceri_scope`, clean worktree. T15C head is `0083_winner_scope_truth`.

## 3. T15A/T15B handoff reconciliation

T15A assigned 15 exact families to T15C and T15B supplied the generic frozen-scope, refresh, plan, remainder, zero-progress, and parent/child primitives. The reconciliation CSV contains 15 rows and zero unreconciled families.

## 4. Winner scope model

Prediction capture, maturation, outcome correction, cohort refresh, generation, and publication are distinct semantic targets. Population operations use persisted typed members; exact single-artifact publication retains generation identity.

## 5. Maturation admission

Maturation admission freezes current pending/current-revision primary H5 NEXT_OPEN predictions due at the retained completed session, with temporal eligibility evaluated once at admission.

## 6. Maturation target membership

Membership is exact `WINNER_PREDICTION` IDs. Batches receive those IDs and cannot append a later live-query result.

## 7. Retry/resume/reclaim

Retry, resume and reclaim change operational leases only. Scope, refresh, plan, cutoff, configuration anchor and members remain invariant.

## 8. Continuation/remainder

Continuation queries the retained prediction IDs and subtracts terminal outcomes. Required child jobs inherit the parent authority.

## 9. Zero-progress behavior

Zero progress with remaining members records `ZERO_PROGRESS_BLOCKED` and retained members; retry-deferred work defers the same job and creates no child chain.

## 10. Refresh cycles

Each completed-session scheduler key is a refresh cycle. Same-cycle concurrency converges; a later terminal-to-new admission creates R2 and may include newly due work.

## 11. Checkpoints

Winner processing runs and checkpoint bodies retain scope/refresh IDs. Single-assignment database triggers reject rebinding.

## 12. Outcome truth model

Outcome proofs retain prediction, horizon, policy, sessions, complete PriceBar values/hashes and revision identity state.

## 13. PriceBar revision inventory

PriceBar row ID, content hash, symbol, session, source, created/first-seen/revised timestamps and cutoff are retained. Matching PriceBarRevision ID/body is retained when it exists.

## 14. WIN-006 analysis

WIN-006 is PARTIAL: exact revision IDs are enforced wherever revision_count indicates a revision, while historical birth rows explicitly record `REVISION_IDENTITY_UNAVAILABLE`.

## 15. Outcome revisions

A later B2 creates O2 through the existing immutable outcome revision service. O1 keeps B1/unavailable birth evidence and predecessor linkage remains explicit.

## 16. Cohort scope

Cohort admission freezes typed prediction, forward-outcome, and target/stop evidence members selected by the exact watermark, cutoff, outcome definition and eligibility policy.

## 17. Cohort refresh

The planner advances evidence once, admits one refresh, captures its generation, and keys the job by the canonical refresh ID.

## 18. Generation scope

Generation birth stores the same scope/refresh/plan plus model/configuration/watermark contract. Root manifest predictions must equal admitted predictions.

## 19. Generation continuation

Generation slices use the existing captured generation checkpoint and manifest; a later matured P4 cannot enter G1 and may enter G2.

## 20. Publication target binding

Publication remains bound to reviewed G1 IDs/keys/hashes. T15C authority is copied to the publication request; retry cannot resolve `latest` or switch to G2.

## 21. Scheduler semantics

The primary H5 scheduler creates one deterministic completed-session admission. Scheduling time is an observation cutoff, not the scope identity.

## 22. Idempotency

Idempotency resolves operation type plus refresh identity. Active duplicate triggers coalesce without changing the admitted target set; later cycles are not suppressed after terminal completion.

## 23. Legacy Winner data

Nullable historical Winner bindings mean `LEGACY_UNKNOWN`. Migration 0083 is additive and performs no production rewrite or historical scope fabrication.

## 24. Concurrency

PostgreSQL advisory admission locking and unique operational keys prevent divergent concurrent maturation. Content-addressed scope/refresh rows converge for identical cohort admission.

## 25. Restart behavior

Durable rows retain scope/refresh/plan, configuration anchor and operation cutoff across restart. New due predictions and later configuration do not alter R1.

## 26. Performance

Member persistence and retrieval are set-based. Inherited T15A bounds cover 1/50/200 members; Winner admission and remainder queries are bulk operations.

## 27. PostgreSQL certification

Fresh disposable PostgreSQL 18 was upgraded through 0083, downgraded to 0082, and re-upgraded. T15A/T15B migration suites, T15C R1/R2 attack, concurrency, and native price-revision scenarios pass.

## 28. Business parity

No Winner formula, threshold, eligibility rule, horizon, target/stop policy or serving calculation changed. T15C changes selection identity, durable binding and truth disclosure only.

## 29. Finding reconciliation

WIN-008 CLOSED. WIN-006 PARTIAL. XINT-012 CLOSED_FOR_PIPELINE_CERI_WINNER and PARTIAL_OVERALL. INV-SCOPE-001 and INV-REFRESH-001 close for Winner but remain PARTIAL_REPOSITORY_WIDE; INV-REVISION-001 is PARTIAL.

## 30. T15D handoff impact

T15D retains 7 exact operation families plus provider/source provenance and Core/CERI/Ranking algorithm findings. It does not inherit Winner target-scope freezing.

## 31. Residual risks

Residual risk is historical birth PriceBar evidence without a revision-row primary key and the seven T15D-assigned scope/provenance families. No current T15C scope defect remains.

## 32. Final verdict

T15C VERDICT: PASS. Unreconciled assigned families: 0. T15C scope/refresh defects: 0. WIN-008: CLOSED. WIN-006: PARTIAL with explicit unavailable lineage.

## Certificate summary

- Assigned operation families: 15
- Unreconciled operation families: 0
- Scope/refresh defects: 0
- Migration required: YES; additive only; no legacy backfill.
