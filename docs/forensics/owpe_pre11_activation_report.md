# OWPE Pre-1.1 Activation Report

## Executive result

The reviewed pre-1.1 training bridge was activated without rewriting historical prediction snapshots, historical target/stop labels, or original `DECISION_TIME` estimates. Migration `0046_owpe_pre11_training_compatibility` was the only schema migration applied. The exact reviewed write produced 8,859 append-only eligibility decisions and 390 append-only replay labels. The reviewed membership projection hash and request key matched exactly.

Finding: `CONFIRMED_EXPECTED_BEHAVIOR` (P0 gate passed). The production write is the reviewed scope, is idempotent, and remains auditable.

## Environment and pre-write gate

- Branch: `codex/ceri-run101-remediation`
- Pre-write git commit: `04684a145b62daefb54eebf106b870591b8b02dc`
- Database: PostgreSQL 18.3, database `swinglens`, user `postgres`, server timezone `Europe/Berlin`
- Alembic before write: `0045_ceri_changes_alerts_semantics`
- Alembic after migration: `0046_owpe_pre11_training_compatibility`
- `winner_probability_auto_maturation_enabled=false`
- Pre-write artifact: `docs/forensics/owpe_pre11_activation_prewrite_snapshot.json`

The reviewed dry run was rerun before any write and returned 8,859 considered snapshots, 2,854 PIT-valid snapshots, 715 replay-possible snapshots, and exactly 390 final eligible members. Its reviewed manifest hash was:

```text
dda5048538702f6eb9ae42f2aebefc86f19b988e3f2aa494e34900f27d462f54
```

No material environment drift from the reviewed dry run was found.

Implementation anchors:

- `app/services/winner_probability/pre11_activation_service.py:51` — fail-closed exact L5 activation;
- `app/services/winner_probability/pre11_activation_service.py:219` — reviewed membership projection/hash;
- `scripts/owpe_pre11_activate_l5.py:1` — explicit preview/write activation command;
- `scripts/verify_owpe_pre11_activation.py:1` — read-only reconciliation and rollback-only trigger probes.

## Migration 0046

Applied command:

```text
alembic upgrade 0046_owpe_pre11_training_compatibility
```

Verified after migration:

- both compatibility tables exist;
- eight table indexes exist;
- ten compatibility/evidence-member foreign keys exist;
- `trg_winner_training_decision_append_only` exists;
- `trg_winner_training_replay_append_only` exists;
- rollback-only `UPDATE` and `DELETE` attempts against both ledgers were rejected;
- no Winner row count or Run 104/105 original checksum changed as a result of the migration.

Finding: `CONFIRMED_EXPECTED_BEHAVIOR` (P0). The installed DDL matches the reviewed migration and enforces append-only storage.

## Exact production write

The write was invoked with explicit approval, actor, manifest path, request key, family, definition, source/target identities, cutoff, and bounded date range. The effective scope was:

| Field | Exact value |
|---|---|
| Training family | `OWPE_1_1_COMPAT_V1` |
| Eligibility policy | `owpe-pre11-eligibility-1.0.0` |
| Compatibility bridge | `owpe-training-compat-1.0.0` |
| Replay policy | `owpe-pre11-replay-1.0.0` |
| Replay method | `PRE11_TO_11_TRAINING_REPLAY` |
| Outcome definition PK | `3` |
| Outcome definition | `T2_5_S2_0_H5_NEXT_OPEN` |
| Target calculation | `owpe-calc-1.1.0` |
| Target config | `218a897655d6c42e19043e1136cb4d578705632f13acf037bc9ce1beef57b527` |
| Source calculation | `owpe-calc-1.0.0` |
| Source config | `2260060ab44d6f46ccff94d61943bbdfcaa49b734ef2ccf177b71dc50f225184` |
| Feature schema | `owpe-features-1.0.0` |
| Cutoff | `2026-08-14T22:34:33.408271+02:00` |
| Date range | `2021-08-14..2026-08-14` |
| Request key | `d555a6b40f63092527b7997932686342e1be32d0582cbfe5d49f71122c6afa8c` |
| Reviewed hash | `dda5048538702f6eb9ae42f2aebefc86f19b988e3f2aa494e34900f27d462f54` |
| Actor | `codex/owpe-pre11-activation-20260815` |

Persisted result:

| Artifact | Count / identity |
|---|---:|
| Eligibility decisions | 8,859; IDs 1..8,859 |
| Approved decisions | 390 |
| Replay rows | 390; IDs 1..390 |
| Replayed wins | 145 |
| Maturation jobs created | 0 |
| Pending H5 rows through 2026-08-13 | unchanged at 1,111 |

An exact retry returned the same logical result while physical counts and ID ranges remained unchanged. Wrong family, wrong request key, manifest mismatch, missing actor, unbounded scope, and absent approval are rejected by the controlled command/service and regression tests.

## Stop-and-reconcile evidence

`scripts/verify_owpe_pre11_activation.py` returned every check `true`:

- decisions = 8,859;
- approved = 390;
- replays = 390;
- wins = 145;
- request key and reviewed hash each have cardinality one and equal the reviewed values;
- append-only `UPDATE`/`DELETE` checks reject writes.

Run immutability was evaluated specifically on original decision-time artifacts:

| Run | Snapshot count/checksum | Original `DECISION_TIME` estimate count/checksum |
|---|---|---|
| 104 | 186 / `55b12cf70c532a120ea8c9ef4d7a31ce` | 184 / `d0b8383e293fe5911f47d1cf70c9630d` |
| 105 | 186 / `1e872fe9d3620a320ea8c9ef4d7a31ce` | 184 / `4783fa1f5675f8e25b02c41dbfd773c1` |

These values match the pre-write snapshot. One explicitly authorized `LATEST_RESCORE` estimate was later appended to Run 105 as the controlled L5 serving proof; it did not modify any original `DECISION_TIME` row.

No legacy target/stop row was relabeled, no historical training Boolean was mass-updated, and automatic maturation remained disabled throughout.

## Safety and remaining risks

- `CONFIRMED_EXPECTED_BEHAVIOR` (P0): exact reviewed membership only; no scope widening.
- `CONFIRMED_EXPECTED_BEHAVIOR` (P0): original Run 104 and Run 105 decision-time artifacts are unchanged.
- `CONFIRMED_EXPECTED_BEHAVIOR` (P1): the remaining 1,111 reviewed pending H5 rows were not matured or drained.
- `DATA_QUALITY_ISSUE` (P2): fresh Run 106 added later-due pending outcomes, so the total pending count increased while the protected through-2026-08-13 population stayed exactly 1,111.

## Verification

Focused OWPE, pipeline, background-job, settings, and ranking suite: 269 passed. Migration/schema tests are included in that slice. Exact replay-lineage tests cover changed bars and revisions observed after the replay cutoff.

Certification state: `PASS_WITH_NONBLOCKING_FINDINGS`
