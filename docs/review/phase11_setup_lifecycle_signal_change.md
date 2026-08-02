# Phase 11 Review - Setup Lifecycle and Signal-Change Engine

Date: 2026-08-02
Reviewer: Codex
Scope: setup lifecycle configuration, canonical snapshot capture, source loading, signal-change
detection, lifecycle engine, episode service, alert service, replay service, maintenance, routes,
schema constraints, exports, and setup-lifecycle tests.

## Objective

Verify lifecycle state transitions, episode identity, alerts, replay, and temporal correctness.

## Executive Summary

Phase 11 is partially exit-ready.

The setup lifecycle implementation has a strong architecture and a substantial focused test suite.
Configuration validates the supported states and transition precedence, source snapshots carry
source ids and hashes, canonicalization is deterministic, signal-change event keys are stable,
episode selection is deterministic, alerts have cooldown and event-key dedupe, and the database
schema includes uniqueness constraints for canonical snapshots, active episodes, lifecycle events,
signal-change events, alert rules, and alert events.

The strict Phase 11 exit criteria are not fully met yet. The largest issue is that captured
`SetupSignalSnapshot` rows are upserted by run/ticker/timeframe/version/config only, so revised
source data for the same run overwrites the prior evidence row even though the feature docs and
review criteria treat snapshots as immutable evidence. The source loader can also attach latest
global market/sector context with no decision-cutoff check before capture; the snapshot builder only
warns after the fact. Replay persistence is exposed as a simple `persist=true` API option without
an explicit confirmation token or promotion workflow, and purge execution exists in the repository
even though retention config disables purge in this phase.

## Evidence Log

| Check | Result | Notes |
| --- | --- | --- |
| Phase 11 checklist from `C:/Users/Ivica/Downloads/software_review_plan.md` | Reviewed | Objective, activities, outputs, and exit criteria mapped. |
| Focused setup lifecycle suite | Passed | `uv run pytest tests/setup_lifecycle -q` -> `140 passed, 1 warning in 14.77s`. |
| Property-based lifecycle tests | Gap found | Search found no Hypothesis/property-test coverage in `tests/setup_lifecycle`; lifecycle tests are example/fixture based. |
| Schema uniqueness controls | Reviewed | Unique/partial indexes cover canonical day, active family episode, lifecycle source key, signal-change source key, alert rule, and alert event key. |
| Replay isolation | Partial | Dry-run replay does not create an evaluation run; persisted replay creates a replay evaluation version but does not mutate live episodes/events. API persistence lacks explicit confirmation. |
| Alert idempotency | Mostly satisfied | Tests cover retry dedupe, cooldown suppression, gate-blocked alerts, data-degraded alerts, and acknowledge/dismiss user state. |

## Formal State-Transition Table

Configured states are `DISCOVERED`, `DEVELOPING`, `TIGHTENING`, `READY`, `TRIGGERED`,
`CONFIRMED`, `EXTENDED`, `FAILED`, and `EXPIRED`; terminal states are `FAILED` and `EXPIRED`
(`config/setup_lifecycle.yaml:24-45`).

| Current State | Allowed Next States | Rule Source | Notes |
| --- | --- | --- | --- |
| None | DISCOVERED, DEVELOPING, TIGHTENING, READY, TRIGGERED, CONFIRMED, EXTENDED | Engine evidence mapping | Opening directly into advanced states is allowed and flagged with `SKIPPED_PRIOR_PROGRESSION`. |
| DISCOVERED | DEVELOPING, TIGHTENING, READY, TRIGGERED, CONFIRMED, EXTENDED, FAILED, EXPIRED, DISCOVERED | Family evidence, failure, age/gap | Forward progress is immediate when evidence is stronger. |
| DEVELOPING | TIGHTENING, READY, TRIGGERED, CONFIRMED, EXTENDED, FAILED, EXPIRED, DEVELOPING | Family evidence, failure, age/gap | No explicit rejection of backward evidence; hysteresis is limited to READY/TRIGGERED. |
| TIGHTENING | READY, TRIGGERED, CONFIRMED, EXTENDED, FAILED, EXPIRED, TIGHTENING | Family evidence, failure, age/gap | Can remain tightening when phase changes within state. |
| READY | TRIGGERED, CONFIRMED, EXTENDED, FAILED, EXPIRED, READY | Hysteresis | Weaker evidence is held when confidence margin is within -5 of normal threshold. |
| TRIGGERED | CONFIRMED, EXTENDED, FAILED, EXPIRED, TRIGGERED | Persistence gate | Confirmation requires at least 2 persistence sessions. |
| CONFIRMED | EXTENDED, FAILED, EXPIRED, CONFIRMED | Family evidence, failure, age/gap | Backward transitions are not explicitly prohibited by a state table, but stronger current-state hysteresis can hold some states. |
| EXTENDED | FAILED, EXPIRED, EXTENDED | Family evidence, failure, age/gap | Actionability is watch-only. |
| FAILED | FAILED | Terminal lock | Terminal states return `TERMINAL_STATE_LOCKED`. |
| EXPIRED | EXPIRED | Terminal lock | Terminal states return `TERMINAL_STATE_LOCKED`. |

