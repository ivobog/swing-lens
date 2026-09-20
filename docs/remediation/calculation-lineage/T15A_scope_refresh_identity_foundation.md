# T15A Scope / Refresh / Acquisition Identity Foundation

## 1. Executive verdict

T15A implements the shared Phase-6 identity foundation without broadly adopting it in Pipeline,
CERI, Winner, or other consumers. Typed deterministic work-scope, refresh-cycle, and acquisition-plan
identities now exist with immutable additive persistence, exact membership rows, explicit plan revision
lineage, continuation/zero-progress rules, parent/child accounting, and focused adversarial tests.

Final certification is recorded in `T15A_validation_summary.json`. Finding closure is intentionally not
claimed: domain adoption belongs to T15B/T15C/T15D.

## 2. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase 2 | `7b015124807b8f895911d7e790fcbf01949ff85f` |
| Phase 3 | `5422bcdf7703db891810d9e9c20a8f1770241fc9` |
| Phase 4 | `d3157c8642c119a7e5d7a84c69f4a58c6d3866d8` |
| T14A | `5eef0d08d783de132fecb14c5dedd66821f0949f` |
| T14B | `d56fe385e29b7b6233d2df5031a40a576db0ca7f` |
| T14C | `8f8b09d0b30cd0dee54328e7ad18619b09ed2a94` |
| T14D | `9d4b2dba6829c8c41e8c56d563b06b3d7d065364` |
| T14E / T15A start | `f587b63e4e35e81486e53ffa0f13b9cd7369f963` |
| T14E frozen source | `f273e9a61b0a363d873b9f58cf0d702c976f5361a29eb56c2e480fa61b20a5d7` |
| Initial branch | `codex/t14e-phase5-integration-certification` |
| T15A branch | `codex/t15a-scope-refresh-identity-foundation` |
| Initial worktree | clean |
| Initial migration head | `0080_effective_configuration` |
| T15A migration head | `0081_scope_refresh_identity` |
| Python | 3.12.2 |
| PostgreSQL | Docker available; disposable PostgreSQL 18 used on `127.0.0.1:55432` |
| T14E report SHA-256 | `233829176ba4d2721b723c971bd93877b5cf467d109d6ed9f0f38660b24f275a` |

No authoritative SwingLens database was accessed.

## 3. Phase-6 problem definition

Phases 0-5 establish calculation/evidence/configuration/mutation authority. They do not fully identify
the intended population, a logical observation cycle, or a provider acquisition plan. Phase 6 adds
those separate semantics so retry cannot become refresh, continuation cannot silently reselect, and
stable idempotency cannot prohibit a later legitimate observation.

## 4. T14E finding snapshot

The authoritative snapshot is `T14E_finding_status_after_phase5.csv`: 39 CLOSED, 18 PARTIAL, and 13
OPEN findings. T15A uses it directly. Phase-7 findings (`CORE-009`, `SETUP-004`, `SETUP-006`,
`SETUP-007`, `SETUP-010`) and the external-SQL governance remainder of `XINT-010` are not pulled into
Phase 6.

## 5. Calculation vs scope identity

Calculation identity says which calculation semantics apply. Work-scope identity says which exact
subjects receive those semantics. `WorkScopeSnapshot` may bind calculation and effective-configuration
fingerprints but does not replace either. Execution tokens, worker attempts, heartbeats, queue times,
and retry counters cannot be stored in the typed semantic identity.

## 6. Scope model

`WorkScopeSnapshot` freezes scope kind, subject kind, definition/schema version, canonical definition,
selection-policy identity, membership policy, selection time, business cutoff, applicable calculation
and configuration identities, parent scope, acquisition plan, and exact evaluated members. Its SHA-256
identity is content-addressed.

`WorkScopeIdentity.legacy_unknown()` represents history whose exact population was not retained. It
cannot be accepted as certified scope evidence.

## 7. Membership model

Membership uses normalized typed `(subject_type, subject_id)` pairs. It is sorted before hashing;
duplicates after normalization are rejected; null/control values are rejected. Exact membership is
persisted in `work_scope_members` with deterministic ordinal and common membership fingerprint.
Ordering attacks, add/remove attacks, duplicate attacks, and 1/50/200-member cases pass.

## 8. Frozen scope

`FROZEN` membership is exact and immutable. Retry/resume keeps the same scope ID. Continuation is
computed from the retained member set minus completed members, never by rerunning the original live
selection query.

## 9. Dynamic scope

