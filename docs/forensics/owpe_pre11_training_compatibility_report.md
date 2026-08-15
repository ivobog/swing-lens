# OWPE Pre-1.1 Training Compatibility Report

## Executive verdict

The append-only compatibility and active-label replay path is implemented and testable, and the production database has been classified read-only. Exactly **390** native pre-1.1 snapshots would be admissible to `OWPE_1_1_COMPAT_V1`. No production writes, migrations, replays, maturations, cohort rebuilds, estimates, or fresh runs were executed.

The proposed set is statistically large enough for an L5 cohort, but that is not yet a production certification. The migration and reviewed two-step write remain unapplied; no persisted mixed-source cohort exists; no served probability has been certified. Automatic maturation remains disabled.

## Authority and environment

Read completely before implementation:

- `docs/forensics/run104_winner_evidence_forensic_report.md`
- `docs/forensics/owpe_post_run104_remediation_report.md`
- `docs/forensics/owpe_fresh_run_certification.md`
- the available unnumbered SRS and SDD Word files (the requested `(2)` filenames were not present)
- `swinglens_owpe_pre11_training_recommendations.md`

The SRS/SDD rule that historical features may not be reconstructed from today's mutable source rows is enforced. Classification reads only `WinnerPredictionSnapshot.feature_json`, immutable source IDs, its cutoff audit, and snapshot lineage. Historical `RankingResult`/sector values are not synthesized.

- Git baseline at investigation start: `3e45cf4`
- Live schema head: `0045_ceri_changes_alerts_semantics`
- Proposed, unapplied head: `0046_owpe_pre11_training_compatibility`
- Database: PostgreSQL
- Active target outcome-definition PK: `3`
- Run 105 cutoff: `2026-08-14T22:34:33.408271+02:00`
- Active config hash: `218a897655d6c42e19043e1136cb4d578705632f13acf037bc9ce1beef57b527`
- Source pre-1.1 config hash: `2260060ab44d6f46ccff94d61943bbdfcaa49b734ef2ccf177b71dc50f225184`

## Findings

### F-01 — Append-only decisions and replay labels

**CONFIRMED_EXPECTED_BEHAVIOR — Severity: P0 control implemented.**

`WinnerTrainingEligibilityDecision` and `WinnerTrainingOutcomeReplay` were added in [tables.py](../../app/models/tables.py). Migration `0046` creates both ledgers, adds origin/decision/replay identity to estimate members, and installs database triggers that reject UPDATE and DELETE. A changed classification is a new revision with `supersedes_*_id`; the latest visible decision is selected before `training_allowed` is evaluated, so a later rejection suppresses an older approval.

The decision persists source and target calculation/config/schema identities, policy/family/bridge, structured feature and config classifications, outcome/PIT/episode/quality status, reason codes, source/evidence hashes, request key, revision, actor, and timestamp. The replay persists the active target semantics, exact entry/due dates and prices, target/stop result, source forward identity, all five `PriceBar` PK/hash/revision identities, lineage hash, and source revision cutoff.

No original prediction, old target/stop row, old training Boolean, or `DECISION_TIME` estimate is updated.

### F-02 — Independent PIT validation is decisive

**CONFIRMED_DEFECT — Severity: P0 historical data exclusion.**

All 8,859 source snapshots claim native capture and stored PIT validation, but only 2,854 pass independent U.S.-calendar reconstruction. The classifier in [pre11_compatibility_service.py](../../app/services/winner_probability/pre11_compatibility_service.py) requires:

```text
prediction_as_of_date == latest_completed_session(source_data_cutoff_at)
planned_entry_session == next_regular_session(prediction_as_of_date)
```

It rejects 6,005 rows as `PIT_SIGNAL_SESSION_MISMATCH`. This prevents the append-only bridge from laundering the Run 104/session-timezone defect into 1.1 training.

### F-03 — Semantic config bridge is valid for one exact source identity

**CONFIRMED_EXPECTED_BEHAVIOR — Severity: P0 control implemented.**

The mapper does not require literal old/new config-hash equality. It allow-lists the exact pre-1.1 source calculation/schema/config and records bridge version `owpe-training-compat-1.0.0`. Target, stop, five-session horizon, `NEXT_OPEN`, and `CONSERVATIVE_STOP_FIRST` must match the active target exactly. Target episode, rolling-window, quality, cohort, and display-threshold policies are applied at training/serving time and retain the target config identity.

