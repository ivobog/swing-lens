# Runtime process ownership decision

Status: accepted (2026-09-11)

SwingLens NORMAL durable operation has exactly one supported ownership topology, versioned as
`supervisor-root-v1`:

```text
swinglens.ps1 / lifecycle controller
  -> app.worker_supervisor (process-group and restart owner)
       -> app.serve (WEB)
       -> app.worker (DURABLE_WORKER)
```

The lifecycle controller verifies the database, applies migrations only while no runtime is active,
computes the desired runtime-generation fingerprint, and launches the supervisor. The supervisor is
the only long-running process permitted to create or replace web and durable-worker children. The
FastAPI lifespan never creates a supervisor.

Direct `python -m app.serve` is a development/test exception. It requires
`USE_DURABLE_PIPELINE=false`, `DURABLE_WORKER_PROCESS_ENABLED=false`, and
`EMBEDDED_JOB_WORKER_ENABLED=false`; it does not represent NORMAL lifecycle operation.

`JOB_WORKER_ENABLED` is a one-release compatibility input, not an ownership switch. Canonical
lifecycle forces it false for every child. Any future topology change requires an explicit new
topology version, runtime-fingerprint change, tests, and an update to this decision.

Database trust is independently selected with an explicit safety context:

- `AUTHORITATIVE_LOCAL` enforces the configured Windows service, executable, data directory,
  listener identity/ancestry, server major, endpoint, connectivity, and database name.
- `DISPOSABLE_TEST` requires an intentionally prefixed disposable database and rejects the active
  SwingLens database. CI or pytest presence never implies this context.

The controller uses one operation ID across restart phases. It creates a new runtime instance ID for
each launched supervisor tree and propagates both identifiers, the Git SHA, and generation
fingerprint to every child.
