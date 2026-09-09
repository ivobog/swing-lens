# Temporal / Preflight / Lifecycle Invariant Registry

Certified baseline: `c59a6550a744bcb576ce17042f4bd4feb39337d9`

Certification branch: `codex/adversarial-temporal-preflight-certification`

Contract owner: the shared `CanonicalEvidenceSerializer`, frozen market-context services,
transition preflight service, lifecycle repository/canonicalizer, technical PIT loader, and CERI
PIT services.

`CERTIFIED` means the invariant has an executable test and passed the 2026-09-09 certification
matrix. `COMPATIBILITY` identifies a deliberately preserved legacy reproduction path; it is not
used to create new safety-critical evidence.

## Registry

| ID | Domain | Formal statement | Failure consequence | Production code owner | Tests | Status |
|---|---|---|---|---|---|---|
| TPI-INV-001 | TIME | `instant(A) == instant(B) => canonical(A) == canonical(B)` | False stale-plan rejection or divergent evidence identity | `canonical_evidence.py` | `test_exact_latest_canary_timezone_regression_is_canonical`, `test_timezone_metamorphism_generated_aware_datetimes` | CERTIFIED |
| TPI-INV-002 | TIME | `canonical(dt) = UTC(dt)` with exactly six fractional digits and `Z` | Runtime/DB timezone changes identity | `CanonicalEvidenceSerializer` | `test_manifest_timestamp_offsets_canonicalize_to_same_exact_instant` | CERTIFIED |
| TPI-INV-003 | TIME | Safety-critical datetimes are timezone-aware; naive values are rejected | Ambiguous instants leak into PIT or hashes | `MarketCalculationCutoff`, serializer, `ceri.pit_eligibility` | `test_unsafe_scalar_representation_fails_closed`, `test_missing_or_naive_receipt_provenance_fails_closed` | CERTIFIED |
| TPI-INV-004 | TIME | Microseconds are preserved exactly; floating-point epoch time is forbidden in evidence encoding | Distinct events collide or round differently | `CanonicalEvidenceSerializer` | `test_manifest_canonicalization_does_not_normalize_different_instants_together` | CERTIFIED |
| TPI-INV-005 | TIME | PostgreSQL session timezone cannot alter an instant or its fingerprint | Persist/reload invalidates a valid plan | `MarketCalculationCutoff.__post_init__`, `cutoff_from_row` | `test_preflight_fingerprints_survive_postgresql_session_timezone_roundtrip` (UTC/Zurich/New York) | CERTIFIED |
| TPI-INV-006 | TIME | Frozen context is a pure function of its supplied instant and market-calendar versions | Wall-clock delay moves execution boundary | `MarketClockService` | `test_temporal_boundary_matrix_is_timezone_representation_invariant`, session temporal suites | CERTIFIED |
| TPI-INV-007 | TIME | DST, open, close, bar-ready, midnight, weekend, and holiday equivalents return the same context in every display timezone | Boundary-dependent nondeterminism | `MarketClockService`, US market calendar | `test_temporal_boundary_matrix_is_timezone_representation_invariant` | CERTIFIED |
| TPI-INV-008 | CONTEXT | `preflight.context_id == pipeline.context_id` | C1/C2 temporal split | `transition_preflight_plan_service`, `pipeline_service` | `test_session_boundary_reuses_reserved_context_and_duplicate_enqueue_is_idempotent`, clone replay | CERTIFIED |
| TPI-INV-009 | CONTEXT | `pipeline.context_id == durable_job.payload.context_id` | Worker executes a different evidence world | `pipeline_service`, `market_calculation_context_service` | `test_pipeline_job_context_round_trip_on_disposable_postgresql` | CERTIFIED |
| TPI-INV-010 | CONTEXT | Technical, lifecycle, CERI, and Winner pipeline children resolve the authoritative persisted context | Cross-domain temporal drift | context resolvers and job handlers | session/CERI/Winner temporal suites | CERTIFIED |
| TPI-INV-011 | CONTEXT | Cutoff, completed session, calendar version, readiness time/version, and reason are immutable after reservation | Retry changes semantics | `MarketCalculationContext`, `MarketCalculationCutoff` | PostgreSQL roundtrip and preflight restart tests | CERTIFIED |
| TPI-INV-012 | CONTEXT | Retry, process restart, or duplicate delivery never creates C2 for a reserved plan | Replacement context invalidates evidence binding | `start_pipeline`, `attach_reserved_market_context` | duplicate-consume, failure-injection, clone replay | CERTIFIED |
| TPI-INV-013 | CONTEXT | Missing, unowned-at-execution, cross-run, or multiply-owned context fails closed | Orphan/cross-run context use | `resolve_pipeline_market_context`, preflight verification | context contract and PostgreSQL preflight suites | CERTIFIED |
| TPI-INV-014 | PIT | `source.known_at = retrieved_at ?? ingested_at` and `known_at <= cutoff` | Post-cutoff source leakage | `ceri.pit_eligibility` | `test_source_known_at_uses_receipt_not_older_publication_time`, clone replay | CERTIFIED |
| TPI-INV-015 | PIT | Missing or naive receipt provenance is ineligible | Fabricated temporal provenance | `source_record_known_at` | `test_missing_or_naive_receipt_provenance_fails_closed` | CERTIFIED |
| TPI-INV-016 | PIT | `bar.session <= frozen.latest_completed_session` | Future-session market leakage | price-bar repositories and CERI PIT predicates | CERI PIT and session temporal suites | CERTIFIED |
| TPI-INV-017 | PIT | `bar.first_seen_at <= cutoff` | Late-arriving bar leaks backward | `price_bar_is_eligible`, SQL predicates | `test_old_session_late_known_bar_fails_knowledge_gate` | CERTIFIED |
| TPI-INV-018 | PIT | `bar.revised_at is null or bar.revised_at <= cutoff` | Post-cutoff current revision leaks backward | CERI/technical PIT loaders | CERI PIT and technical reconstruction suites | CERTIFIED |
| TPI-INV-019 | PIT | Selection chooses the latest eligible version, never merely latest current | Current-state leakage into historical replay | CERI point-in-time query and price-bar revision loader | CERI point-in-time/revision tests | CERTIFIED |
| TPI-INV-020 | PIT | Query order, cache temperature, and worker delay do not change the eligible evidence set | Nondeterministic reconstruction | technical/CERI source loaders | ordering metamorphisms, broad clone scan, CERI suites | CERTIFIED |
| TPI-INV-021 | LIFECYCLE | Persisted historical snapshot evidence is immutable after insert | History rewrite | lifecycle repository and forensic hash | lifecycle immutability PostgreSQL suite, Runs 148–152 clone hashes | CERTIFIED |
| TPI-INV-022 | LIFECYCLE | Exactly one pointer exists for `(ticker,timeframe,data_as_of_date)` | Ambiguous session canonical | current-selection unique key/repository | lifecycle PostgreSQL suites | CERTIFIED |
| TPI-INV-023 | LIFECYCLE | A new session inserts a new pointer and never rewrites an earlier session pointer | Historical canonical drift | `advance_canonical_selection` | `test_session_canonical_keys_and_cross_session_current_state_are_distinct` | CERTIFIED |
| TPI-INV-024 | LIFECYCLE | A same-session replacement changes only the pointer/admin row; evidence snapshots remain unchanged | Evidence mutation | canonicalizer/repository | lifecycle immutability and canonicalization suites | CERTIFIED |
| TPI-INV-025 | LIFECYCLE | Current state is the latest valid session-canonical pointer by session date | Wrong cross-session state | `current_cross_session_snapshot` | lifecycle current-state adversarial tests | CERTIFIED |
| TPI-INV-026 | LIFECYCLE | Selection ledger is append-only and uniquely keyed | Lost/rewritten audit history | `SetupSignalSnapshotSelectionEvent` | lifecycle schema/immutability suites | CERTIFIED |
| TPI-INV-027 | LIFECYCLE | Every committed pointer transition has exactly one matching ledger event in the same transaction | Pointer/ledger divergence | `advance_canonical_selection` | failure-between-operations and rollback tests | CERTIFIED |
| TPI-INV-028 | PREFLIGHT | Equal semantic evidence produces equal fingerprints | False stale rejection | candidate/preflight fingerprint functions | exact regression, ordering/property tests | CERTIFIED |
| TPI-INV-029 | PREFLIGHT | Material pointer, eligible evidence, selection-key, technical, or HIGH-status change alters the contract and rejects enqueue | Stale plan executes | preflight verification | pointer/evidence/idempotency tests | CERTIFIED |
| TPI-INV-030 | PREFLIGHT | Persist/reload leaves evidence, technical, candidate, and context fingerprints unchanged | Database representation changes identity | shared serializer/preflight service | PostgreSQL timezone matrix | CERTIFIED |
| TPI-INV-031 | PREFLIGHT | Cancelled plans reject with `PLAN_CANCELLED` | Cancelled work executes | preflight state machine | terminal-state PostgreSQL tests | CERTIFIED |
| TPI-INV-032 | PREFLIGHT | Expired plans reject with `PLAN_EXPIRED` and are never reusable | Time-expired evidence executes | preflight state machine | expiry/abandonment tests | CERTIFIED |
| TPI-INV-033 | PREFLIGHT | Stale plans reject with `STALE_PREFLIGHT`; mismatched keys use `SELECTION_KEY_MISMATCH` | Ambiguous operational diagnosis | preflight verification | state/error taxonomy tests | CERTIFIED |
| TPI-INV-034 | PREFLIGHT | One idempotency key binds one run, ticker set, and cutoff instant | Semantically different request reuses old plan | `create_transition_preflight_plan` | idempotency conflict PostgreSQL test | CERTIFIED |
| TPI-INV-035 | PREFLIGHT | Duplicate enqueue yields one pipeline; a racing loser is idempotent or receives `DUPLICATE_ENQUEUE` | Duplicate execution | `start_pipeline`, plan row lock | concurrent consumer test, clone replay | CERTIFIED |
| TPI-INV-036 | PREFLIGHT | Abandonment leaves no business mutation or lock; reserved context remains forensic and cannot be reused after terminal plan state | Lock/orphan mutation leak | expiry/cancel services | abandoned, cancellation, failure-injection tests | CERTIFIED |
| TPI-INV-037 | PERSISTENCE | ORM → PostgreSQL → ORM preserves semantic identity | Durable boundary changes identity | ORM types and shared serializer | PostgreSQL timezone matrix | CERTIFIED |
| TPI-INV-038 | PERSISTENCE | JSON parse/render of canonical JSON preserves bytes and fingerprint | Transport changes identity | `CanonicalEvidenceSerializer` | `test_scalar_container_json_and_ordering_contract` | CERTIFIED |
| TPI-INV-039 | PERSISTENCE | Dict key order does not affect bytes | Incidental map ordering changes hash | serializer | scalar/container test | CERTIFIED |
| TPI-INV-040 | PERSISTENCE | Set/frozenset and designated reason-code collections are canonically ordered | Query/container order changes hash | serializer | scalar/container and Winner manifest tests | CERTIFIED |
| TPI-INV-041 | PERSISTENCE | Finite Decimal values use normalized fixed-point; UUID, Enum, bytes, bool, null, int are deterministic | Runtime `repr()` affects identity | serializer | scalar/container tests | CERTIFIED |
| TPI-INV-042 | PERSISTENCE | Float values must be finite and negative zero canonicalizes to zero | NaN/Inf/platform ambiguity | serializer | unsafe scalar and scalar/container tests | CERTIFIED |
| TPI-INV-043 | PERSISTENCE | Date, datetime, and time occupy distinct ISO profiles; datetime alone is timezone-normalized | Type/instant ambiguity | serializer | scalar/container tests | CERTIFIED |
| TPI-INV-044 | CONCURRENCY | Preflight reads mutable pointer admin state as current, not as cutoff-frozen PIT evidence | Post-preflight pointer advance is hidden | transition candidate pointer query | `test_pointer_precondition_reads_current_admin_state_not_frozen_pit_cutoff` | CERTIFIED |
| TPI-INV-045 | CONCURRENCY | Plan consume uses a PostgreSQL row lock; two consumers produce one owner | Double pipeline | preflight verification/consume | `test_two_concurrent_plan_consumers_create_exactly_one_pipeline` | CERTIFIED |
| TPI-INV-046 | CONCURRENCY | Lifecycle key canonicalization is transaction-serialized with sorted advisory locks | Concurrent pointer lost update/deadlock | lifecycle repository | lifecycle concurrency PostgreSQL tests | CERTIFIED |
| TPI-INV-047 | CONCURRENCY | Pointer update and ledger insert commit or roll back together | Split-brain audit state | lifecycle repository transaction | both pointer/ledger injection tests | CERTIFIED |
| TPI-INV-048 | CONCURRENCY | Evidence arriving after cutoff cannot enter technical/CERI reconstruction, even before enqueue | Race-dependent PIT leakage | PIT query predicates | evidence revision and CERI PIT tests | CERTIFIED |
| TPI-INV-049 | CONCURRENCY | Failure before commit at context, plan, pipeline, job, pointer, or ledger boundaries leaves no half-applied state | Restart observes partial business state | transaction owners | parameterized PostgreSQL failure-injection suite | CERTIFIED |
| TPI-INV-050 | PREFLIGHT | In-process schema validation or migration must not disable application forensic loggers | Preflight/lifecycle rejection evidence silently disappears | `alembic/env.py`, application log owners | full-suite ordered logging regressions and focused `caplog` tests | CERTIFIED |

