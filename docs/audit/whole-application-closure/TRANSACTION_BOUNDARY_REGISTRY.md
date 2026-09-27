# Transaction-boundary registry

Mechanical AST search found 276 production functions containing `commit`, `rollback`, `flush`, `begin`, or transaction-context primitives. Most are leaf API/service boundaries. The 25 normalized boundaries below cover every pipeline, worker, recovery, continuation, publication, and autonomous path; the exact leaf-site census remains available in the T14A raw persistence artifacts.

At every `commit`, row/advisory locks end, database-trigger/event state becomes visible, and loaded ORM objects may be expired or stale. Python payloads, IDs, dataclasses, and pre-fetched bodies remain in memory but are not authority. A continuation must reload by durable identity and revalidate its authority.

| ID | Boundary and exact owner | Locks released / state retained | Required reacquisition/revalidation | Continuation |
| --- | --- | --- | --- | --- |
| TX-01 | upload route -> `create_upload_run` -> route commit/rollback | upload/raw-row locks; request bytes/hash retained | reload UploadRun by ID/hash | pipeline POST is separate request |
| TX-02 | `pipeline_service.start_pipeline` / route commit | pipeline/job/idempotency locks; returned ORM IDs | active-job/coalescing, pipeline config/scope, certification root plan/session | worker claim |
| TX-03 | `background_worker.run_worker` registration commit; later startup processor/config commit | worker and SEC release locks; process IDs/settings retained | worker row by ID/instance; SEC release signature/status | enters loop |
| TX-04 | `run_worker_once`: recovery/registration/control heartbeat commit, scheduler commit, claim commit | job/worker locks; claimed ID/token retained | execution token + RUNNING owner; certification predicate was checked at claim | handler execution |
| TX-05 | FULL_PIPELINE handler main domain transaction and per-stage flush/checkpoints | stage/source/evidence locks on final commit; in-memory pipeline objects | pipeline/root token, calculation/config IDs, source bundles, cancellation | next stage in-process or CERI async handoff |
| TX-06 | IB plan/item batch commits and summary terminalization | item/contract/bar locks; plan/scope IDs retained | acquisition plan, scope, refresh cycle, contract/source identity | return to pipeline/repair |
| TX-07 | SEC readiness repair progress/provider batch boundaries | filing/source/processor locks; progress payload retained | active processor signature, filing/source hashes, pipeline cancellation and scope | `enqueue_pipeline_after_sec_repair` |
| TX-08 | CERI provider ingest job transaction/batches | ingestion/source/request locks; payload IDs retained | provider/dataset/request key, PIT cutoff, job token | normalize enqueue |
| TX-09 | CERI normalization batch/job | source/normalized/revision locks | exact source row content, current revision under lock, cutoff | feature enqueue |
| TX-10 | CERI feature batch/job | feature/build-state locks | company/run/cutoff and exact normalized source pins | finalizer/capture enqueue |
| TX-11 | CERI capture | score/evidence locks | calculation identity, source bundle, run/session/cutoff | change enqueue |
| TX-12 | CERI change detection (currently one handler transaction across company chunks) | locks held until terminal handler commit; chunk Python data retained | source-bundle event revalidation immediately before commit; job token/cancel | alert enqueue |
| TX-13 | child finalizer/continuation enqueue commits | child/pipeline/job locks; root/pipeline IDs retained | durable child terminal state, root causality, session marker, active continuation uniqueness | FULL_PIPELINE continuation |
| TX-14 | preflight consume and decision-handoff freeze | plan/manifest locks | exact plan status/session and complete frozen evidence/config/cutoff | setup/lifecycle stages |
| TX-15 | setup capture/lifecycle/alert transaction | signal/episode/evidence locks | handoff manifest, immutable evidence, original-context decision | Winner stage or maintenance terminalization |
| TX-16 | Winner capture transaction | prediction/estimate/evidence/model locks | model generation, manifest membership, calculation/run identity | pipeline terminal/maturation enqueue |
| TX-17 | pipeline and job terminalization commit | pipeline/step/job locks | execution token; recompute final child/step statuses | downstream scheduler may observe terminal state |
| TX-18 | Winner maturation/revision/backfill job transaction and continuation commit | prediction/outcome/obligation locks | prediction contract, outcome definition, market session/price authority | next maturation/revision/cohort job |
| TX-19 | cohort generation/materialization/publication transactions | generation/publication/advisory locks | evidence watermark, generation status, exact model/evidence membership | published read model |
| TX-20 | CERI/setup alert persistence and ack/dismiss route commit | alert/rule locks | source change/lifecycle/rule evidence or direct target row | no automatic continuation except CERI pipeline release |
| TX-21 | control heartbeat independent commits | job/worker row locks only; domain session may remain open | exact execution token; must not infer business authority from heartbeat | handler continues |
| TX-22 | supervisor acquire/heartbeat/fence/reconcile/shutdown commit | supervisor/worker/job/pipeline-step locks | worker instance/process liveness and certification claim where applicable | RECOVERING requeue/restart |
| TX-23 | worker stale/abandoned recovery and failure retry commit | job locks | lease expiry/worker identity/retry classification/cancel flag | later claim |
| TX-24 | periodic evidence retention/admin cleanup commit | operational evidence/job-attempt locks | retention cutoff, terminal status, protected evidence refs | periodic loop |
| TX-25 | licensed-data purge preview/execute transactions | broad CERI artifact locks | provider/license scope, preview manifest hash, confirmation token, immutable decision refs | rebuild required flag only |