Implementation details:

- Evidence-to-state priority maps hard failure, observation gap expiry, max age expiry, extended,
  confirmed, triggered, ready, tightening phases, trackable, then discovered
  (`app/services/setup_lifecycle/lifecycle_engine.py:112-144`).
- Terminal states are locked before new evidence is evaluated
  (`app/services/setup_lifecycle/lifecycle_engine.py:59-61`,
  `app/services/setup_lifecycle/lifecycle_engine.py:165-184`).
- Hysteresis prevents READY/TRIGGERED flapping and enforces confirmation persistence
  (`app/services/setup_lifecycle/lifecycle_engine.py:146-163`).
- Episode open/update/close behavior persists active episode state and emits transition events only
  when state or phase changes (`app/services/setup_lifecycle/episode_service.py:60-110`,
  `app/services/setup_lifecycle/episode_service.py:187-242`,
  `app/services/setup_lifecycle/episode_service.py:264-342`).

## Episode Lineage and Audit Requirements

Current lineage fields:

- Snapshot source ids include raw row, fundamental, technical, combined, ranking, market regime, and
  sector rotation ids (`app/services/setup_lifecycle/snapshot_builder.py:415-426`).
- Snapshot source lineage includes run id, ticker, data-as-of date, latest bar lineage, ranking
  profiles, market-regime as-of, and sector-rotation as-of
  (`app/services/setup_lifecycle/snapshot_builder.py:428-448`).
- Snapshot source data hash includes ticker, as-of date, source ids, signal values, and lineage
  (`app/services/setup_lifecycle/snapshot_builder.py:129-136`).
- Lifecycle events carry snapshot id, effective date, from/to state and phase, actionability before
  and after, confidence, severity, engine/config version/hash, reason codes, evidence, and warning
  flags (`app/models/tables.py:2428-2501`).
- The active episode uniqueness index permits one active episode per ticker/timeframe/family
  (`app/models/tables.py:2413-2425`).
- Primary episode selection is deterministic by state precedence, confidence, setup score, current
  date, and family precedence (`app/services/setup_lifecycle/episode_service.py:508-513`,
  `app/services/setup_lifecycle/episode_service.py:656-666`).

Audit gaps:

- Snapshot identity excludes `data_as_of_date`, `source_data_hash`, and `evaluation_run_id`, so a
  changed source hash for the same run/ticker/version/config updates the existing snapshot row
  rather than creating a new evidence revision (`app/services/setup_lifecycle/repository.py:178-233`,
  `app/models/tables.py:2321-2329`).
- Canonical revisions are audited as lifecycle events, but snapshot overwrites before
  canonicalization do not preserve the previous snapshot payload for reconstruction.
- Lifecycle events include evidence and warning flags, but source cutoff should be promoted to a
  first-class field or consistently included in evidence for every transition, not just inferable
  from the snapshot.

Required additions:

- Add append-only snapshot revisions keyed by `(run_id, ticker, timeframe, data_as_of_date,
  engine_version, config_hash, source_data_hash)` or maintain a separate immutable evidence table.
- Add explicit `source_cutoff_at` or `decision_cutoff_at` to snapshots/events.
- Store previous snapshot hash/id whenever a revised source row produces a new snapshot revision.

## Replay and Reconstruction Safety Report

Current controls:

