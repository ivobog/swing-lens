# Authoritative migration, controlled startup, and idle certification

**Result: PASS. `SAFE_FOR_ONE_LIVE_CANARY: YES`—after a fresh controlled restart and fresh IB preflight.** No pipeline or live canary was executed. The runtime was stopped canonically after observation because this task did not authorize an immediate canary. The PostgreSQL 18.3 authoritative database remains at `0083_winner_scope_truth`; the retained backup was neither replaced nor restored.

## Release and hard boundary

The certified implementation is commit `e529e5b73b177cf1e94abbd07a99d3c2c807a010`. The runtime started from local `main` HEAD `1b0878f7e60eb097bdd93fd6271a12aa0cdc5579`, whose delta from the certified commit is three release-attestation documentation files. All 1,143 certified implementation/test blob entries match the committed [V2 source freeze](FINAL_RELEASE_source_freeze_v2.json); canonical fingerprint `cfaec2e02c0cb9c37c83251ad309575f3f2fa118876badec8678af42bbd41682`. No application source code was edited in this task.

Before migration the worktree was clean, the authoritative endpoint was `127.0.0.1:5432/swinglens` on PostgreSQL `18.3`, and Alembic current was `0080_effective_configuration`. There were zero other database sessions, zero queued/running/recovering jobs, no SwingLens web or durable worker, and no port-8000 listener. The externally started IB Gateway was listening on port `4002` as PID `16520` (`ibgateway`), and the repository's read-only API handshake returned `IB_API_READY` with a current-time smoke response.

The retained [custom-format backup](../../../backups/swinglens_final_release_gate_pre_0081_20260923_122912.dump) was rechecked immediately before mutation: `809,809,835` bytes, SHA-256 `acba22199a8bac158b4eb72a824bcf5b52075858e850b9a16d8b4dd0aa74703a`, with its [evidence manifest](../../../backups/swinglens_final_release_gate_pre_0081_20260923_122912.evidence.json) present. `BACKUP_INTEGRITY: PASS`. The exact backup had already passed restore validation in the preceding gate. A fresh read-only pre-migration comparison matched all 20 protected tables and all 3,569,855 rows to that manifest.

## Authoritative migration and evidence

Each canonical `uv run alembic upgrade <revision>` invocation used a process-local `SWINGLENS_DATABASE_SAFETY_CONTEXT=AUTHORITATIVE_LOCAL`; no database URL or persistent `.env` setting was changed. Each intermediate head was confirmed before the next transition.

| Transition | Start UTC | End UTC | Result |
| --- | --- | --- | --- |
| `0080` → `0081_scope_refresh_identity` | 2026-09-23 14:42:37.864 | 14:42:42.786 | Exit 0; current confirmed |
| `0081` → `0082_pipeline_ceri_scope` | 14:42:59.410 | 14:43:04.899 | Exit 0; current confirmed |
| `0082` → `0083_winner_scope_truth` | 14:43:20.176 | 14:43:25.172 | Exit 0; current/head confirmed |

`alembic current` and the single repository head both report `0083_winner_scope_truth`. `alembic check` exited 0 with **“No new upgrade operations detected.”** Schema/model drift is therefore clear. The post-migration read-only proof reproduced the original-column SHA-256 hashes for all 3,569,855 protected rows: **zero unexpected protected-evidence mutations**. Thirty-one important table counts and all job/pipeline status distributions were unchanged. Four new scope/refresh/acquisition authority tables were empty; no historical row had a populated new semantic binding; all 42,762 historical jobs had the intended `required_for_parent_completion=false`; there were zero unvalidated foreign keys. Before startup, direct `0083`-specific quiescence confirmed zero other sessions and zero queued/running/recovering jobs. The older `0080`-specific boundary helper's aggregate `passed` flag is intentionally inapplicable after migration because it hardcodes `0080`; its constituent post-migration facts were verified independently.

Detailed local operational evidence is retained under ignored `backups/`: `swinglens_authoritative_migration_20260923.pre.json`, `.post.json`, and the four `swinglens_authoritative_idle_20260923.*.json` snapshots/comparison. These are not replacement backups and were not pushed.

## Controlled startup and idle behavior

