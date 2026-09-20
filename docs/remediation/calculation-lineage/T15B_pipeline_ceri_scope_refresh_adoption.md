# T15B Pipeline and CERI Scope / Refresh / Acquisition Adoption

## 1. Executive verdict

PASS. T15B adopts the canonical T15A `WorkScopeIdentity`, `RefreshCycleIdentity`, and
`AcquisitionPlanIdentity` for all 19 assigned Pipeline, CERI, IB-fetch, and prewarm operation
families. Assigned defects and unreconciled families are both zero. No historical semantic state was
fabricated and no production data was rewritten.

## 2. Baselines

- Original audited baseline: `3a9d47063be996908b7d5d1cc5769e0bbd033546`
- Phase-5 certification: T14E at `f587b63e4e35e81486e53ffa0f13b9cd7369f963`
- T15A: `8188f1ac3e7e3a8f93ff75c2cf10019d9c5935f8`
- T15B started at T15A HEAD on branch `codex/t15b-pipeline-ceri-scope-refresh-adoption`
- Starting migration head: `0081_scope_refresh_identity`
- Final migration head: `0082_pipeline_ceri_scope`
- Starting source tree hash: `ef08b3b930f89de016213d28b7677b51c6260036`
- T15A validation / handoff / inventory / matrix / report SHA-256:
  `9F04B02764210BBB09BCDFA8582584E5827904FAF4AF8152FD193F73BD393080`,
  `54E4E702FC9EDDC1B38A4B59ADA60E4691FBE9A7DCB687382CDF5894B1BF5702`,
  `CADDC1A79B84BD1FD81AB131FDFE062188FE94D9AE88C45757C261F38CD2C5E4`,
  `D5438BCD3DFA3D432EE82A67EF4933C49CCCAC0BFBC35458996DE386F8C0E92A`,
  `A53C563661E4883F77BD190FD97689E73705894EB37F9EAD85338FDC6A8525E0`.

## 3. T15A handoff reconciliation

The deterministic [T15B operation reconciliation](T15B_operation_reconciliation.csv) selects the
exact records whose `phase6_target_task` is T15B from the T15A inventory. It contains all 19 assigned
IDs, their former behavior, issue flags, entrypoint, and required adoption. Reconciled: 19;
unreconciled: 0. The checked generator fails if the count, uniqueness, or domain classification
changes.

## 4. Pipeline root scope

`start_pipeline` freezes the ordered, normalized tickers from the admitted upload after creation of
the immutable market calculation context and before durable job enqueue. The root `PipelineRun` and
`FULL_PIPELINE` job are bound to the same three canonical identities in one transaction.

## 5. Stage scopes

Stage subsets use a new frozen child scope with `parent_scope_id` rather than altering root
membership. Market-data work freezes the exact fetch-plan subjects and binds the resulting
`IBFetchRun` and job. SEC readiness repair inherits the root authority and is explicitly required for
parent completion.

## 6. Retry semantics

A same-semantic retry retains S/R/P and may only change execution ownership, lease, worker, or retry
count. Durable IB work restores the admitted serialized plan; it does not recompute current coverage.
The database trigger rejects reassignment of any non-null authority reference.

## 7. Resume semantics

Pipeline and IB resume require complete retained authority. Pipeline membership is loaded from
`work_scope_members`; failed IB items are selected within the retained plan. An interrupted pipeline
that predates the child bindings may deterministically re-admit child authority only from its exact
retained immutable fetch plan and retained root scope. Otherwise missing authority is
`LEGACY_UNKNOWN_SEMANTIC_AUTHORITY` and fails closed rather than querying current state.

## 8. Continuation semantics

Continuation computes `persisted members - completed members`. The `[A,B,C]`, complete `A`, newly
eligible `D` attack returns only `[B,C]`. An explicit later admission may create `[A,B,C,D]` under a
new scope/refresh/plan.

## 9. Zero-progress behavior

Non-empty remainder plus zero processed members yields `STOP_ZERO_PROGRESS`. The durable job becomes
`BLOCKED`; reason, scope, count, and remaining members are persisted. No equivalent continuation job
is generated automatically.

## 10. Parent/child accounting

`required_for_parent_completion` is explicit on durable children. The scope-aware counts expose
planned, completed, failed, cancelled, and remaining children. Parent completion calls the terminal
gate and cannot report complete while any required child is active.

## 11. Stale recovery

Recovery changes execution token/lease only. The same scope, refresh, plan, cutoff, configuration,
and retained payload remain bound. PostgreSQL single-assignment triggers protect Pipeline, background
job, IB fetch, CERI ingestion, and CERI processing owners.

## 12. CERI refresh identity

CERI uses an explicit cycle key when supplied by a scheduler/operator; otherwise it derives a stable
provider/dataset/ticker/business-session key. A retry of R1 converges on R1. A legitimate later cycle
R2 creates a different `RefreshCycleIdentity` without random timestamp entropy.

## 13. CERI scope identity

Provider ingestion freezes the admitted ticker. Backfill freezes the resolved ticker set once before
iteration; wildcard selection is resolved at admission. Provider records discovered for a member are
evidence for that member and do not silently expand the work scope.

## 14. CERI idempotency

The former key identified only provider/dataset/ticker and could suppress a later valid observation.
The new request key adds the canonical refresh ID. Same-source/same-revision/same-refresh is
idempotent; a stable provider key in R2 can create a new observation or superseding source revision.

## 15. CERI continuation