- Replay config is disabled by default, non-authoritative by default, and declares explicit admin
  action plus confirmation requirements (`config/setup_lifecycle.yaml:401-406`).
- Dry-run replay returns proposed decisions without creating an evaluation run
  (`app/services/setup_lifecycle/replay_service.py:43-56`; tested at
  `tests/setup_lifecycle/test_replay.py:18-30`).
- Persisted replay creates a separate `REPLAY` evaluation run/version and does not create live
  lifecycle events, live episodes, or alerts (`app/services/setup_lifecycle/replay_service.py:57-95`;
  tested at `tests/setup_lifecycle/test_replay.py:33-53`).
- Reconstructed origin is configured to stay out of live alerts/statistics and OWPE exports
  (`config/setup_lifecycle.yaml:415-419`), and alert persistence suppresses reconstructed sources
  when evidence carries the origin (`app/services/setup_lifecycle/alert_service.py:253-256`).

Safety gaps:

- The API exposes persisted replay directly through `persist=true` and commits when true
  (`app/routers/setup_lifecycle_routes.py:419-438`). There is no confirmation token, preview token,
  second-step confirmation, or admin-only promotion endpoint in this route.
- Config validation rejects authoritative-by-default replay and missing explicit-admin action, but
  does not reject `promotion_requires_confirmation=False`
  (`app/services/setup_lifecycle/config.py:606-625`,
  `app/services/setup_lifecycle/config.py:662-688`).
- The replay request has `reason` and `requested_config`, but the route does not collect a reason
  or confirmation metadata (`app/services/setup_lifecycle/replay_service.py:24-30`,
  `app/routers/setup_lifecycle_routes.py:419-438`).

Recommendation:

- Keep dry-run replay as the only unauthenticated/simple route behavior.
- Require a preview token, explicit confirmation field, requester, and reason for persisted replay.
- Add a separate promotion endpoint that writes an administrative audit event and never silently
  mutates live episodes.
- Add config validation for `promotion_requires_confirmation`.

## Alert Idempotency and Noise-Reduction Actions

Current controls:

- Built-in rules are seeded from config, respect `built_in_rules_enabled`, and are upserted by
  stable `rule_id` (`app/services/setup_lifecycle/alert_service.py:47-67`,
  `app/services/setup_lifecycle/repository.py:622-642`).
- Alert persistence suppresses disabled rules, low-confidence sources, reconstructed sources, and
  active cooldown windows (`app/services/setup_lifecycle/alert_service.py:235-259`).
- Alert event keys include rule id, source event key, ticker, episode id, effective date, and
  evaluation run id (`app/services/setup_lifecycle/repository.py:856-875`).
- Duplicate alert events are deduped by `event_key`
  (`app/services/setup_lifecycle/repository.py:655-665`).
- Cooldown searches recent alert events by ticker/timeframe/rule and semantic key
  (`app/services/setup_lifecycle/repository.py:667-692`).
- Acknowledge and dismiss only alter alert user state
  (`app/services/setup_lifecycle/repository.py:694-725`).

Tested controls:

- Retry dedupe for `NEW_READY` alerts (`tests/setup_lifecycle/test_alert_service.py:44-64`).
- Cooldown suppression for same episode and score acceleration
  (`tests/setup_lifecycle/test_alert_service.py:66-107`,
  `tests/setup_lifecycle/test_alert_service.py:151-184`).
- Gate-blocked alerts without lifecycle mutation
  (`tests/setup_lifecycle/test_alert_service.py:128-149`).
- Data degraded risk alerts (`tests/setup_lifecycle/test_alert_service.py:185-207`).
- Acknowledge/dismiss user-state behavior (`tests/setup_lifecycle/test_alert_service.py:208-226`).

Recommended actions:

- Add stale-alert expiration semantics separate from user dismissal.
- Add a test for `alerts.built_in_rules_enabled=false` at the full evaluation-service level, not
  only alert seeding.
- Add database-level integration tests for concurrent duplicate alert insert races.

## Findings Register

### PH11-001 - Captured setup snapshots are upserted, not immutable evidence

Severity: High

Evidence:

- Snapshot source hash changes when relevant evidence changes
  (`tests/setup_lifecycle/test_snapshot_builder.py:89-96`).
