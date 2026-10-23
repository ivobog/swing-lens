# MSI activation fence

The optional `JOB_ACTIVATION_MIN_ID` and `PIPELINE_ACTIVATION_MIN_ID` settings
establish one fixed deployment boundary without rewriting historical jobs,
pipelines, contexts or immutable bindings. Both default to zero (existing
behavior) and must be configured together when enabled.

With application producers and workers stopped, record each operational table's
verified `MAX(id) + 1` in the protected deployment configuration. Keep these values
unchanged across restarts, worker replacement and retry. Do not recompute them on
startup: doing so would strand newly authorized queued work.

The existing execution-authority predicate excludes older job IDs and newer
descendants referring to older roots, parents or immediate triggering jobs.
Pipeline ownership still requires explicit ACTIVE, nonterminal authority, now
also bounded by the minimum pipeline ID. The claim and recovery paths share this
predicate, including watchdog fencing. Continuation reconciliation excludes old
pipelines. Readiness and lifecycle quiescence use the same execution scope;
historical findings remain visible as historical diagnostics.

The two settings enter the lifecycle generation fingerprint and worker startup
diagnostics. Removing or lowering a boundary can re-authorize historical work;
retain the protected activation configuration. This fence is operational scope,
not a replacement for frozen business configuration or calendar authority.

Native PostgreSQL tests cover the exact 49 QUEUED plus stale RUNNING job shape,
unchanged historical rows, late descendants of old roots, new ACTIVE pipeline
work, claim/heartbeat/progress/retry/recovery, historical handoff exclusion and
exactly-once new continuation creation. The operational activation receipts,
configured boundaries and provider results remain outside Git.