Provider, normalize, feature, capture, change, alert, SEC-repair, and backfill children carry explicit
authority references. Checkpoints remain within their originating scope and refresh. Zero progress is
blocked rather than recursively re-enqueued.

## 16. Acquisition plans

Every assigned acquisition path persists subjects, source/provider class, request type, cutoff,
window, configuration/policy identity, and requirements in the canonical immutable T15A plan. Secrets
are excluded.

## 17. Plan retries

Timeouts, crashes, lease loss, and temporary provider failures retry P1. Job payloads carry immutable
identity references and the exact retained FetchPlan where existing executor architecture requires
the item detail.

## 18. Replanning

Changing membership, source policy, range, or requirements creates P2. P2 records P1 as predecessor
and a typed reason such as `POLICY_CHANGE`; P1 remains immutable. A provider returning only A/C from
a request for A/B/C records B missing but does not redefine P1.

## 19. Scheduler semantics

A scheduler retry uses its existing R1 authority. A later scheduled business cycle admits a new R2
and, where semantics changed, a new scope/plan. Clock time alone is not used as an idempotency escape.

## 20. Checkpoint semantics

Checkpoint keys and owners include scope/refresh authority. PostgreSQL certification creates
overlapping S1/R1 and S2/R2 checkpoints and proves S1 completion does not advance S2.

## 21. Truth/revision bindings

CERI source truth retains row ID, provider record ID, content hash, timestamps, and supersession. The
ingestion run now also binds the source observation to scope/refresh/plan. Exact downstream provider,
currency, publication, and revision proof not already available remains explicitly T15D scope;
Winner birth-price revision adoption remains T15C.

## 22. Legacy behavior

New binding columns are nullable solely to preserve readable historical rows. There is no bulk
backfill. An old in-flight operation without complete authority is `LEGACY_UNKNOWN` and cannot be
resumed or executed by reconstructing today's population. The sole compatibility path is
deterministic child-authority admission from an already retained exact immutable plan plus retained
root scope; it never consults current coverage.

## 23. Concurrency

Content-addressed, conflict-safe persistence makes concurrent identical plan/scope/refresh admissions
converge on the same IDs. Concurrent genuinely different cycle keys remain different refreshes. Both
properties are tested on PostgreSQL with independent sessions.

## 24. PostgreSQL certification

A disposable PostgreSQL 18 instance ran migration upgrade and schema/model drift checking. The T15B
integration scenario passed foreign-key binding, single-assignment trigger rejection, continuation,
zero progress, required children, retry/replan, checkpoint isolation, CERI R1/R2, legacy failure,
and concurrency.

## 25. End-to-end interruption scenario

The synthetic scenario admits S1 `[A,B,C]`, R1, P1; completes A; changes current eligibility/policy
to add D; and recovers under a new execution token. S1/R1/P1 remain unchanged and continuation is
`[B,C]`. Explicit R2 under policy v2 admits `[A,B,C,D]` and creates P2 with P1 lineage.

## 26. CERI performance

Authority member writes and reads are set-based. The certified T14D bounds remain capture 29/29,
pipeline 70/70, and material 29/29 for populations 1/50. T15B adds no per-member identity SELECT
loop.

## 27. Business parity

Valid current operations preserve calculation and provider-result behavior. Intended changes are
scope freezing, later-refresh admission, honest child completion timing, and fail-closed legacy
resume. No unexpected business-result change was observed.

## 28. Phase-5 regression

T15B uses the existing durable job mutation paths and execution ownership. It creates no unfenced
writer. Phase-5 mutation inventory and semantic-family review remain authoritative after refreshing
source pins for the reviewed implementation files.

## 29. Finding reconciliation

- `PIPE-001`: CLOSED — required semantic children gate parent completion.
- `PIPE-004`: CLOSED — reclaim retains S/R/P and zero progress cannot self-loop.
- `PIPE-005`: CLOSED — assigned fetch/prewarm retries preserve P1; replans create P2 lineage.
- `CERI-012`: CLOSED — same-cycle retry coalesces and later refresh is not suppressed.
- `XINT-007`: CLOSED_FOR_T15B_SOURCE_BINDING / PARTIAL_OVERALL — exact downstream provenance stays T15D.
- `XINT-012`: CLOSED_FOR_PIPELINE_CERI / PARTIAL_OVERALL — Winner adoption remains T15C.
- `INV-SCOPE-001`: ENFORCED_FOR_PIPELINE_CERI / PARTIAL_REPOSITORY_WIDE.
- `INV-REFRESH-001`: ENFORCED_FOR_PIPELINE_CERI / PARTIAL_REPOSITORY_WIDE.

## 30. T15C handoff impact

T15C can directly reuse `admit_frozen_operation`, retained-member remainder calculation,
zero-progress blocking, scope-aware checkpoint ownership, and typed P1→P2 lineage. No Winner
operation was moved into T15B.

## 31. T15D handoff impact

T15D receives CERI source observations already bound to scope/refresh/plan. It still owns the
remaining provider/currency/publication/revision provenance and algorithm-specific findings; T15B
does not claim those closures.

## 32. Residual risks

Repository-wide scope and refresh invariants remain partial until T15C/T15D adoption. Historical
legacy work remains intentionally non-certifiable. Operational monitoring should treat blocked
zero-progress work as requiring explicit retry/backoff or manual resolution.

## 33. Final verdict

PASS. Nineteen of nineteen assigned operation families are certified adopted, with zero assigned
defects and zero unreconciled families. The migration is additive, the new head is single, the
PostgreSQL and deterministic artifact gates pass, and no production/runtime mutation occurred.
