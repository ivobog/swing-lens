# Winner cohort concurrency and combined release recertification

**Overall task verdict: FAIL — fresh canary source not available.** The combined
implementation and release test gates passed; `READY_FOR_FINAL_CANARY_RERUN` is
**NO** because the newest identified local 100-row screener export is the old
September 11 source. The September 17 export has 86 rows. Neither is a newly
captured valid 100-row upload source. No old upload, run 162, pipeline 153, or
authoritative business evidence was changed, and no new pipeline was submitted.

## Committed source and safety

- Branch: `codex/replacement-canary-marketdata-winner-remediation`; starting
  HEAD `d1e913e06344c63d25ff0a514b950359dadfb463`; certified application
  ancestor `4a9f414ceebbc8bde0332a4a4730e28f6b229015`.
- The uncommitted replacement-canary delta was preserved before editing in
  `C:/Users/Ivica/Documents/SwingLens-remediation-safety-20260924`: binary
  patch SHA-256 `7411EEC35FF8C05AF08EEEEE046358B418918D6A061057AE3AAACA09B57AFC86`,
  file hashes, 27 exact file copies, and reverse-apply verification. The other
  26 copied files still matched exactly before the first commit; the shared
  inventory test was staged from its exact saved version for that commit.
- Replacement remediation: `d1eb69f0d346f726b5da630c18c9877e5ae214a0`
  (`fix: harden market-data acquisition failure handling`).
- Winner concurrency and config-anchor coordination:
  `e59906bfd17b1b6db9445fa79eefe6975f842310`
  (`fix: reconcile concurrent winner cohort refresh`).
- Canonical V2 committed application/test fingerprint at the implementation
  commit: `ed2f64d05a3a669e676021498368cf9c9fda8d65153b028730c64c62f0056f40`
  (680 implementation and 463 test entries). This is not the older
  `4558eda...` fingerprint. This report is a later documentation-only artifact;
  the implementation commit is the tested candidate source identity.

## Independent blocker and exact failure

Full node:
`tests/integration/test_winner_jobs_reliability_postgresql.py::test_concurrent_refresh_requests_coalesce_without_losing_desired_state`.
It failed repeatedly on the uncommitted combined tree and also in an isolated
disposable PostgreSQL 18 worktree at clean starting commit `d1e913e...`, proving
independence from the replacement-canary remediation. That temporary worktree
was removed after reproduction. The original full gate was 4,093 passed, 10
skipped, one failed. The exact exception was
`SemanticAuthorityError: SEMANTIC_AUTHORITY_REBIND_FORBIDDEN:scope_id` at
`app/services/scope_refresh_adoption.py:130`, reached from
`CohortRefreshPlanner.request_for_current_evidence` through
`CohortGenerationService.capture_or_resume` and `bind_semantic_authority`.
The protected object was the persisted generation's immutable `scope_id`, not
the mutable `WinnerCohortRefreshState.desired_*` intent.

Primary classification: **SCOPE_IDENTITY_DEFECT**, a real product identity
defect exposed by concurrent requests, not an invalid immutability test.
Both requests had the same config hash
`8d5a4438e586808569b7bb3f6de935c47698f57f06ed3072dcb78e7e2840c8cb`,
desired watermark hash
`e20e81e4255b91b94a6656f8d892ecd07b6cd02f3d23497df3f78c5c30f01aa5`,
three frozen members, membership fingerprint
`306f7349c0c4dfaffde23a112816428a9884afcdad5fcfeee071ba3918eed0dd`,
plan ID `7456afcc18be5c27cb8229fbf020e57080e5f0035b1278f897be601328059ba4`,
and generation key
`ad9a92a495fae5728c826c14e4b8307b7bfff56d902a8c3823b48cc88e74ed93`.

