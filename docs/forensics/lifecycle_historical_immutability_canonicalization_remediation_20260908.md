# Lifecycle Historical Immutability & Canonicalization Forensic Remediation

Date: 2026-09-08  
Branch: `codex/lifecycle-historical-immutability-remediation`  
Baseline HEAD: `188566a4d596fa30f532c0f5e6d2b8dccd2ad40c`  
Code-and-test remediation HEAD before this evidence commit: `3151675bb46cc6ec4173149877792d5edbe12a3d`

## A. Executive verdict

```text
LIFECYCLE IMMUTABILITY ROOT CAUSE CERTIFIED: YES
CANONICALIZATION REMEDIATION CERTIFIED: YES
SAFE FOR CONTROLLED CANARY #3: YES
```

Root-cause classification: **C — partially valid administrative evolution, incorrectly modeled on immutable historical evidence.** The old design legitimately needed a current selection to advance, but stored that selection by demoting the prior snapshot in place. That made one row represent both what the run decided then and what the system considers current now. The five Canary #2 hash changes were administrative only, but they destroyed self-contained decision-time canonical meaning.

The remediation makes snapshots append-only decision evidence, retains `is_canonical`, `canonical_reason`, `canonicalized_at`, and `canonical_decision_json` as immutable canonical-at-decision facts, and moves the evolving current selection into `setup_signal_snapshot_current_selections`. Every runtime pointer transition also creates an append-only `setup_signal_snapshot_selection_events` record. Current APIs retain the `is_canonical` contract by deriving it from the pointer; historical run reconstruction does not consult that pointer.

The authorization for this task did not include deployment. Revision `0069_lifecycle_current_selection` was validated only on disposable PostgreSQL. A separate authorized deployment task must install revision 0069 before Controlled Canary #3.

## Baseline and safety envelope

| Item | Observed baseline |
|---|---|
| Git branch | `codex/session-temporal-integrity-canary-remediation` before creating the dedicated branch |
| Git HEAD | `188566a4d596fa30f532c0f5e6d2b8dccd2ad40c` |
| Working tree | clean |
| `188566a4...` | exact baseline HEAD; ancestor of remediation branch |
| `dfd0f8bf...` | ancestor |
| Alembic code head | `0068_market_calc_context` |
| Production Alembic head | `0068_market_calc_context` |
| Production DB identity | database `swinglens`; PostgreSQL 18.3; server `127.0.0.1/32:5432` |
| Application running | no |
| Worker running | no |
| Active jobs | 0 |
| Queued jobs | 0 |
| Baseline local time | `2026-09-08T19:23:43.2928253+02:00` |
| Baseline UTC time | `2026-09-08T17:23:43.2935215Z` |
| Baseline New York time | `2026-09-08T13:23:43.2991121-04:00` |

Run 148 was `COMPLETED` with five rows; Pipeline 141 was `PARTIAL`; Job 42862 was `COMPLETED` with retry/recovery counts 0/0. Run 149 was `COMPLETED` with five rows; Pipeline 142 was `PARTIAL`; Job 42876 was `COMPLETED` with retry/recovery counts 0/0. Both sets remain production evidence and were queried only inside explicit read-only transactions.

## B. Run 148 mutation reconstruction

The pre-Canary #2 dump `backups/session_temporal_canary_2_20260908/swinglens_pre_controlled_canary_2.dump` supplied the before image. An exhaustive comparison of every physical `setup_signal_snapshots` column found exactly two changes on each Run 148 row and no others.

| Snapshot ID | Ticker | Changed column | Old value | New value | Mutation path | Run causing mutation |
|---:|---|---|---|---|---|---:|
| 36934 | AAPL | `is_canonical` | `true` | `false` | `SetupLifecycleCanonicalizer.canonicalize_run` → `select_canonical_snapshot` → legacy `SetupLifecycleRepository.promote_canonical_snapshot` bulk update | 149 |
| 36934 | AAPL | `superseded_by_snapshot_id` | `NULL` | `36939` | same transaction/path | 149 |
| 36935 | AMGN | `is_canonical` | `true` | `false` | same | 149 |
| 36935 | AMGN | `superseded_by_snapshot_id` | `NULL` | `36940` | same | 149 |
| 36936 | AWK | `is_canonical` | `true` | `false` | same | 149 |
| 36936 | AWK | `superseded_by_snapshot_id` | `NULL` | `36941` | same | 149 |
| 36937 | CBOE | `is_canonical` | `true` | `false` | same | 149 |
| 36937 | CBOE | `superseded_by_snapshot_id` | `NULL` | `36942` | same | 149 |
| 36938 | DHT | `is_canonical` | `true` | `false` | same | 149 |
| 36938 | DHT | `superseded_by_snapshot_id` | `NULL` | `36943` | same | 149 |

