# Autonomous mutation registry

Eighteen mechanisms can mutate production state without a new main-pipeline POST. Read-only samplers/preflight are listed separately and are not included in that count.

| ID | Autonomous trigger | Job/action created | Conditions and scope | Production mutation | Certification behavior |
| --- | --- | --- | --- | --- | --- |
| AUTO-001 | supervisor loop startup/interval | acquire/heartbeat supervisor | process role, worker ID, lease | `background_supervisors` | enabled control-plane |
| AUTO-002 | supervisor sees absent/exited child | start/restart web or worker process | ownership + restart budget/backoff | process state, later registrations | enabled; child inherits mode/session environment |
| AUTO-003 | supervisor watchdog timeout | fence stalled jobs, interrupt steps, requeue | progress/worker heartbeat and stage timeout | jobs/pipeline steps/runs | current tree session-scoped |
| AUTO-004 | supervisor sees dead/stale/critical worker | reconcile/fence/requeue, retire registration | worker ID/instance/process state | jobs/pipeline/worker registration | current tree exact-session scope for jobs |
| AUTO-005 | supervisor shutdown | terminate child, reconcile/requeue, release supervisor | owned worker/supervisor | jobs/workers/supervisor | current tree exact-session scope for jobs |
| AUTO-006 | worker process startup | worker registration/heartbeat thread | process role/settings | `background_workers` | enabled control-plane |
| AUTO-007 | worker process startup | `register_deployed_processor` | every startup, deployed code signature | global `ceri_sec_processor_releases` | **still enabled and unscoped (GAP-002)** |
| AUTO-008 | NORMAL worker iteration | recover abandoned/stale jobs | lease/heartbeat/worker predicates | `background_jobs`, possibly pipeline steps/runs | disabled by worker branch |
| AUTO-009 | NORMAL worker interval | durable evidence retention | monotonic interval/settings | operational evidence/enqueue-attempt rows | disabled by worker branch |
| AUTO-010 | NORMAL worker iteration with setting | schedule primary H5 maturation | due prediction/outcome work | creates/coalesces Winner jobs | disabled by worker branch |
| AUTO-011 | `run_after` becomes due | worker claims retry/deferred/recovering job | queue/priority/time/status | all handler domains | claim predicate restricts exact session |
| AUTO-012 | CERI ingest/normalize/feature handler success | next CERI child job | payload scope, root causality, idempotency key | background jobs and CERI artifacts | only authorized descendants |
| AUTO-013 | CERI batched run finalizer/capture/change success | capture/change/alert jobs | durable child completeness | jobs/CERI artifacts | only authorized descendants |
| AUTO-014 | CERI alert completion | FULL_PIPELINE continuation | WAITING pipeline and child outcome | job/pipeline state | only authorized descendant; root must remain active |
| AUTO-015 | SEC readiness branch/repair completion | repair and FULL_PIPELINE continuation | missing SEC readiness, then durable repair | jobs/SEC data/pipeline | only authorized descendant |
| AUTO-016 | Winner maturation/revision handler | next continuation and possible cohort request | pending scope, depth, watermark | jobs/outcomes/cohorts | only authorized descendant; independent scheduler disabled |
| AUTO-017 | alert calculation/finalizers | CERI/setup alert persistence | new change/lifecycle evidence and rule match | alert tables | pipeline descendants authorized; direct admin ack/dismiss remain callable |
| AUTO-018 | classified database infrastructure failure before job claim | invalidate failed session, publish degraded worker state, and retry after bounded exponential backoff | SQLSTATE 53200/53/08, PostgreSQL unavailable/shutdown, or narrowly recognized connection loss before a claim exists | exact owned `background_workers` registration only; no job mutation | enabled control-plane behavior; certification claim isolation remains unchanged |

## Explicit non-pipeline user/admin triggers

These do not start autonomously, but they also do not require a main-pipeline POST: 52 mutating HTTP route registrations and 49 mutation-indicator CLI entrypoints. They cover upload, maintenance cleanup, CERI backfill/reprocess/purge/review, setup replay/repair/alert state, Winner capture/maturation/cohort/model retirement, IB fetch/intelligence/journal mutation, market prewarm, and gateway control. They are mapped in EXEC-023/024 and the writer registry.

## Read-only automatic mechanisms

| Mechanism | Actual behavior |
| --- | --- |
| FastAPI lifespan database health sampler | reads health/metrics; no pipeline-state writer found |
| resource/system samplers | reads process/database/job aggregates and exports metrics |
| `serve.run_startup_preflight` | explicitly read-only; does not run migrations |
| database monitor middleware/thread | observability output; no business-state writer |
| readiness checks | read state and may block admission; no repair unless explicit repair scheduler is invoked by pipeline |

## Hidden-writer conclusion

No additional startup, `atexit`, timer, or thread-based production writer was found. The two material surprises relative to previous lineage documents are supervisor recovery (AUTO-003..005) and worker SEC release registration (AUTO-007). AUTO-007 violates the certification disabled-workflow model even though its write is deployment metadata rather than a financial result.