| Time | Request A | Request B |
| --- | --- | --- |
| T0 | Both declare the same `2026-08-17T12:00:00Z` observation and material watermark. | Same contract, config, members, and desired intent. |
| T1 | Acquires the contract-state `SELECT FOR UPDATE`; freezes `training_cutoff_at=2026-08-17T12:00:00.000001+00:00`, scope `ddf5322ab743eac902ba9eea1b988e2c4c19937775c88a23ec6fe9827be244ee`, refresh `75a368676e00c3fa47923e30abbac3bdcb4d9b89e8bfda89fa09a91435551f69`, generation row 1. | Waits on the same PostgreSQL row lock. |
| T2 | Commits scope, refresh, plan, generation, and one job. | Reloads the same `state.updated_at` instant in the session's Europe/Berlin spelling, `2026-08-17T14:00:00+02:00`. |
| T3 | Immutable owner remains scope `ddf532...`. | Old code freezes `2026-08-17T14:00:00.000001+02:00`, producing scope `03d54bb17ee06c97041910cc4dfccd4e9b79ea1e53adbda0096ffa11ee1c34e3` and refresh `a90227ac27cb852ced3b1de20489726c8fa24f3fa40fbbb513ec03a488421e9b`; the unchanged generation key finds row 1, whose immutable scope cannot be rebound. B rolls back with the exact exception. |

The fix freezes `state.updated_at.astimezone(UTC)` and derives both the cohort
training cutoff and acquisition business cutoff from that material instant,
not from the later request clock or a timezone-specific rendering. The
generation immutability guard was not weakened. Equivalent requests now share
scope, refresh, plan, generation, and job; the second reports coalescing.
`WinnerCohortRefreshState` remains mutable operational desired-state intent.
A newer material revision produces a separate frozen scope/refresh/generation
while advancing the desired hash, leaving the predecessor's payload and
membership fingerprint unchanged. A different config also yields distinct
semantic authority even if its evidence watermark is the same.

The incompatible-config attack additionally exposed a real PostgreSQL
deadlock at `effective_configuration_records` during simultaneous mixed
shared/config-specific multirow inserts. Stable input ordering alone did not
prevent it. `persist_configuration_anchor` now takes one transaction-scoped
PostgreSQL advisory lock before those index writes; SQLite tests bypass that
PostgreSQL-only operation. Three repeated incompatible-config attacks passed.
No process-local mutex or migration was introduced. The existing row lock,
unique generation key, and unique active-job request key coordinate identical
admission across processes; a two-process attack passed three repeated runs.

A unit-before-integration run exposed a separate test-isolation leak: the
simulated-capture fixture could import `outcome_authority` for the first time
while `validate_prediction_source` was temporarily mocked, permanently
binding that mock through a `from` import. Importing the downstream module
before patching fixes only the fixture; 535 unit-before-integration tests and
the full repository then passed. Production capture validation remains intact.

## Findings and current-source derivative

`REL-WIN-003` is **CLOSED**. The prior replacement findings remain:
`REL-MD-001 CLOSED`, `REL-MD-002 CLOSED`, `REL-WIN-002 CLOSED`, and
`REL-IB-002 EXTERNAL_BOUNDARY_CERTIFIED`. DOCN's frozen post-qualification
duration, CLBK/OKE typed provider/contract boundary, deterministic retry and
successful-item reuse, zero-progress guard, and legacy-vs-native Winner
obligation separation all passed focused and full tests. The explicit
legacy-obligation-plus-concurrent-native refresh test left the legacy row
unchanged, fabricated no native capture proof, and coalesced the exact native
scope. Publication monotonicity/idempotence and T15C frozen-scope/refresh
properties remained covered by the 566-test Winner lane and full suite.