The certified [controlled-window wrapper](../../operations/RELEASE_CONTROLLED_CANARY_WINDOW.md) passed `verify`, then `start` ran from 14:54:42 to 14:56:29 UTC and exited 0. Runtime instance `68d04db4f5d24c99b7869b59c5457890` and lifecycle configuration fingerprint `81e3f6267bae77b3f4f7f61379a257a14e54d4605c957f89c1be88fd3fb7d89b` bound the same repository, source HEAD, and authoritative database. The active process tree contained web PID `16292`, supervisor PID `19552` (launcher `6172`), and **one** supervised durable worker, PID `8444` (launcher `9908`), worker ID `local-worker-1`, instance `c8a25f5a2c474eb39f870bb6fe97a5ae`. There was no embedded or duplicate durable worker, stale lifecycle owner, or migration mismatch. The worker startup log records `NORMAL`, `0083`, certified Git SHA, CERI readiness, and the two Winner automatic flags as false.

Stored normal settings on this host are `true` for Winner auto maturation, auto cohort refresh, and market-data prewarm. The controlled effective settings were `false` for all three, while durable pipeline, Winner pipeline capture, Winner calculation, CERI batched workflow, and CERI provider ingestion remained `true`. The worker loop passes the false maturation flag into admission; cohort continuation paths and prewarm enqueue are gated by their respective settings. No persistent settings were modified. The explicit `POST /runs/{run_id}/pipeline` route remained present in the live OpenAPI document; it was **not** called.

After startup stabilized, the measured idle window ran **14:58:18.770–15:00:08.547 UTC (109.776 seconds)**. That crosses at least 54 two-second worker polls and 21 five-second heartbeat intervals. Beginning and ending active jobs were `0→0`; total jobs `42,762→42,762`; pipelines `151→151`. Every important table count, job-type/status distribution, pipeline status distribution, and protected-table PostgreSQL insert/update/delete counter was unchanged. Exactly one active worker existed at both snapshots; PID `8444`, instance, registration generation `77`, and start time were unchanged, while heartbeat and control-loop timestamps advanced and memory state stayed `OK`. There were no autonomous Winner, cohort, prewarm, CERI, publication, maintenance, or other business jobs and no protected business-state delta. The supervisor lease generation (`66` at startup) is a different counter from worker registration generation (`77`); comparing them would incorrectly suggest restarts. The supervisor log shows a single worker launch, and no child restart was observed during the interval.

Web `/health` and `/ready/core` returned HTTP 200; the latter reported `ok` with database and `0083` head. Web, worker, and supervisor `/metrics` all returned HTTP 200. Prometheus reported all three SwingLens targets `up`; Grafana `/api/health` was HTTP 200 and a query through its provisioned Prometheus datasource succeeded with three series. Application readiness was `degraded` **only** for the non-blocking disk warning: 12.5% free (63.8 GB observed), below the 15% warning threshold but above the 5% critical threshold. Core readiness and all canary-critical runtime checks were OK. This warning should be rechecked before the later canary.

IB's port and authenticated API readiness were checked separately. The read-only API handshake returned `IB_API_READY`, server version `176`, and a current-time response, including after SwingLens shutdown at 15:10:19 UTC. The runtime's `optional_unavailable` IB observability label while idle means the optional check is not run without an IB-required queued workload; it is not evidence of an API failure. The later `REQUIRE_IB` POST performs a fresh fail-closed preflight. No order was submitted.

## Later single-canary input and disposition

Read-only inspection identified normal completed upload run **`155`**: 100 source rows, 100 distinct representative tickers, source file present, no existing pipeline, and no active run job. The proposed future request, **not sent**, is:

```http
POST /runs/155/pipeline
Content-Type: application/x-www-form-urlencoded

market_data_policy=REQUIRE_IB
```

The route returns HTTP 303 to `/runs/155/pipeline/{pipeline_id}`. The separately authorized canary task should record that pipeline ID, root/background job ID, scope ID, refresh-cycle ID, acquisition-plan ID, and effective configuration anchor/reference from the resulting read-only status and authoritative records. It must use the controlled-window `start`, recheck core/worker/observability and a fresh `IB_API_READY`, submit **only one** request, then stop with the same wrapper.

Because no immediate canary was authorized, controlled `stop` ran 15:06:19–15:08:27 UTC and exited 0. A subsequent normal read-only lifecycle status was `STOPPED`, with no runtime or stale state; PostgreSQL remained `0083`, active jobs remained zero, and no protected table count or DML counter changed during shutdown. The external IB Gateway remained running. Prometheus/Grafana stopped with the lifecycle. **Runtime left running: NO. Controlled-window overrides active after stop: NO.** Live canary executed: **NO**. Push performed: **NO**. Production backup retained: **YES**.
