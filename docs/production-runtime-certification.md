# Production Runtime Certification

SwingLens must be certified as a durable asynchronous system before a normal run is
promoted. Focused tests alone are not a promotion signal.

Run the deterministic gate with:

```powershell
pwsh .\swinglens.ps1 certify-runtime
```

The command executes separate focused, PostgreSQL transaction, watchdog/recovery,
certification-isolation, and production-runtime test groups. The production-runtime
group starts `app.worker_supervisor`, which launches the real `app.serve` and
`app.worker` entrypoints. A run is admitted through the browser/public web entrypoint
and is processed through the durable queue, leases, detached control transactions,
async CERI child graph, dependency barriers, and continuations.

External input is deterministic in the standard gate. The adapter is allowlisted and
may be installed only in `CERTIFICATION` mode; it replaces provider I/O, not internal
orchestration. Every invocation creates and migrates a disposable PostgreSQL database,
uses session-scoped certification authority, disables unrelated automatic workflows,
and drops the database during cleanup. Certification artifacts are therefore neither
normal-run authority nor dependencies of later certification runs.

While the graph runs, the observer samples runtime and PostgreSQL state every 500 ms.
It fails on duplicate active ownership, expired leases, frozen useful progress, stale
control-loop health despite fresh process/resource health, orphan or duplicate child
and continuation jobs, cross-run lineage, repeated recovery, premature parent
completion, lock waits, old transactions, and idle-in-transaction sessions.

Reports are written below `test-results/production-runtime-certification/<execution>`:

- `report.json` is the mechanical promotion result;
- `REPORT.md` is the human-readable summary;
- one stdout/stderr log is retained per gate;
- `runtime-evidence/<execution>` contains the runtime report, invariant samples,
  stage results, child/continuation evidence, SQL extracts, and lifecycle logs.

The deterministic command can pass while `SAFE_FOR_NORMAL_RUN` remains `NO`. That is
intentional: `SMALL_LIVE_PROVIDER_CANARY` is a separate mandatory promotion gate and
is never launched implicitly. After a passing deterministic report exists for the
exact current working-tree fingerprint, run:

```powershell
pwsh .\swinglens.ps1 certify-runtime -LiveProviderCanary
```

Live mode validates the configured SEC fair-access identity, EODHD credential, and
the exact configured IB endpoint (API handshake plus historical-data capability)
before it creates any runtime. A failed preflight writes a durable `BLOCKED` report
and performs no provider call. A passing preflight runs a five-symbol cohort in the
same disposable PostgreSQL/supervisor/web/durable-worker fixture as deterministic
certification. Only the provider adapter and deterministic IB pacing overrides are
removed; queueing, leases, checkpoints, continuations, Setup, Winner, publication
barriers, observer, cleanup, and certification authority are unchanged. A normal run
is safe only when every gate in the live report is `PASS`.