The causing execution was Run 149 / Pipeline 142 / Job 42876 / lifecycle evaluation 257. Legacy `CANONICAL_REVISION` events 44507–44511 identify the five old/new pairs and were created at `2026-09-08 17:32:52.497738+02`. The successor rows were canonicalized between `17:32:53.700883+02` and `17:32:53.799874+02`. The snapshot table has no `updated_at`, so the exact old-row update timestamp is **not auditable**; it can only be bounded to that transaction. No timestamp was inferred or fabricated.

### Read-only before/after hashes and successors

Canary #2 computed `md5(to_jsonb(snapshot)::text)` over every physical column.

| Run 148 ID | Ticker | Pre-149 full-row MD5 | Current full-row MD5 | Run 149 successor | Successor full-row MD5 | Current pointer intended after 0069 |
|---:|---|---|---|---:|---|---:|
| 36934 | AAPL | `69f5bbca94cab12971f135019ac1c3f1` | `9985493c2c465c2eb92be5a222aca5df` | 36939 | `b7169206c29564b155ff098f81a87b8a` | 36939 |
| 36935 | AMGN | `8992fc1b66a0cc26b8cea0b70a6c1c31` | `7764b234b876e16d9f72148e5a99403c` | 36940 | `a619d6c307c16f25e0e825805e46df3d` | 36940 |
| 36936 | AWK | `2e36d1b837c031b0cfc41f86c10d8823` | `700407e985965cdfe7eb984a04752d8b` | 36941 | `bf43ee92578b4923848bcec7b0a8922e` | 36941 |
| 36937 | CBOE | `ad4e010ff08feb0007bde997f21c7815` | `4ce6d28bf3f4b3d50cb2e146cdfb7740` | 36942 | `1c271662801a2da36784c3d83bb31da1` | 36942 |
| 36938 | DHT | `635709f203340d16ba7ca2d9846d7a77` | `39f190f63fe96de65c808f56b2067a15` | 36943 | `2c177dab307bbd75ec9b587554087bd7` | 36943 |

All successors have calculation context 2, cutoff `2026-09-08 17:32:05.731953+02`, frozen input session `2026-09-04`, and calendar `swinglens-us-equities-v1`. Run 148's four context fields were null due to Canary #1 and remained byte-for-byte unchanged by Run 149. The remediation does not attempt to repair those rows.

| ID | Ticker | Source-data SHA-256 |
|---:|---|---|
| 36934 | AAPL | `c263c60c809ee0d24717a402ff45f09902b2eb40f595d158d6c7e9a91b2afd42` |
| 36935 | AMGN | `73584e14dad07a21f45071318f5c84a97ed7735fdb07383666119c2ae387b54b` |
| 36936 | AWK | `5e112cbab97e94fa7be6563a1cf53ec1dea03bc38a82b389fae385010fe458b8` |
| 36937 | CBOE | `af652148066773f042c34e0acd8b36201e6122bc86d87eaa54877febc90e3de1` |
| 36938 | DHT | `d059bd35c3e70d0d79d411dd4b035422c3f76dec694e87da64277761ac72d75b` |
| 36939 | AAPL | `89b31b0a3829a75bb638954fd887edbc512ca44eb49c7918e8c5813299338201` |
| 36940 | AMGN | `6a7d439e0f13ff94fd7186856b29097ac085ca484801d03ecaae9b6eb164a34f` |
| 36941 | AWK | `64afe005359d8afc2cb17cdbebb53c4fb0bf96ca16a536a821c1da2771a3c616` |
| 36942 | CBOE | `1f131c19bc6f46fb1fcdd4d29b0296a3a1f67304f75b2bd8e4e57feadf554119` |
| 36943 | DHT | `ac0437b55131f0fae9bee7cb88a95120a599fa7001f84bd630f03574021d8937` |