This is not a rename of outcome-definition 1 to definition 3. A successful row receives a separate definition-3 replay artifact with `PRE11_TO_11_TRAINING_REPLAY` provenance.

### F-04 — Missing ranking/sector is level-specific, not fabricated

**CONFIRMED_EXPECTED_BEHAVIOR — Severity: P1.**

Every one of the 390 eligible members lacks ranking and sector context. Those features are persisted as `OPTIONAL_MISSING_ALLOWED`; they cannot match levels whose dimensions require ranking or sector, but they remain eligible for L5 and other levels that do not require the absent dimension. No current `RankingResult` or sector row is read to fill history.

### F-05 — Exact replay and revision lineage is reproducible

**CONFIRMED_EXPECTED_BEHAVIOR — Severity: P0 control implemented.**

The replay uses the approved U.S. calendar, `NEXT_OPEN`, entry-as-session-1 five-session counting, +2.5% target, -2.0% stop, and conservative stop-first resolution. It requires all five bars on the exact sessions, one adjustment basis, positive OHLC values, a current source forward identity, and bar value revisions visible before the training cutoff.

The 390 proposed members contain 1,950 bar positions (1,739 unique `PriceBar` PKs), all with data hashes. There are 473 references to revised bars; all 473 resolve to exact `PriceBarRevision` PKs. An actual revision after the cutoff fails closed. A later unchanged “last seen” refresh does not falsely become a value revision.

### F-06 — Pending backlog must not be drained for this scope

**CONFIRMED_EXPECTED_BEHAVIOR — Severity: P1.**

The historical count of 1,111 pending H5 rows through due session 2026-08-13 is reproduced. Although 337 belong to otherwise compatible snapshots, zero have a complete replayable five-session bar set and zero become final eligible members. All 1,111 are outside the approved write requirement. No maturation was run.

### F-07 — Candidate L5 is mathematically sufficient but not production-persisted

**CONFIRMED_EXPECTED_BEHAVIOR — Severity: P0 proof, pending write authorization.**

The read-only set has n/effective-n 390, wins 145, raw rate 0.3717948718, posterior 0.3780487805 under Beta(10,10), interval [0.331112, 0.424986], width 0.093874, projected grade High. Its dates span 2026-08-04 through 2026-08-06 and runs 78, 85, and 86.

These numbers are not stored cohort output. They must be reproduced after the reviewed write from persisted decisions, replay rows, evidence members, and manifest before any probability can be certified.

### F-08 — Current dependence does not suppress serving

**CONFIRMED_EXPECTED_BEHAVIOR — Severity: P0 regression protected.**

Evidence membership applies independence to historical members. It does not gate service on the fresh current prediction's `dependent_episode` value. A new regression test proves a dependent current prediction receives a non-null cohort estimate when 15 independent historical members satisfy the active threshold.

## Architecture changes

| Component | Change |
|---|---|
| `app/models/tables.py` | New append-only decision/replay ORM models; evidence-member origin/decision/replay identities |
| `alembic/versions/20260814_0046_owpe_pre11_training_compatibility.py` | New tables, indexes, FKs, append-only triggers; not applied |
| `pre11_compatibility_service.py` | Scoped classifier, semantic bridge, replay preview, deterministic request/manifest hashes, guarded write service |
| `scripts/owpe_pre11_training_compatibility.py` | Required `dry-run` or explicit `write`; all scope arguments mandatory |
| `evidence_service.py` | Native 1.1 UNION latest approved pre-1.1 replay; all existing cutoff/quality/window/episode gates retained |
| `evidence_manifest_service.py` | Per member decision ID, replay ID, and `NATIVE_1_1`/`PRE11_COMPAT_REPLAY` origin |
| `probability_estimator.py` | Native/replay composition counts, policy version, and evidence date range on statistics/estimates |
| `reproduction_service.py` | Rebuilds replay members and verifies the persisted eligibility decision remains allowed |
| `api_service.py` / Winner table | Explicit evidence-composition object and compact `native / pre-1.1 replay` display; member payloads expose origin/decision/replay IDs |

## Dry-run proof

The authoritative artifact is [owpe_pre11_dry_run_manifest.md](owpe_pre11_dry_run_manifest.md).

