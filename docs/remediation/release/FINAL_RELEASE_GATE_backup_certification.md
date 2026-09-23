# Final release gate and authoritative backup certification

Verdict: **PASS**. No authoritative migration, runtime start, pipeline, live canary, or push occurred in this gate.

## Code and authority

Local `main` was clean and fast-forwarded from `d5066e979ac3958c7e5a4cd125c08106856f1026` to certified remediation commit `e529e5b73b177cf1e94abbd07a99d3c2c807a010`; the merge base was `d5066e9`. `origin/main` remained `febe376be67f01589199cd3ba55af98fd54001ed`. The Phase 5/6/7 chain is reachable from local `main`, with no cherry-pick or push.

The [final source freeze](FINAL_RELEASE_source_freeze_v2.json) records the ordered 680 implementation and 463 test paths, their committed Git blob IDs, tree `c8040fdf7de23a92dfe753ecf0e686fba9eb0008`, migration head `0083_winner_scope_truth`, and tool version `release-remediation-1`. Its canonical V2 fingerprint is `cfaec2e02c0cb9c37c83251ad309575f3f2fa118876badec8678af42bbd41682`. Recalculation from `e529e5b` matched; the LF/CRLF checkout and true committed-byte-change tests passed. The tree and migration head are supplementary metadata; the canonical fingerprint follows the frozen V2 tool payload. Historical Phase 5/6/7 legacy checkout-byte pins remain immutable and are reconciled to committed blobs, not rewritten.

The focused Phase 5/6/7 certificate lane passed 36/36. The current Phase 5 derivative retains 252 callers and 220 semantic families with zero unknown or bypass classifications. The 70-finding ledger machine-counts 63 CLOSED, 7 PARTIAL, 0 OPEN, no lost finding, and zero active supported-current defects. The seven PARTIAL items remain historical/external authority boundaries.

The certification files are a docs-only attestation successor on local `main`. Because the V2 aggregate intentionally includes the commit ID, the frozen fingerprint identifies the tested release-code commit `e529e5b`; a later docs-only commit does not re-identify those same source/test blobs as a new tested code baseline.

## Disposable PostgreSQL 18 and regression

The `postgres:18` disposable instance reports PostgreSQL 18.6. A fresh database upgraded `0001 → 0083`, passed `alembic check` with no model drift, downgraded `0083 → 0080`, and re-upgraded `0080 → 0083`. The focused protection and certificate tests passed. Repository-wide Ruff, formatting on the 18 remediation-changed Python files, `compileall`, route inventory, tracked-secret scan, single Alembic head, and `git diff --check` passed.

After certification, the explicitly named disposable container and its anonymous PostgreSQL volume were removed. It had no host bind mount. The retained host backup was rechecked afterward at the same size and SHA-256.

The 15-node Winner caller module passed in explicit reverse collection order (15/15). The one-shot full non-E2E regression passed: 4,038 passed, 3 documented skips, 38 marker deselections, and zero failures in 8,061.36 seconds. The separate E2E run passed 28/28 with zero failures in 801.17 seconds, without concurrent pytest.

## Read-only authoritative backup

Before backup, the authoritative `127.0.0.1:5432/swinglens` server reported PostgreSQL 18.3, revision `0080_effective_configuration`, size 8,391,153,343 bytes, no active SwingLens writer sessions, and no queued/running/recovering jobs. The authoritative runtime was stopped. The canonical backup workflow discovered compatible `pg_dump 18.3` automatically without a PATH edit or exposed password.

The retained recovery artifact is `C:\Users\Ivica\Documents\SwingLens\backups\swinglens_final_release_gate_pre_0081_20260923_122912.dump`, a PostgreSQL custom-format dump created at `2026-09-23T11:00:08.7521387Z`, 809,809,835 bytes, SHA-256 `acba22199a8bac158b4eb72a824bcf5b52075858e850b9a16d8b4dd0aa74703a`. `pg_restore --list` succeeded. The ignored, non-temporary `backups` directory also retains `.metadata.json`, pre/post source evidence manifests, and validation reports. No backup was overwritten, removed, uploaded, or committed. The source pre/post manifests match on revision, counts, and content hashes for all 20 protected tables (3,569,855 rows).

## Exact restore and real-data migration rehearsal

The exact new dump restored into isolated `swinglens_pytest_authoritative_restore` on PostgreSQL 18.6 at `127.0.0.1:55433`. Before migration it was at `0080`; repository restore validation passed, including critical-table counts, foreign keys, and required evidence hashes. Source and restored logical schema inventories match: 118 tables, 2,307 columns, logical digest `b7a84e16c590da9ab2f71df0f9a79a3ac385993312c04cb97893a68c4f99999d`. PostgreSQL rebuilt 29 physical column ordinals where old dropped-column slots existed; names, order, types, nullability, and defaults of logical columns matched.

The first content-manifest comparison used the container's default UTC session and showed false hash mismatches for timestamp-with-time-zone rendering. The authoritative source session uses `Europe/Berlin`; with that timezone pinned on the disposable verifier, the 20-table comparison passed with zero row-count or content-hash mismatches. Both diagnostic and successful reports are retained; this is a serialization boundary, not changed stored instants.

On that restored copy only, `0080 → 0081_scope_refresh_identity → 0082_pipeline_ceri_scope → 0083_winner_scope_truth` succeeded. `alembic current`, `alembic check`, and post-migration restore validation passed. Read-only Phase 6/7 real-data checks found four new empty authority tables, nullable new bindings with zero pre-existing rows populated, the intended `required_for_parent_completion=false` value on all 42,762 historical jobs, 12 enabled immutability triggers, and zero unvalidated foreign keys. Original-`0080`-column hashes match the backup manifest across all 3,569,855 protected rows: **unexpected protected-evidence mutations = 0**. No worker or external provider was connected to the clone.

The authoritative recovery boundary is the checksum-verified pre-`0081` full backup plus certified release commit identity. A production Alembic downgrade is **not** the default rollback mechanism merely because the disposable downgrade path works.

## Readiness and hard boundary

The externally started `ibgateway` process accepted a local connection on port 4002; provider login or order capability was not asserted. Controlled-window `verify` passed with Winner automatic maturation, automatic cohort refresh, and market-data prewarm disabled while the explicit pipeline, CERI, Winner pipeline capture, scope/refresh, configuration authority, readiness, and mutation fencing remain enabled. Neither the Gateway nor SwingLens was started or stopped by this gate.

The final read-only authoritative check again found PostgreSQL 18.3 at `0080_effective_configuration`, no other database sessions, zero queued/running/recovering jobs, and no listener on runtime port 8000. IB Gateway still accepted a local connection on 4002, and controlled-window `verify` remained PASS.

`SAFE_TO_MIGRATE_AUTHORITATIVE_DB`: **YES**. `READY_FOR_MIGRATION_STARTUP_IDLE_CERTIFICATION`: **YES**. These are readiness findings, not authorization: a later prompt must separately authorize authoritative migration and controlled startup. No live canary was run here.

Machine-readable details are in [the JSON certificate](FINAL_RELEASE_GATE_backup_certification.json). Operational reports and the retained dump remain under ignored `backups/` and must not be uploaded or committed.