- Repository identity lookup ignores `source_data_hash`, `data_as_of_date`, and `evaluation_run_id`;
  it matches only run id, ticker, timeframe, engine version, and config hash
  (`app/services/setup_lifecycle/repository.py:178-233`).
- The schema unique constraint mirrors that same identity
  (`app/models/tables.py:2321-2329`).
- `_apply_snapshot_fields` updates source ids, source hash, signals, lineage, warnings, and promoted
  fields on the existing row (`app/services/setup_lifecycle/repository.py:893-933`).
- Tests intentionally assert retry idempotency as "one snapshot per ticker"
  (`tests/setup_lifecycle/test_snapshot_builder.py:99-120`).

Impact: Reprocessing the same run with revised technical, price, market, or sector source data can
overwrite the only setup snapshot row for that ticker/run/config. This preserves retry idempotency
but fails the Phase 11 requirement that replay/reconstruction and state changes be reproducible from
immutable evidence.

Recommendation:

- Keep retry idempotency by source hash, not by mutable run/ticker identity.
- Add append-only snapshot revisions and mark a canonical/latest materialized pointer separately.
- Add a test proving revised source data creates a new immutable snapshot or preserved revision.

### PH11-002 - Run source loading can attach future global market/sector context

Severity: High

Evidence:

- Source loader selects either run-specific or global market snapshot and orders by latest as-of date
  with no cutoff tied to the upload run or ticker bar date
  (`app/services/setup_lifecycle/source_loader.py:112-131`).
- Sector snapshot selection has the same pattern
  (`app/services/setup_lifecycle/source_loader.py:133-152`).
- Snapshot builder can warn about future-dated context when related context as-of date is after the
  snapshot as-of date (`app/services/setup_lifecycle/snapshot_builder.py:321-355`,
  `app/services/setup_lifecycle/snapshot_builder.py:676-681`).
- Canonicalization treats `FUTURE_DATED_SOURCE_CONTEXT` as a fatal source-pipeline warning
  (`app/services/setup_lifecycle/canonicalization.py:207-217`), but the source was still attached
  to the snapshot.

Impact: A setup snapshot can contain future market or sector context in its source ids, lineage, and
signals. The warning helps canonical selection, but Phase 11 asks for temporal correctness and
source cutoff guarantees, so future context should be rejected or excluded before snapshot capture.

Recommendation:

- Resolve ticker snapshot as-of date before context selection, then query market/sector snapshots
  with `as_of_date <= data_as_of_date`.
- If no eligible context exists, attach no context and emit `MISSING_MARKET_REGIME` /
  `MISSING_SECTOR_ROTATION` instead of attaching future context.
- Add tests where a global context snapshot exists only after the ticker source date.

### PH11-003 - Persisted replay bypasses the documented confirmation requirement

Severity: Medium

Evidence:

- Config declares replay disabled by default and promotion confirmation required
  (`config/setup_lifecycle.yaml:401-406`).
- Replay route accepts `persist: bool = False`; when true it calls replay and commits
  (`app/routers/setup_lifecycle_routes.py:419-438`).
- Persisted replay creates a `REPLAY` evaluation run without requiring a confirmation token, reason,
  or separate admin action (`app/services/setup_lifecycle/replay_service.py:57-87`).
- Cross-section config validation rejects missing explicit admin action but does not reject
  `promotion_requires_confirmation=False`
  (`app/services/setup_lifecycle/config.py:662-688`).

Impact: Replay still does not silently mutate live lifecycle state, which is good. But it can create
persisted research state through a single query flag, short of the Phase 11 "promotion requires
explicit confirmation" expectation.

Recommendation:

- Require `confirm=true`, a preview token, requester, and reason for persisted replay.
- Add a second-step promotion route if replay output ever becomes live-authoritative.
- Validate `promotion_requires_confirmation` in config.

### PH11-004 - Purge execution exists despite retention config disabling purge

Severity: Medium

Evidence:

- Retention config says keep immutable evidence indefinitely and `purge_enabled: false`
  (`config/setup_lifecycle.yaml:408-413`).
- Config validation rejects `purge_enabled=true` in this phase
  (`app/services/setup_lifecycle/config.py:669-672`).
- Repository still exposes `preview_purge` and `execute_purge`; `execute_purge` deletes snapshots,
  episodes, lifecycle events, signal-change events, alert events, and evaluation runs when supplied
  a matching token (`app/services/setup_lifecycle/repository.py:754-780`).