Under the corrected model, a clean Run 148 snapshot would retain its own `is_canonical=true` decision fact, Run 149 would append its own canonical decision, the current pointer would move to 36939–36943, and an event would record each old/new pointer transition. Because production had already lost the Run 148 flags before this task, migration bootstrap will only capture the deterministically observable current pointers (36939–36943); it will not invent Run 148 intervals or events.

## C. Hash analysis

The Canary #2 control hash included **all snapshot columns**, grouped as follows:

| Hash category | Included columns |
|---|---|
| Business/decision evidence | setup family/phase/state/actionability/quality/confidence; all fundamental, dual, trend, momentum, setup, risk, final and profile scores; technical classification/stage/pullback/action bias/combined decision/ranking; all price, pivot, trigger, stop, target, distance, risk/reward, crossing, coverage and freshness values; signals/features/warnings/missing-data/diagnostic/debug JSON |
| Temporal lineage | evaluation/run/source/raw/scoring/result/regime/sector/context IDs; ticker/timeframe/date/session/cutoff/calendar; origin; engine/config/source/schema versions and hashes; source lineage JSON |
| Administrative canonicalization state | `is_canonical`, `canonical_reason`, `canonicalized_at`, `superseded_by_snapshot_id`, `canonical_decision_json` |
| Operational metadata | physical `id` and foreign-key identifiers |
| Generic/audit timestamps | `calculated_at`, `captured_at`, `canonicalized_at` |

Result: **B — only administrative status changed.** It was still a real immutability failure because the old `is_canonical` value was also the only row-local record that Run 148 had been canonical at decision time.

The new `IMMUTABLE_EVIDENCE_HASH` is SHA-256 over a canonical manifest containing all 74 snapshot columns except the already-damaged legacy `superseded_by_snapshot_id`. Crucially, it continues to include `is_canonical`, `canonical_reason`, `canonicalized_at`, and `canonical_decision_json` as decision-time evidence. `CURRENT_ADMIN_STATE_HASH` independently covers every field of the pointer row. This is an explicit boundary, not a weakened full-row check.

## D. Intended lifecycle semantics

The SRS/SDD and implementation documents describe each run as creating immutable point-in-time setup evidence retained indefinitely, with corrections represented by new versions. They also say canonical metadata may change without mutating immutable snapshot evidence. The only coherent interpretation is two related views:

1. A snapshot answers: **“What lifecycle evidence and canonical decision existed for this ticker at this run/cutoff?”**
2. A current-selection pointer answers: **“Which snapshot is currently selected for this ticker/timeframe/date?”**

The legacy table attempted to answer both with `is_canonical` and `superseded_by_snapshot_id`. The corrected API deliberately distinguishes `canonical_at_decision` from derived current `is_canonical`, and labels records `CURRENT_CANONICAL`, `HISTORICAL_EVIDENCE`, or `NONCANONICAL_EVIDENCE`.

### Complete snapshot-column mutability classification

Every physical snapshot column is accounted for below. Comma-separated entries have the same semantics and recommendation.

