# T09C — Winner Publication Fencing

## 1. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T09A commit | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| T09B commit | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` |
| T09C starting HEAD | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` |
| Branch | `codex/t09c-winner-publication-fencing` |
| Final HEAD | The focused T09C remediation commit; exact SHA is recorded in the final response |
| Migration head | `0074_ceri_evidence_quarantine` |
| Python | 3.12.2 from `.venv` |

HEAD exactly matched the required T09B base before editing. There was no tracked
implementation drift. The pre-existing untracked architecture/audit documents and IB
historical probe files were preserved and not staged.

## 2. Findings

- `WIN-007`: **CLOSED**. A compatible generation can publish only when its material
  evidence watermark is the current desired watermark and is strictly newer than the
  active generation. Older/incomparable candidates cannot move the pointer.
- `WIN-009`: **CLOSED**. Repeating publication of the active generation returns
  `ALREADY_ACTIVE` without changing lifecycle state, pointer state, or timestamps.
- `XINT-011`: **CLOSED within Winner**. Publication is atomic, complete, idempotent,
  and monotonic in the implemented service contract.
- `INV-PUBLICATION-001`: **ENFORCED within Winner**. Repository-wide pointer invariants
  outside Winner remain outside this task.

## 3. Existing publication architecture

Winner capture freezes immutable prediction feature values. Outcome maturation appends
versioned later truth. Cohort refresh freezes a contract and material evidence watermark,
creates a content-addressed generation, materializes root/per-cohort manifests and
generation-bound statistics, and validates temporal eligibility and complete group counts.

`WinnerCohortRefreshState` is the serving projection for one exact cohort contract. It owns
the desired evidence watermark plus `published_generation_id` and
`published_watermark_hash`. The automatic publisher is reached from cohort materialization
completion through `CohortGenerationService.publish`. The manual reviewed estimate
transition is reached through `WinnerEstimatePublicationService.publish`, normally from
`scripts/winner_estimate_publication.py`. Winner admin routes enqueue cohort refresh work;
they do not write the pointer directly. Rescore and serving paths read the published
generation and do not write its pointer.

## 4. Publication ordering contract

The semantic ordering key is the component-wise material evidence watermark:

```text
(
  forward_revision_id,
  target_stop_revision_id,
  eligibility_decision_id,
  training_replay_id,
  temporal_validity_decision_id,
)
```

Generation B is newer than compatible generation A only when every B component is greater
than or equal to the corresponding A component and at least one component is greater.
Equal components are the same evidence position. Mixed movement is incomparable and is
rejected. Row ID, `requested_at`, `started_at`, `ready_at`, `published_at`, completion time,
and process finish order never participate in semantic ordering.

Normal automatic publication also requires the candidate watermark to equal the locked
refresh state's current desired watermark. A build that finishes after desired evidence
advanced remains a valid complete `READY` generation but is rejected as stale and never
becomes temporarily active.

## 5. Compatibility contract

Two generations are comparable only when all actual persisted serving-contract dimensions
match:

```text
outcome_definition_id
feature_schema_version
calculation_version
config_hash
eligibility_policy_version
compatibility_policy_version
cohort_algorithm_version
```

The candidate must also match the locked refresh state on those fields. Generation key and
watermark hash are recomputed from the candidate's contract/watermark before normal
publication. Unknown watermark dimensions, malformed values, contract drift, divergent
watermarks, and content-identity mismatches are rejected as incompatible rather than being
ordered by timestamp or ID.

The pre-existing operator-reviewed clean-estimate transition is preserved as an explicitly
named reviewed supersession. Its reviewed manifest, exact previous-generation compare-and-
swap, candidate purity checks, actor, approval, and request ledger authorize the otherwise
incomparable transition. It does not silently infer cross-contract ordering.

## 6. Transactional publication fence

Normal publication performs the following in the caller's single transaction:

1. Apply the background lease guard.
2. `SELECT ... FOR UPDATE` the candidate's refresh-state pointer row.
3. `SELECT ... FOR UPDATE` the requested generation and current active generation.
4. Validate same-generation idempotency, contract/content identity, desired watermark,
   active watermark ordering, completeness, and temporal integrity.
5. Mark the former active generation `SUPERSEDED`, mark the candidate `PUBLISHED`, and set
   both pointer fields in one flush.
6. Apply the lease guard again; the caller commits the same transaction.

Concurrent normal publishers for one contract serialize on the refresh-state row. A loser
re-reads the committed pointer and cannot move it backward. Reviewed supersession locks all
involved refresh-state rows and generations in ID order, validates the exact expected
active generation, and delegates the lifecycle/pointer mutation to the same canonical
service.

No code sets the pointer to `NULL` during a same-state normal switch. A reviewed cross-state
transition may clear the old contract's pointer and set the new contract's pointer, but both
changes remain in one transaction. Any exception or failed commit rolls back all lifecycle,
estimate, request-ledger, and pointer changes.

## 7. Pointer-writer inventory

| Writer/path | Entry point | Before | Validation/transaction | After |
|---|---|---|---|---|
| Automatic cohort publication | `CohortMaterializationService.materialize_slice` → `CohortGenerationService.publish` | Atomic/complete, non-monotonic, duplicate retry unreachable | Locked pointer + generation; complete/temporal/content/contract/watermark checks; one transaction | `CANONICAL_MONOTONIC_PUBLICATION` |
| Background cohort refresh handler | `execute_cohort_refresh_job` → refresh/materialization | Automatic writer with cooperative lease checks | Same canonical publisher plus T09B session commit fence | `CANONICAL_MONOTONIC_PUBLICATION` |
| Manual reviewed estimate publication | `WinnerEstimatePublicationService.publish` | Direct generation status and pointer mutation | Reviewed hashes/purity/request ledger plus canonical reviewed supersession | `CANONICAL_MONOTONIC_PUBLICATION` (explicit reviewed supersession mode) |
| Publication operations script | `scripts/winner_estimate_publication.py` | Called reviewed service | Calls the same reviewed service; no SQL pointer update | `CANONICAL_MONOTONIC_PUBLICATION` |
| Winner admin cohort endpoint | `POST /api/winner-probability/cohorts/refresh` | Enqueues job | Queue-only | `READ_ONLY` with respect to pointer |
| Winner operations/status endpoints | operations and API services | Read operational/publication state | No pointer assignment | `READ_ONLY` |
| Latest rescore handler | `execute_latest_rescore_job` | Requires published generation | Writes generation-bound estimates only | `READ_ONLY` with respect to pointer |
| V2 serving | `WinnerProbabilityApiService` / `estimate_is_serving` | Resolves exact contract pointer | Query-only | `READ_ONLY` |
| Pre-1.1/legacy non-generation estimate activation | `Pre11L5ActivationService` and V2-disabled serving | Immediately published non-generation estimate; no generation pointer | Existing explicit legacy contract, outside active-generation publication | `LEGACY_NON_SERVING` with respect to generation pointer |
| Migrations/model declarations | Alembic `0049`, ORM fields | Schema definition only | No runtime publication | `READ_ONLY` / historical schema |

No `REMAINING_POINTER_BYPASS` was found. The only assignments to
`published_generation_id` or `published_watermark_hash` now reside in
`CohortGenerationService`.

## 8. Publication result semantics

| Result | Meaning | Semantic side effects |
|---|---|---|
| `PUBLISHED` | Complete, compatible, desired, strictly newer candidate became active | One lifecycle/pointer transition |
| `ALREADY_ACTIVE` | Requested generation is already the valid active generation | None |
| `REJECTED_STALE` | Candidate is behind desired or active compatible evidence | None |
| `REJECTED_INCOMPATIBLE` | Contract/content/watermark cannot be safely compared | None |
| `REJECTED_INCOMPLETE` | Required counts/root manifest/evidence metadata are incomplete or failed | None |

Automatic cohort results expose `publication_status`. A stale build requests continuation
when desired evidence has advanced. Reviewed publication retains its request-key result
ledger: a committed response-lost retry returns the stored result and does not repeat
estimate or generation publication.

Generation status means current serving lifecycle, not “was ever published”:
`READY` is complete but inactive, `PUBLISHED` is active for its pointer, and `SUPERSEDED`
is formerly active. Rejected candidates retain their prior status. `published_at` remains
the original activation time and is not rewritten on a retry; a former generation's
completion timestamp is no longer rewritten at supersession.

## 9. T09B compatibility

The background cohort path remains inside T09B's `fence_domain_commits` context. T09C keeps
the pre/post publication lease guard and does not commit through a raw connection or a new
thread. The focused positive control replaces the execution token before a background
publication commit: the SQLAlchemy `before_commit` fence raises `JobLeaseLost`, rollback
restores the candidate to `READY`, and the previous pointer state is unchanged.

Publication therefore requires both current worker ownership and semantic publication
eligibility. Content identity or idempotency never authorizes a stale worker.

## 10. Tests

| Required regression | Test evidence | Finding |
|---|---|---|
| First publication | `test_first_and_newer_publication_are_monotonic` | WIN-007 |
| Newer publication | same test | WIN-007 |
| Stale publication rejected | `test_older_generation_finishing_last_is_rejected_stale` | WIN-007/XINT-011 |
| Older finishes last | same test changes the old generation's `ready_at` to later | WIN-007 |
| Same-generation retry | `test_same_generation_retry_is_side_effect_free` | WIN-009 |
| Concurrent older/newer | `test_concurrent_adversarial_publishers_converge_to_newest` | WIN-007/XINT-011 |
| Incomplete candidate | `test_incomplete_and_incompatible_candidates_cannot_replace_active` | XINT-011 |
| Incompatible generation | same plus `test_divergent_watermarks_are_not_timestamp_ordered` | XINT-011 |
| Publication rollback | `test_publication_rollback_keeps_previous_active` | XINT-011 |
| Committed response lost | `test_committed_response_lost_retry_returns_already_active` | WIN-009 |
| Automatic path race | concurrent test invokes the real canonical automatic publisher | WIN-007 |
| Manual/admin race | `test_manual_reviewed_publication_rejects_pointer_drift` | WIN-007/XINT-011 |
| T09B ownership | `test_t09b_execution_token_fence_still_rejects_background_publication` | XINT-011 + T09B |

`test_reviewed_supersession_uses_canonical_pointer_switch` proves the retained manual
supersession path uses the canonical switch. `test_pointer_queries_compile_with_row_locks`
proves the PostgreSQL pointer statement includes `FOR UPDATE`.

## 11. Testing exclusions

- Live external/provider: `tests/ib_market_intelligence/test_external_smoke.py`, IBKR/IB
  Gateway, live SEC/provider tests, and the `external` marker.
- Browser/E2E: `tests/e2e/`, Playwright/Selenium/webapp integration flows, and `e2e`/`slow`
  markers.
- PostgreSQL-dependent: `tests/integration/` and disposable-PostgreSQL fixtures. No
  PostgreSQL container or credentials were started/repaired.

These are intentional T09C project-policy exclusions, not implementation failures.

## 12. Verification totals

### Focused Winner publication

```text
64 passed, 0 failed, 0 skipped, 0 deselected, 1 warning
```

### Winner subsystem

```text
283 passed, 0 failed, 0 skipped, 1 deselected, 1 warning
```

### Background compatibility

```text
98 passed, 0 failed, 0 skipped, 0 deselected, 1 warning
```

### Broader clean lane

```text
2413 passed, 0 failed, 7 skipped, 33 deselected, 22 warnings
```

The broad command was the T09B clean lane with the same ignores and marker exclusions.
Its first preflight run had one environment-only failure because this host's Windows
PowerShell 5.1 execution policy blocked `swinglens.ps1` before the test could reach the
intended version guard. The node passed in isolation and the full lane passed after setting
`ExecutionPolicy Bypass` for that test shell's process only. No persistent policy or
application change was made.

Warnings are the existing Starlette/httpx deprecation plus 21 Python 3.12 SQLite datetime
adapter deprecations. The seven skips are unavailable disposable-PostgreSQL fixtures.

Static verification: Ruff passed for all changed Python files and `git diff --check`
passed (apart from Git's line-ending notices).

## 13. Database impact

```text
migration required: NO
production rewrite required: NO
```

Existing contract, material-watermark, generation-key, completeness, lifecycle, timestamp,
and refresh-state pointer fields are sufficient. No migration, data backfill, fabricated
history, production database access, or runtime Winner mutation was performed.

## 14. Finding verdicts

```text
WIN-007: CLOSED
WIN-009: CLOSED
XINT-011: CLOSED within Winner
INV-PUBLICATION-001 within Winner: ENFORCED
```

## 15. T09C verdict

```text
T09C WINNER PUBLICATION FENCING: PASS
```

Winner publication logic is certified by deterministic unit/service/repository tests,
adversarial ordering, transaction rollback, actual automatic/manual call paths, SQL lock
compilation, and the broader clean lane. Full PostgreSQL concurrent publication
certification remains **DEFERRED BY PROJECT TEST POLICY** and is not claimed.

Residual risks are limited to that deferred PostgreSQL runtime exercise, future code that
bypasses the canonical service/raw ORM transaction contract, and intentionally out-of-scope
Phase 1+ calculation identity, immutable evidence, readiness, configuration, entry-point,
maturation-scope, refresh-identity, and reconstruction work.