## Canonical scalar and container contract

- `datetime`: aware only; UTC; `YYYY-MM-DDTHH:MM:SS.ffffffZ`.
- `date`: `YYYY-MM-DD`; never timezone converted.
- `time`: fixed six-digit fractional ISO time; an attached timezone must be valid.
- `timezone`: fixed offsets render as `UTC±HH:MM[:SS]`; named zones use their IANA key.
- `Decimal`: finite, normalized, fixed-point (`1.2300` and `1.23` are identical).
- `float`: finite JSON number; `-0.0` canonicalizes to `0.0`; NaN/Infinity fail closed.
- `UUID`: lowercase canonical hyphenated text.
- `Enum`: recursively canonicalized semantic `.value`.
- `bytes`: base64 in a `$bytes_base64` object.
- `dict`/mapping: stringified keys sorted lexically; post-stringification collisions fail closed.
- `list`/tuple: domain order preserved.
- `set`/frozenset and reason-code collections: sorted by canonical JSON value.
- unsupported objects: fail closed; `repr()` and `default=str` are forbidden for new
  safety-critical hashes.

## Fingerprint inventory

| Fingerprint | Owner | Inputs | Shared serializer? | PG roundtrip tested? | Timezone invariant? | Ordering invariant? |
|---|---|---|---:|---:|---:|---:|
| Preflight aggregate evidence | `transition_candidate_service.aggregate_evidence_fingerprint` | ticker, selection key, candidate evidence hash | Yes | Yes | Yes | Yes |
| Frozen context | `market_calculation_context_fingerprint` | context ID and all semantic context fields | Yes | Yes | Yes | Yes |
| Technical reconstruction | `_technical_reconstruction_fingerprint` | all semantic `TechnicalScore` columns except row/run/creation IDs | Yes | Yes | Yes | Yes |
| Candidate evidence | `_candidate_evidence_fingerprint` | context, pointer expectations, source hash, technical hash, raw row, PIT bars, classification | Yes | Yes | Yes | Yes |
| Pointer/precondition | preflight expected-pointer comparison plus aggregate evidence | exact/latest pointer IDs and revisions | Yes | Yes | N/A | Yes |
| Candidate set | preflight plan JSON and aggregate hashes | normalized tickers, exact selection keys, classifications | Yes | Yes | Yes | Yes |
| CERI score evidence | `score_evidence_hash` | cutoff/session, components, ledgers, source IDs, config, lineage | Yes | Clone | Yes | Yes |
| CERI source content | `source_record_content_hash` | provider economic projection | Yes | Clone | Yes | Yes |
| CERI revision evidence | `revision_evidence_hash` | revision inputs and source lineage | Yes | Suite | Yes | Yes |
| CERI price response | `price_response_service._hash` | event identity, frozen cutoff, selected bar IDs/metrics | Yes | Clone | Yes | Yes |
| CERI feature rebuild input/output | `feature_rebuild_service._stable_hash` | frozen context, request bounds, sorted row fingerprints/output identities | Yes | Clone | Yes | Yes |
| Lifecycle immutable snapshot | `immutable_evidence_hash` | every snapshot field except legacy mutable admin field | Yes (compatibility wrapper) | Clone | Yes | Yes |
| Lifecycle current selection | `current_admin_state_hash` | complete pointer/admin row | Yes (compatibility wrapper) | Clone | Yes | Yes |
| Technical artifact/config | `technical_artifact_cache` | input series versions/session/config/engine/schema | Yes | Suite | N/A | Yes |
| Winner temporal manifests | `temporal_manifest_canonicalization` | decisions, dates, timestamps, evidence metadata | Yes (compatibility wrapper) | Yes | Yes | Yes |