| Column(s) | Current semantics | Intended classification | Mutable? | Evidence | Recommendation |
|---|---|---|---:|---|---|
| `id` | physical evidence identity | `IMMUTABLE_TEMPORAL_LINEAGE` | no | PK/FKs and forensic manifests | hash and retain |
| `evaluation_run_id`, `run_id`, `source_run_id_text` | execution identity | `IMMUTABLE_TEMPORAL_LINEAGE` | no | run reconstruction | hash and retain |
| `raw_row_id`, `fundamental_score_id`, `technical_score_id`, `combined_result_id`, `ranking_result_id`, `market_regime_snapshot_id`, `sector_rotation_snapshot_id` | upstream evidence lineage | `IMMUTABLE_TEMPORAL_LINEAGE` | no | source graph | hash and retain |
| `ticker`, `company_name`, `sector`, `timeframe`, `data_as_of_date` | evidence subject/key | `IMMUTABLE_TEMPORAL_LINEAGE` | no | run/date identity | hash and retain |
| `calculation_context_id`, `calculation_cutoff_at`, `input_as_of_session`, `calendar_version` | frozen temporal context | `IMMUTABLE_TEMPORAL_LINEAGE` | no | temporal integrity rules | hash and retain |
| `calculated_at`, `captured_at` | calculation/capture provenance | `IMMUTABLE_TEMPORAL_LINEAGE` | no | point-in-time reconstruction | hash and retain; never reinterpret as update time |
| `origin_type`, `engine_version`, `config_version`, `config_hash`, `source_data_hash`, `schema_version` | origin/version provenance | `IMMUTABLE_TEMPORAL_LINEAGE` | no | deterministic replay | hash and retain |
| `is_canonical`, `canonical_reason`, `canonicalized_at`, `canonical_decision_json` | formerly mixed current and decision state; now canonical-at-decision | `IMMUTABLE_DECISION_EVIDENCE` | no after initial decision completion | docs plus loss of Run 148 self-contained truth | hash; expose current state from pointer |
| `superseded_by_snapshot_id` | legacy mutable current-state linkage | `MUTABLE_ADMINISTRATIVE_STATE` (legacy only) | historically yes; new code never writes it | exact Canary #2 mutation | freeze existing value, exclude from immutable hash, retain for compatibility only |
| `primary_setup_family`, `primary_phase`, `lifecycle_state_candidate`, `actionability_candidate`, `data_quality_label`, `confidence_score`, `confidence_label` | lifecycle decision output | `IMMUTABLE_DECISION_EVIDENCE` | no | snapshot purpose | hash and retain |
| `fundamental_score`, `dual_score`, `trend_score`, `momentum_score`, `setup_score`, `risk_score`, `final_score`, `profile_score` | scored evidence | `IMMUTABLE_DECISION_EVIDENCE` | no | decision inputs/outputs | hash and retain |
| `technical_classification`, `stage`, `pullback_health`, `action_bias`, `combined_decision`, `ranking_profile` | classifications/decisions | `IMMUTABLE_DECISION_EVIDENCE` | no | decision output | hash and retain |
| `close_price`, `pivot_price`, `trigger_price`, `stop_price`, `target_price`, `distance_to_pivot_pct`, `entry_risk_pct`, `reward_risk`, `close_above_trigger`, `high_above_trigger`, `high_price` | market/setup evidence | `IMMUTABLE_DECISION_EVIDENCE` | no | point-in-time inputs | hash and retain |
| `technical_confidence`, `required_feature_coverage`, `freshness_status` | evidence quality | `IMMUTABLE_DECISION_EVIDENCE` | no | canonical precedence and output | hash and retain |
| `signals_json`, `feature_flags_json`, `warning_flags_json`, `missing_data_json`, `source_lineage_json`, `diagnostic_high_cross_json`, `debug_json` | structured evidence/provenance/diagnostics | `IMMUTABLE_DECISION_EVIDENCE` or `IMMUTABLE_TEMPORAL_LINEAGE` | no | replay and explanation | hash and retain |

There are no snapshot columns named `pipeline_id`, `status`, `active`, `latest`, `updated_at`, `version`, `revision`, or `superseded_at`. Pipeline lineage is reachable through run/evaluation relationships. `updated_at` exists only on the new mutable pointer and is never treated as decision time.

The pointer's `ticker`, `timeframe`, and `data_as_of_date` form its immutable key; `selected_snapshot_id`, selected run/evaluation IDs, revision, reason, decision JSON, and `updated_at` are mutable administrative state; `created_at` is immutable operational metadata. The selection-event ledger is append-only evidence.

## E. Architecture defect

The old operation was:

```text
append new snapshot
UPDATE prior same-key snapshot SET is_canonical=false,
    superseded_by_snapshot_id=<new id>
mark new snapshot canonical
```

Run 148's row alone therefore cannot answer what was considered canonical when Run 148 completed. A later audit event happens to prove this particular transition, but there was no guaranteed canonical interval ledger and no old-row mutation timestamp. The model violated point-in-time reproducibility even though it did not alter scores or signals. The absence of snapshot `updated_at` avoids a misleading timestamp but also makes the in-place mutation unauditable to exact time.

## F. Chosen remediation model

| Option | Historical reproducibility | Query/runtime cost | Migration/compatibility | Concurrency/audit | Verdict |
|---|---|---|---|---|---|
| A. Mutable flag on evidence row | weak unless a complete external history already exists | simplest reads | superficially easy | old audit was incomplete and row meaning changed | rejected |
| B. Append-only supersession relation | strong | joins/events needed | moderate | strong audit; current lookup less direct | sound but not narrowest for frequent current reads |
| C. Separate pointer plus append-only transition event | strong | one indexed join for current reads | additive; API can derive legacy field | unique pointer, row/advisory locks, durable events | **chosen** |
| D. Existing equivalent | unavailable | n/a | legacy lifecycle event was not a complete pointer ledger | insufficient | rejected |