The historical Phase-5/6/7 certificates and the original 63 CLOSED / 7 PARTIAL
/ 0 OPEN finding snapshot were not rewritten. The historical source-pinned
Phase-5 derivative correctly fails closed on eight changed reviewed source
paths. A current-source derivative comparison at the tested commit maps all
252 callers to the same 220 semantic families: zero incomplete families, zero
final-disposition changes, zero writer/authority contract mismatches, zero
stable semantic-family-ID mismatches. Nine source-proof family IDs evolved
relative to the last derivative, all with unchanged semantic IDs and reviewed
changed source. The separate Phase certificate lane passed 199/199: Phase 5
95, Phase 6 33, Phase 7 71. This is current-source revalidation, not a claim
that a historical pin already covered new bytes.

## Test and operational gate

- Exact identical concurrency and cross-process nodes: three consecutive
  paired PostgreSQL runs, all passed; incompatible-config attack: three
  consecutive PostgreSQL passes. Differing material membership, legacy/native,
  clock/timezone retry, and immutable predecessor tests passed.
- Winner reliability module: 24/24 normal and 24/24 explicit reverse order.
  Broad Winner capture/obligation/maturation/cohort/scope lane: 566/566.
- Market-data, deterministic retry, transient retry, progress/reuse lane:
  131/131. T14A static inventory: 20/20. Configuration delivery and domain
  fence: 27/28 before the SQLite dialect guard, then the exact SQLite and
  concurrent PostgreSQL nodes 2/2; the final whole-repository run covers the
  corrected source without failure.
- Full non-E2E on the committed implementation: **4,068 passed, 10 skipped,
  31 E2E-marked deselected, zero failed/errors**, one uninterrupted run in
  1:19:39. E2E-marked lane: **31 passed**, including the established 28 and
  three additional workflows. Phase-5/6/7 certificate lane: **199 passed**.
- `ruff check app tests scripts`, changed-file `ruff format --check`,
  `compileall app tests scripts`, and Git diff checks passed. The known
  unrelated repository-wide legacy lint debt was not mass-reformatted.
- Read-only authoritative verification: PostgreSQL 18.3 `swinglens`, Alembic
  `0083_winner_scope_truth`, maximum pipeline ID 153, pipeline 153 still
  `FAILED/FETCHING_MARKET_DATA`. No migration or authoritative write.

## Next-canary source and read-only IB preflight

No new upload-ready source was fabricated. The September 11 retained export
(`data/uploads/20260911_101959_f626ad5b_money money_2026-09-11.csv`, SHA-256
`b467f00f618116cf2d65b862756ca548b3f80dc5711cd1da637d1ec78f4adbdc`)
was used **only as the established reference universe**, not reused as the
next canary upload. Its 100 distinct symbols were qualified read-only against
IB Gateway, with production force-refresh plan windows and three-second
historical request pacing. All 100 qualified. TRADES returned bars for 99;
ADJUSTED_LAST returned bars for 99. CLBK's newly qualified conId 902968711
returned IB code 162 / `HISTORICAL_DATA_UNAVAILABLE` for both feeds, so it is
retained as typed insufficient. DOCN succeeded for both feeds with its planned
3-year duration. OKE succeeded for both feeds on its live qualified identity.
CLBK and OKE live fingerprints differ from retained old plan fingerprints;
new admission must freeze new contracts, never mutate pipeline 153's plan.

`FINAL_CANARY_CANDIDATE_PREFLIGHT.csv` has all 100 symbols and the requested
12 columns. Its local Windows worktree SHA-256 is
`AE104150742AAF5AFE802E1AD0F53B8B38C5F9E2D9AE67AEBD4F2A5AEDD3DA9A`;
Git's LF-normalized committed blob SHA-256 is
`6BDB5D2B75A15413A826CBC9BE607A62B31D14604ED746292FE0CA05EC38CB2D`.
Every row's candidate semantics begins `REFERENCE_ONLY`. The adjacent raw
JSON retains request parameters and provider codes. A fresh, valid 100-row
screener export must still be captured, hashed, and checked against the same
source/IB contract before any upload or final live canary. Therefore
**READY_FOR_FINAL_CANARY_RERUN: NO**. No push was performed.