`DECLARED_DYNAMIC` requires a frozen `policy_id`, `policy_version`, and `evaluation_rule`. Each
evaluation still has an exact membership fingerprint. A future reevaluation needs an explicit reason
and is distinguishable from an equivalent continuation. No selected production family is currently
certified as declared dynamic; this is a zero count, not an unknown classification.

## 10. Continuation model

`ScopeContinuationState` validates completed members as a subset of the exact scope and deterministically
returns the remaining members. A frozen `[A,B,C]` scope with `A` complete returns `[B,C]`; a later live
row `D` is absent.

If a continuation processes zero members while frozen members remain, the decision is
`STOP_ZERO_PROGRESS`, never another equivalent continuation. Dynamic reevaluation is allowed only for
a declared dynamic scope with an explicit reevaluation reason.

## 11. Parent/child accounting

`ParentChildAccounting` records every semantic child target as planned, created, completed, failed, or
cancelled and exposes bounded counts. Parent completion occurs only when every planned child is
complete. Failure and cancellation are explicit terminal states; a returned parent handler does not
imply semantic completion.

## 12. Refresh-cycle model

`RefreshCycleIdentity` binds refresh kind, scope, observation-cycle key, business cutoff, reason,
configuration, provider/source policy, calculation identity, and predecessor. The observation-cycle
key is declared business identity, not a queue/worker attempt.

## 13. Retry vs refresh

`retry()` retains the exact refresh object and fingerprint. `next_refresh()` requires new cycle input,
links the predecessor, and produces a different digest. The attack `S/R1/A1 -> S/R1/A2 -> S/R2`
passes: execution attempt is outside the semantic hash, while `R2 != R1`.

## 14. Idempotency semantics

Content-addressed persistence makes the same scope/plan/cycle idempotent. It does not globally block a
subject. A later refresh is keyed by its new refresh-cycle identity. Domain request keys must include or
bind that identity during adoption.

## 15. Acquisition-plan identity

`AcquisitionPlanSnapshot` identifies exact subjects, provider/source class, request type, cutoff,
window, configuration/policy, required/optional observations, plan version, and revision key. Transport
retry metadata is excluded.

## 16. Acquisition replanning

`revised()` creates a different plan linked to the immutable predecessor with a typed reason. Supported
foundation reasons are provider limitation, missing source, newly discovered source, explicit operator
replan, and policy change. Retrying the same provider call keeps the same object and identity.

## 17. Truth/revision model

`PriceBarRevision` is strong for changed bars, CERI source records are strong for arrived provider
records, and Winner evidence manifests are strong for realized outcome populations. Exact revision
coverage is nevertheless incomplete at several consumption boundaries, especially Winner birth bars.
The machine handoff contains the field-level inventory.

## 18. Provider provenance boundary

CERI source records retain provider record ID, content/normalized hashes, supersession, and multiple
business/observation times. Derived consumers do not uniformly prove provider revision, currency/scale,
publication state, and exact source reference together. `CORE-005`, `CERI-002`, `CERI-007`, `WIN-006`,
and the applicable background-scope portion of `XINT-007` remain visible.

## 19. Legacy semantics

The migration is additive. There is no production rewrite and no legacy backfill. Existing rows are not
assigned synthetic scope, refresh, or plan IDs. Missing history remains `LEGACY_UNKNOWN`/uncertified.

## 20. Relevant operation-family inventory

The deterministic inventory starts from the 220 T14D-certified families and selects only concrete
families involving population selection/iteration, refresh, retry/resume/continuation, acquisition,
maturation, cohort/generation, or truth revision. It contains 41 families: 19 for T15B, 15 for T15C,
and 7 for T15D. Seven already have a frozen/remainder model; zero are certified dynamic; 40 lack a
shared scope identity, 41 lack shared refresh identity, 23 lack an acquisition-plan identity, and 36
have incomplete revision identity. Unknown scope models: zero.

## 21. Pipeline findings

- `PIPE-001`: inventoried/contract defined/open. Parent/child accounting exists but actual child jobs
  are not bound yet.
- `PIPE-004`: inventoried/contract defined/open. Resume/recovery can retain stage/config authority
  without an exact target population.
- `PIPE-005`: scope/plan contract defined/partial. In-memory IB plans exist, but immutable plan/replan
  identity is not adopted.

## 22. CERI findings