Revision 0069 adds one unique pointer per `(ticker,timeframe,data_as_of_date)` and an append-only revision event. It replaces the old partial unique canonical index with a non-unique canonical-at-decision index so multiple runs may truthfully retain their own decisions. Legacy bootstrap copies only rows currently marked canonical, sets reason `LEGACY_CURRENT_FLAG_BOOTSTRAP`, and records `historical_intervals_reconstructed=false`. It creates no fabricated events or times.

Snapshot retry behavior was also corrected: an exact existing snapshot identity is returned without reapplying a mutable payload, including after an insert race. Standalone source cutoffs are now deterministically derived from upload processed/uploaded time, not retry wall-clock time.

### Canonicalization call graph and downstream semantics

The production path is now:

```text
run/evaluation → append or identity-dedupe snapshot
→ acquire sorted same-key PostgreSQL advisory locks
→ reload all committed same-key candidates
→ deterministic precedence selection
→ lock/create/update one current pointer
→ append pointer-selection event when changed
→ complete canonical-at-decision fields only on the affected run's selected snapshot
```

| Consumer | Required view | Remediated behavior |
|---|---|---|
| lifecycle UI/API/dashboard | current selection, with optional history | pointer join; compatibility `is_canonical` is derived; `canonical_at_decision` is explicit |
| run detail/timeline/replay | historical snapshot as of run | direct run-owned snapshots; no present-day pointer filter |
| change detection and episodes | previous/current selected evidence by date | pointer-backed selection queries; lifecycle events retain linked evidence |
| alerts | immutable triggering event/snapshot plus current display status | event evidence remains historical; display status can be pointer-derived |
| maintenance/current history | currently selected snapshots | pointer join |
| ranking/actionability | run-local lifecycle output | immutable snapshot fields; no historical demotion dependency |
| CERI and Winner integration | frozen linked evidence where present | no new dependency on lifecycle current flags |
| forensic tooling | historical bytes and evolving admin state | separate immutable-evidence and current-admin hashes |

Raw current-state predicates on snapshot `is_canonical` were replaced in the lifecycle query, replay, maintenance, and change-history paths. The legacy `superseded_by_snapshot_id` stays serialized for compatibility but is frozen and has no new semantic authority.

## G. Adjacent subsystem audit

These are separate design risks, not changes made by this task:

| Finding | Severity | Evidence | Risk/recommendation |
|---|---|---|---|
| `STI-IMM-F001` | HIGH | `app/services/market_regime_repository.py:210-211` demotes `is_current_revision` and writes `superseded_by_snapshot_id` on the prior snapshot | same co-location defect; design a separate current pointer |
| `STI-IMM-F002` | HIGH | `app/services/sector_rotation_repository.py:298-299` performs the same prior-row mutation | same remediation class |
| `STI-IMM-F003` | HIGH | `SetupLifecycleRepository.supersede_prior_current_events` mutates prior lifecycle event `is_current_version`/supersession fields; point-in-time exports/queries can filter the present flag | audit event-version history boundary in a separate task |
| `STI-IMM-F004` | MEDIUM | CERI normalization and manual-review paths demote prior estimate/catalyst revisions via `is_current=false` | explicit revisions and manual audit help, but full-row evidence hashes change; review boundary |
| `STI-IMM-F005` | MEDIUM | Winner outcome and target/stop revision services mutate prior `is_current_revision` flags | replacement lineage exists; perform a hash-boundary audit |
| `STI-IMM-F006` | MEDIUM | Winner cohort publication mutates a previous generation's `completed_at` while a separate `published_generation_id` pointer already exists | clarify timestamp meaning and keep generation evidence immutable |

No directly analogous prior-row canonical demotion was found in the scoped technical-artifact or IBMI search. These findings do not change the lifecycle/CERI Canary #3 control scope and do not require broad refactoring here, but the HIGH findings should be separately scheduled.

## H. Concurrency and idempotence