Impact: No route currently exposes this directly in the reviewed setup lifecycle routes, but any
future or internal caller can bypass the disabled retention policy by calling repository methods.
That weakens retention and audit guarantees.

Recommendation:

- Move purge behind a service that checks `config.retention.purge_enabled`, preview required,
  confirmation required, and audit required before deletion.
- Make repository delete helpers private implementation details with no policy bypass.
- Add a test proving purge execution is rejected while config disables purge.

### PH11-005 - Lifecycle invariants lack property-based coverage

Severity: Low

Evidence:

- The setup lifecycle test package has broad example tests for lifecycle states, adapters,
  hysteresis, terminal locks, canonical tie-breaking, replay, alerts, and maintenance.
- Search found no Hypothesis/property-test usage in `tests/setup_lifecycle`.

Impact: The most important scenarios are example-covered, but state machines benefit from generated
transition sequences that can uncover invalid edge cases such as repeated dates, non-monotonic
evidence, missing observations, and terminal reopen attempts.

Recommendation:

- Add property-based tests for random sequences of snapshots and observation gaps.
- Assert invariants: one active episode per family, terminal episodes never reopen, state age never
  decreases except on state transition, current_as_of_date never moves backward, duplicate evidence
  does not create duplicate events, and replay does not mutate live state.

## Positive Controls

- Config validates all lifecycle states, terminal state set, and transition precedence coverage
  (`app/services/setup_lifecycle/config.py:300-316`).
- Signal registry separates close-authoritative trigger crossing from diagnostic high crossing
  (`config/setup_lifecycle.yaml:1-9`,
  `app/services/setup_lifecycle/config.py:683-688`).
- Canonicalization tie-breaking is deterministic and audited when selection changes
  (`app/services/setup_lifecycle/canonicalization.py:74-112`,
  `app/services/setup_lifecycle/canonicalization.py:189-197`).
- Signal-change detection dedupes retry processing by stable source event key
  (`app/services/setup_lifecycle/change_detector.py:92-140`,
  `tests/setup_lifecycle/test_change_detector.py:114-136`).
- Database constraints protect canonical-day uniqueness, active episode uniqueness, lifecycle
  event dedupe, signal-change dedupe, and alert dedupe (`app/models/tables.py:2321-2343`,
  `app/models/tables.py:2413-2425`, `app/models/tables.py:2491-2501`,
  `app/models/tables.py:2558-2564`, `app/models/tables.py:2603-2653`).

## Exit Criteria

| Criterion | Status | Notes |
| --- | --- | --- |
| Invalid transitions are impossible or rejected | Partial | Terminal reopen is rejected and many transitions are deterministic, but there is no explicit allowed-transition matrix enforcement and no property-based sequence testing. |
| Reprocessing the same evidence is idempotent | Partial | Signal changes and alerts dedupe; same-run snapshot capture is idempotent. Revised source data can overwrite the prior snapshot instead of preserving a revision. |
| Replay cannot silently mutate live research state | Partial | Dry-run replay is isolated and persisted replay creates a parallel replay evaluation run. The API still permits persisted replay with only `persist=true` and no explicit confirmation token/reason. |
| Episode identity remains stable where intended and splits where required | Partial | One active episode per family is enforced and cooldown exists; add tests for revised source data, family switches, and out-of-order dates. |
| Alert dedupe, acknowledgement, stale alerts, and disabled-alert behavior are tested | Partial | Dedupe/cooldown/ack/dismiss are tested. Stale alert expiry and full-pipeline disabled-alert behavior need coverage. |
| All state changes include source cutoff, calculation version, configuration hash, and evidence ids | Partial | Versions/config hashes/evidence ids exist. Source cutoff should be explicit and future context should be excluded before capture. |

## Recommended Next Actions

1. Make setup signal snapshots append-only by source hash/revision while retaining retry idempotency.
2. Add as-of-safe market/sector context loading for setup lifecycle capture.
3. Gate persisted replay behind explicit confirmation, reason, requester, and audit metadata.
4. Route purge through a retention policy service that rejects execution while purge is disabled.
5. Add property-based state-machine tests for generated snapshot/gap sequences and duplicate/revised evidence.