`CeriIngestionService.request_key()` currently returns
`ceri:{provider}:{dataset}:{ticker}`. `CeriIngestionRun.request_key` is globally unique and completed or
partial rows short-circuit later ingestion under that key. This key represents a logical provider
subject request, not a refresh cycle or provider revision. That exact mismatch underlies `CERI-012`
and can suppress legitimate refresh. T15B must bind request identity to refresh-cycle and acquisition
plan while retaining same-cycle retry idempotency. Algorithm-specific CERI defects remain T15D.

## 23. Winner findings

H5 maturation selects membership inside each `drain_due()` batch from the current pending/due query.
Processed IDs are local to one call. Retry can see newly due predictions; a continuation can admit new
rows; current status changes can remove rows. The existing zero-progress guard prevents one runaway
pattern but does not freeze the cycle population. T15C must select exact forward-outcome IDs before the
first batch and pass only the remainder to continuations.

Winner cohort generation is stronger: desired watermarks, generation keys, and exact evidence
manifests preserve realized population and outcome revisions. It still needs a shared refresh-cycle
binding. `WIN-006` remains partial because exact PriceBar revision IDs used at birth/outcome boundaries
are not uniformly retained. `WIN-008` remains open.

## 24. Other domain findings

Core/Ranking refresh routes and selected Setup repair/capture operations use live run/current queries
without independent population identity. They are assigned to T15D alongside their algorithm and
provenance work. Original-context Setup findings remain Phase 7.

## 25. Query/performance model

Scope persistence uses one record upsert, one batched member upsert, one record verification read, and
one set-based ordered member verification read. Tests at 1, 50, and 200 members prove a constant upper
statement bound and no member-by-member query loop.

## 26. PostgreSQL certification

Disposable PostgreSQL 18 ran migration upgrade/check, immutable trigger attacks, FK attacks,
content-addressed retry, new-refresh lineage, plan revision lineage, and query-count tests. Result: 5
passed. The container used port 55432 and no authoritative database.

## 27. Static/inventory certification

The generator validates every selected ID against the T14D family certificate, rejects duplicates,
requires explicit T15B/C/D disposition, and fails on unknown scope classification. Its source hashes
pin the T14D certificate and T14E finding snapshot. Determinism tests compare regenerated bytes to the
checked-in JSON/CSV artifacts.

## 28. Finding reconciliation

T15A does not force any of the 70 findings closed. It implements the shared foundations for
`INV-SCOPE-001..004`, `INV-REFRESH-001..002`, `INV-PLAN-001..002`, and `INV-REVISION-001`; enforcement
is partial until domain entrypoints adopt them. Phase-5 mutation authority remains unchanged.

## 29. T15B handoff

Adopt the new identities in Pipeline, IB acquisition, CERI acquisition/SEC repair, and related child
work. Bind root admission, children, retries, resumes, and continuations to retained scope/refresh/plan
IDs. Change CERI keys so same-cycle retry coalesces but a new refresh cannot be suppressed. Implement
explicit replan lineage. Primary findings: `PIPE-001`, `PIPE-004`, `PIPE-005`, `CERI-012`, `XINT-007`,
`XINT-012`.

## 30. T15C handoff

Freeze H5 maturation forward-outcome IDs at cycle creation; process only the remainder; make newly due
rows a new cycle. Bind cohort refresh/generation to refresh identity. Persist exact price revision IDs
for birth/outcome truth or an explicit unavailable/legacy state. Primary findings: `WIN-006`,
`WIN-008`.

## 31. T15D handoff

Complete remaining provider/currency/publication/revision provenance and algorithm-specific CERI,
Core, Ranking, and producer-readiness findings from the exact T14E Phase-6 snapshot. Adopt scope
identity for the seven selected non-Pipeline/non-Winner operation families. Do not claim scope identity
alone fixes financial formulas or algorithm defects.

## 32. Residual risks

Production consumers are not yet bound to the new tables. A privileged external SQL principal could
bypass application ORM guards, although PostgreSQL immutability triggers protect these identity
tables; broader external-SQL governance remains out of scope. Dynamic policy adoption requires domain
review. Existing historical operations remain uncertified rather than fabricated.

## 33. Final verdict

**PASS.** The final validation summary records 385 passing non-database tests, 80 distinct passing
PostgreSQL tests, migration/schema parity, deterministic artifact checks, changed-file Ruff/format,
compilation, whitespace, and secret-scan gates. The architecture is intentionally a foundation: exact
semantic identities and persistence now exist, while finding remediation is handed off without
overstating closure.