Canonical ordering is independent of commit order: completed daily bar, successful source pipeline, required-feature coverage, context completeness, `calculated_at`, then snapshot ID. For each affected key, transactions acquire a sorted `pg_advisory_xact_lock` before reloading candidates. The later lock holder observes the earlier commit and recomputes over the full committed candidate set. A row lock protects an existing pointer; uniqueness constraints protect first creation and event revision. Therefore two committed pointers cannot exist and a stale pre-lock candidate set cannot overwrite the deterministic winner.

Retrying the selected Run B is a no-op at the pointer (`selected_snapshot_id` unchanged), creates no selection event, and leaves historical hashes stable. Exact snapshot-identity retries return the stored row without rewriting it. Real PostgreSQL competing Run B/Run C tests certified the deterministic winner, one pointer, monotonic event revisions, and stable old-row bytes.

## I. Schema/migration

```text
migration required: YES
new migration ID: 0069_lifecycle_current_selection
code migration head: 0069_lifecycle_current_selection (single head)
production migration applied: NO
production remains: 0068_market_calc_context
```

Disposable PostgreSQL tests covered 0068→0069 upgrade, bootstrap truthfulness, constraints, indexes, pointer behavior, concurrent completion, and downgrade inspectability. Downgrade is intentionally described as lossy administrative compatibility because it must translate the pointer back into the legacy one-current-row representation; production historical evidence must not be downgraded casually.

## J. Test evidence

| Command/scope | Result |
|---|---|
| `uv run pytest tests/setup_lifecycle -q` | 254 passed, 1 warning, 26.41s |
| `uv run pytest tests/integration/test_slse_golden_corpus.py -q` | 3 passed, 3 warnings, 83.38s |
| disposable PostgreSQL aggregate: lifecycle immutability, session temporal integrity, SLSE golden corpus, Winner temporal integrity, CERI batched workflow | 23 passed, 28 warnings, 312.23s |
| `uv run pytest -m 'not slow' -q` (clean second run) | 2175 passed, 106 skipped, 58 deselected, 23 warnings, 368.93s |
| first full non-slow run | one unrelated transient PowerShell subprocess startup timeout; 2174 passed; isolated rerun `tests/ops/test_lifecycle_powershell.py`: 21 passed; clean complete rerun above passed |
| `uv run ruff check app tests` | all checks passed |
| `uv run ruff format --check` on migration, hash module, hash tests, and PostgreSQL immutability test | 4 files already formatted |
| `uv run python -m compileall -q app scripts tests alembic` | passed |
| `uv run alembic heads` | one head: `0069_lifecycle_current_selection` |

For transparency, repository-wide `uv run ruff check .` still reports 24 pre-existing issues in old migrations, and repository-wide `uv run ruff format --check .` reports 251 pre-existing files that would be reformatted. The remediation's application/test lint and changed/new-file format gates pass; mass-formatting unrelated history was deliberately excluded from this narrow task.

The disposable migration subprocesses require explicit `SWINGLENS_TEST_DISPOSABLE_ALEMBIC=1`; `alembic/env.py` still validates both candidate and admin URLs as disposable and exact-match. Default/production migration behavior is unchanged.

## K. Production safety

The final production verification at `2026-09-08 21:37:45.506401+02` was again performed with an explicit read-only transaction. It observed production at revision 0068, all ten hashes and values above unchanged, zero active jobs, zero queued jobs, and no application/worker process. No migration was applied, no job was enqueued, and no application or worker was started.

```text
Run 148 rows unchanged during this task: YES
Run 149 rows unchanged during this task: YES
production business writes: NO
historical repair: NO
Canary #3 run: NO
production migration applied: NO
```

“Unchanged” here means unchanged from the post-Canary #2 baseline; this task did not undo or compound the already-observed legacy administrative mutations.

## L. Final recommendation

```text
May a separate task run Controlled Canary #3?

YES — after the normal, separately authorized deployment of the remediated code
and migration 0069, with the same pre/post controls and no fabricated repair of
Runs 148 or 149.
```

Controlled Canary #3 was not run here. The recommended canary should compare `IMMUTABLE_EVIDENCE_HASH` for historical snapshots, `CURRENT_ADMIN_STATE_HASH` for expected pointer evolution, and the append-only event ledger. A pointer advance is expected administrative change; any historical evidence hash change remains a fail-fast violation.