```text
historical considered              8,859
native                             8,859
PIT valid                          2,854
prediction eligible                2,845
lineage sufficient                 2,845
feature compatible                 2,845
config-semantics compatible        2,845
outcome replay possible              715
quality allowed                      715
independent representatives          390
inside rolling window                390
final training eligible              390
```

Deterministic request key: `d555a6b40f63092527b7997932686342e1be32d0582cbfe5d49f71122c6afa8c`.

Reviewed-set hash: `dda5048538702f6eb9ae42f2aebefc86f19b988e3f2aa494e34900f27d462f54`.

The SQLAlchemy unit of work remained `new=0`, `dirty=0`, `deleted=0` and was rolled back after each census.

Final live safety check: `alembic_version` remains `0045_ceri_changes_alerts_semantics`, both proposed compatibility tables are absent (`count=0`), and no `WINNER_OUTCOME_MATURATION` job was created after the Run 105 cutoff (`count=0`).

## Safety controls and proposed write scope

The write service rejects absent approval, absent/incorrect request key, absent reviewed manifest, manifest-hash mismatch, missing actor, wrong training family, missing outcome definition, timezone-naive cutoff, and unbounded/reversed date range. Decision/replay hashes make retry idempotent. Broad unscoped replay is not expressible through the command.

If separately approved, the exact proposed write is:

```text
training family: OWPE_1_1_COMPAT_V1
target outcome definition PK: 3
cutoff: 2026-08-14T22:34:33.408271+02:00
date range: 2021-08-14..2026-08-14
request key: d555a6b40f63092527b7997932686342e1be32d0582cbfe5d49f71122c6afa8c
reviewed manifest hash: dda5048538702f6eb9ae42f2aebefc86f19b988e3f2aa494e34900f27d462f54
decision rows: 8,859 (approved and rejected, preserving reason audit)
replay rows: 390 (approved members only)
pending H5 maturations: 0
historical snapshot/estimate mutations: 0
```

The migration should be applied in a controlled maintenance transaction first. The write should then run with an explicit actor and reviewed manifest, be reconciled by request key/hash, and be followed by L5-first materialization and exact reproduction before L4-L0 or a fresh run.

## Tests and results

Focused OWPE/calendar/settings suite:

```text
202 passed in 13.33s
```

New compatibility tests cover native compatible acceptance, reconstructed rejection, PIT rejection, required-feature rejection, optional ranking absence, semantic config acceptance despite literal hash mismatch, config rejection, quality rejection, episode dependence/deduplication, deterministic zero-write dry run, strict write approval/scope, exact holiday/NEXT_OPEN/H5 replay, conservative same-bar handling, PriceBar lineage, source immutability, append-only supersession, mixed evidence composition, and dependent-current serving.

Alembic reports a single head at `0046_owpe_pre11_training_compatibility`; offline PostgreSQL DDL generation succeeded. No migration was applied to the live database.

The full repository suite returned `1651 passed, 9 skipped, 2 failed` in 649.12 seconds. Both failures are outside the compatibility classifier/evidence path:

- the destructive browser certification fixture expected automatic/manual background maturation lineage and CERI alerts that were absent; automatic maturation is intentionally disabled by this task,
- the external-worker integration fixture timed out waiting for a disposable test worker to register.

The clean-PostgreSQL migration-to-head test passed inside that run. The two broad-suite failures are reported rather than hidden; neither authorizes enabling maturation or changing unrelated CERI/worker behavior.

## Unresolved risks and next approval boundary

- **CONFIRMED_EXPECTED_BEHAVIOR — P0 pending authorization:** no production decision/replay rows exist yet, so mixed-source evidence is not active.
- **CONFIRMED_EXPECTED_BEHAVIOR — P0 pending authorization:** L5/L4-L0 materialization and stored probability/interval/member-hash reproduction have not been executed.
- **CONFIRMED_DEFECT — P1:** Run 105's ranking pipeline has no automatic ranking stage; see the separate ranking investigation.
- **DATA_QUALITY_ISSUE — P1:** 2,130 otherwise compatible snapshots cannot replay the active label at the cutoff, and the approved scope fails closed rather than guessing.
- **CONFIRMED_EXPECTED_BEHAVIOR — P0:** automatic maturation remains off (`winner_probability_auto_maturation_enabled=false`).

This implementation is ready for code and dry-run-scope review. It is **not** a production-write approval and does not certify a production probability.

READY_FOR_REVIEW