## State machine

The persisted initial state is `RESERVED` (the repository does not use separate ephemeral
`CREATED`/`READY` states).

| From | Event | To | Allowed? | Side effects |
|---|---|---|---:|---|
| RESERVED | CONSUME | CONSUMED | Yes | Atomically attach reserved context and pipeline ID; set consumed time |
| RESERVED | CANCEL | CANCELLED | Yes | Set cancelled time; context retained unowned for forensics |
| RESERVED | EXPIRE | EXPIRED | Yes | Set `TTL_EXPIRED`; context retained unowned and invalid |
| RESERVED | INVALIDATE | STALE | Yes | Persist explicit invalidation when invoked by an owning workflow |
| CONSUMED | ENQUEUE duplicate | CONSUMED | Idempotent/reject | Return authoritative pipeline or `DUPLICATE_ENQUEUE` in a race |
| CANCELLED | ENQUEUE | CANCELLED | No | `PLAN_CANCELLED`; no business writes |
| EXPIRED | ENQUEUE | EXPIRED | No | `PLAN_EXPIRED`; no business writes |
| STALE | ENQUEUE | STALE | No | `STALE_PREFLIGHT`; no business writes |
| Any terminal | Any state change | unchanged | No | No implicit resurrection or reuse |

Cancelled/expired contexts are forensically retained, never reusable, and eligible for a future
explicit metadata-retention cleanup policy only after their terminal plan is outside its required
audit-retention window. No such cleanup is introduced or performed by this certification.