## Rollback paths

| Rollback family | Owners | Consequence |
| --- | --- | --- |
| Handler exception | `run_worker_once`, route/service boundaries | domain writes roll back; worker then records retry/failure in a valid transaction |
| Lease loss | worker catches `JobLeaseLost` | current session rolls back; no terminal write using stale token |
| Startup failure | worker registration/startup blocks | startup transaction rolls back; heartbeat loop is stopped where already started |
| Supervisor cycle failure | helper-local `SessionLocal` contexts | local reconciliation/acquisition writes roll back; loop reports and retries |
| Provider/batch failure | IB/CERI handlers | batch-specific checkpoints determine replay scope; PARTIAL may be committed intentionally |
| Publication conflict | cohort/estimate/model services | advisory/row lock transaction rolls back; prior publication remains authoritative |

## Critical boundary analysis

### Source bundle revalidation

`PrefetchedSourceBodies` retains exact primary-key addresses and canonical bodies. SQLAlchemy connection events mark bundles dirty after intervening SQL; before the final domain commit the bundle refreshes the exact rows under `FOR UPDATE` and compares canonical fingerprints. The current working tree chunks those exact-address refresh queries below a 50,000-parameter budget. This is the path Run 166 exposed: a valid pre-checkpoint source bundle is not authority after a commit or intervening mutation.

### Heartbeats

The FULL_PIPELINE heartbeat may use an independent control session so lease renewal does not wait on long calculations. When the domain fence already owns the job row, it writes through the fenced session instead. A heartbeat commit proves liveness only; it does not refresh source/config/evidence authority.

### CERI company chunks

The current CERI change implementation chunks data loading and progress reporting but intentionally does not commit each company chunk. This avoids partial change publication and keeps source-bundle validation atomic, at the cost of one potentially long transaction. Progress heartbeats written in the same transaction are not externally visible until commit, so supervisor timeout policy remains material.

### Continuations

Every CERI, SEC, Winner, and pipeline continuation crosses a commit. Payload ancestry is not sufficient authority; the new handler must reload durable pipeline/root state, exact session marker, identities, source/evidence pins, and cancellation/terminal status. Enqueue-time coalescing prevents duplicate rows but does not replace revalidation.

## Transaction findings

- GAP-003: recovery/session authority is optional at shared lower-level boundaries.
- GAP-004: CERI alert rebuild loads globally before the terminal transaction and therefore creates memory/latency risk.
- GAP-005: purge builds a broad in-memory manifest before mutation, increasing time between authority preview and locked application despite hash recheck.
- GAP-010: the change job's progress heartbeat is same-transaction and may not advance the externally observed progress timestamp during a long chunk; chunk size currently bounds but does not prove timeout safety under production payload size.
