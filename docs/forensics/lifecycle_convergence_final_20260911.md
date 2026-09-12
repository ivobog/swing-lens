# SwingLens lifecycle convergence final forensic report — 2026-09-11

## 1. Executive verdict

**NOT CERTIFIED.** The stopped-generation convergence defect described by the original report was
remediated in `7d3a950d449581fea73fe1486449a0a52742ab24`, and exact-SHA canonical CI run
`34639244871` is fully green. The second bounded real-machine attempt proved read-only stale-state
classification, automatic archival/retirement, clean startup, idempotent startup, core readiness,
canonical topology, and observability. Its required `restart` was then externally interrupted after
the stop phase and has no terminal journal event. Per the no-retry rule, diagnose and safe stop were
performed and the certification sequence was not resumed. The machine is now canonically stopped,
with zero active jobs and PostgreSQL still running. The original failed attempt below remains intact
and traceable.

## 2. Original baseline

The preserved baseline is
[`lifecycle_convergence_baseline_20260911.md`](lifecycle_convergence_baseline_20260911.md). Its
starting point was branch `codex/final-certification-runtime-topology-remediation`, SHA
`e61bdbd76551a78d5c83cf077f7093f94c66e86b`, with no live SwingLens runtime, authoritative local
PostgreSQL 18.3 at schema head `0072_ceri_artifact_context_lineage`, no active business jobs, and no
running observability stack. Baseline CI run `34601813963` was red because disposable CI databases
were being subjected to authoritative-machine provenance rules.

Before the interrupted final certification at `095a552`, remediation had already established
authoritative PostgreSQL provenance, a clean preflight, first and idempotent starts, healthy
supervisor/web/worker topology, and available Prometheus/Grafana with only a low-disk warning.

## 3. Root-cause tree

```text
Lifecycle non-convergence
├── Ambiguous runtime ownership (remediated)
│   ├── web-owned supervisor path
│   ├── SupervisorProcessManager ownership path
│   └── process-group leader confused with inner Python supervisor
├── Unsafe environment/database inference (remediated)
│   ├── CI/pytest presence implicitly selected trust semantics
│   └── disposable databases were checked as authoritative local databases
├── Over-broad startup readiness gate (remediated)
│   └── full application /ready made optional integrations block core startup
├── Incomplete operation correlation/diagnostics (remediated)
│   └── restart phases and children lacked one durable operation/generation identity
└── Stopped-generation convergence defect (REMEDIATED in 7d3a950)
    ├── interrupted restart stopped the old tree but left its version-5 state record
    ├── checkout advanced from 095a552 to tree-identical merge SHA 0fb435e
    ├── desired fingerprint changed solely because the Git SHA is fingerprinted
    ├── no recorded process is alive and lifecycle ports are free
    └── start, stop, and status reject/classify the stale record before retiring it
```

## 4. Final canonical supervisor-root topology

The accepted NORMAL topology is `supervisor-root-v1`:

```text
swinglens.ps1 / lifecycle controller
  -> app.worker_supervisor (process-group and restart owner)
       -> app.serve (WEB)
       -> app.worker (DURABLE_WORKER)
```

The FastAPI lifespan does not launch a supervisor, `SupervisorProcessManager` is removed, and the
legacy worker flag is not an ownership switch. The final failure occurred before this tree launched;
it did not resurrect any forbidden ownership path.

## 5. Database safety-context result

The runtime uses explicit `AUTHORITATIVE_LOCAL` safety semantics. Final probes verified:

- endpoint `postgresql+psycopg://127.0.0.1:5432/swinglens` (credentials omitted);
- PostgreSQL `18.3`;
- Windows service `postgresql-x64-18`;
- executable `C:\Program Files\PostgreSQL\18\bin\postgres.exe` for listener PID `6976`;
- data directory `C:/Program Files/PostgreSQL/18/data`;
- current and expected Alembic head `0072_ceri_artifact_context_lineage`;
- `USE_DURABLE_PIPELINE=true`;
- provenance verified and schema at head.

Disposable CI uses explicit `DISPOSABLE_TEST` with an allowed disposable database prefix and rejects
the active SwingLens database. No CI/pytest ambient-variable inference remains.

## 6. Readiness split result

The implemented contract is:

- `/health`: web-process liveness;
- `/ready/core`: authoritative database/schema/storage plus canonical supervisor/web/worker identity,
  registration freshness, ancestry, and mandatory listeners;
- `/ready`: comprehensive application-operational health, including optional providers and queues.

At the recovered stopped checkpoint and at final physical shutdown, all three HTTP endpoints were
unreachable as expected because port 8000 had no listener. Prior to interruption, the running
`095a552` runtime had passed `/health` and `/ready/core`; `/ready` was degraded only by the existing
low-disk warning. The reconciled runtime never launched, so no post-reconciliation HTTP readiness
claim is made.

## 7. Runtime-generation result

The generation covers Git SHA, checkout, Python executable, runtime mode, topology version,
authoritative database identity, Alembic heads, critical configuration, ports, and worker modes.

- stale recorded SHA: `095a552e21f92523a7d8e67df2109742a0cad0a9`;
- stale fingerprint: `e54e16bd93327d546a0584d6600711bef6bc397b8ce400ac0be66e1fb4795ffa`;
- desired reconciled SHA: `0fb435ee6eb76049ddf6d76d859775fec35768f5`;
- desired fingerprint: `156a71dbbf347e9b8725282a589c380c81757f3f73e55b680b9f3a1afa5fafe3`;
- stale runtime instance: `44718fd5e4414bf4b3e9488a4b9e7c9b`;
- topology version in both records: `supervisor-root-v1`.

Active-generation mismatch is correctly fail-closed. The unresolved problem is that the same guard
also blocks convergence when every recorded process is dead and every lifecycle port is released.

## 8. Crash-loop behavior

The supervisor implements bounded, per-child restart windows and reason/stage reporting. Windows
durable worker-kill recovery passed in exact-SHA CI run `34634676999`; the preceding exact-SHA main
run `34633478836` also passed it. The last real runtime's supervisor state showed zero web/worker
restart failures and `cycle_failed=false` before shutdown. No real-machine crash-loop exercise was
performed after reconciliation because the first canonical start failed before launch.

## 9. Lifecycle operation correlation / flight recorder

The lifecycle journal is `logs/lifecycle/lifecycle.jsonl`. The interrupted and final operations were:

| Operation ID | Action | Result | Evidence |
|---|---|---|---|
| `02550f9b-a6ba-4ee9-8d77-026ebfad3e86` | interrupted `restart` | no terminal event | begin at 17:34:49Z; old web/worker shutdown events followed; no replacement boot |
| `69a9bad8-b70e-4af7-9df9-903c27830157` | final `start` | failure | `RESTART_REQUIRED`, generation fingerprint differs |
| `74e0a150-a8e5-4679-b28c-c28bd5df985b` | post-failure `diagnose` | success | sanitized bundle captured |
| `7d98c1dc-0328-4b79-896f-8337e11e75e1` | safety `stop` | failure | same `RESTART_REQUIRED` conflict |
| `73aea43a-8611-4094-a90d-aee70d2c21ee` | final `status` | failure classification | `CONFLICT`, runtime null, no active jobs |

The original successful start operation `0167a896-2072-4c53-a470-55e4e8011da1` propagated one
operation ID, runtime instance ID, Git SHA, and fingerprint through supervisor, web, and worker logs.

## 10. Prometheus/Grafana lifecycle observability

Prometheus (`127.0.0.1:9090`) and Grafana (`127.0.0.1:3000`) remained reachable. Grafana health was
HTTP 200 with database `ok`; Prometheus `/-/ready` returned ready. At physical runtime shutdown the
three targets `swinglens-web`, `swinglens-worker`, and `swinglens-supervisor` were all `down` with
connection-refused scrape errors, which matches the stopped-runtime observability contract. No
claim of 3/3 targets up is made for the reconciled SHA because the runtime did not start.

## 11. Diagnose command result

Recovery diagnose succeeded at bundle
`artifacts/diagnostics/lifecycle-20260911T183547Z-1320cc31-4e7b-4046-b249-aa4de978d55f`.
Post-failure diagnose succeeded at bundle
`artifacts/diagnostics/lifecycle-20260911T184458Z-74e0a150-a8e5-4679-b28c-c28bd5df985b`.
The latter classified the most likely boundary as `CORE_RUNTIME_STOPPED_OR_UNREACHABLE`, captured
zero active jobs, verified PostgreSQL/schema state, and preserved desired/stale generation evidence.
Both bundles were read-only with respect to business state.

## 12. CI failures encountered during remediation and fixes

- Run `34601813963` at `e61bdbd`: PostgreSQL 16 Linux and temporary PostgreSQL 18 Windows databases
  were incorrectly treated as authoritative local. Fixed by explicit safety contexts and disposable
  database naming/rejection rules.
- Run `34621645170` at `93ef856`: Chromium/Firefox smoke and populated restore failed; the restore
  used an invalid `pg_dump` URL form. Fixed by the PostgreSQL 18/browser certification harness changes
  in `329f3d7`.
- Run `34624686236` at `329f3d7`: 13 full-suite failures exposed POSIX listener evidence assumptions,
  dotenv provenance fixture assumptions, and one winner reliability fixture. Fixed narrowly in
  `095a552`.
- Run `34626305077` at `095a552`: all canonical CI jobs green.
- Reconciled exact-SHA runs `34633478836` (main) and `34634676999` (remediation branch) at `0fb435e`:
  all required non-nightly jobs green.

## 13. Previous interrupted restart classification

**`RESTART_PARTIALLY_COMPLETED`.** Operation `02550f9b-a6ba-4ee9-8d77-026ebfad3e86` has an
`operation_begin` but no `operation_complete`. The old runtime's worker and web emitted shutdown
events, registrations have stopping timestamps, all recorded PIDs are absent, and ports
8000/9101/9102 are released. There is no replacement supervisor/web/worker boot after the restart
begin. Thus the stop phase completed physically, the start phase did not occur, and the operation
did not close its journal/state transaction.

## 14. Branch/main reconciliation details

After `git fetch origin`:

- `git rev-list --left-right --count origin/main...HEAD` returned `4 0`;
- merge base was `095a552e21f92523a7d8e67df2109742a0cad0a9`;
- main-only commits were merge commits `91806dc`, `48503ad`, `0ed4664`, and `0fb435e`;
- `git diff HEAD origin/main` was empty;
- both trees were `72685182542448b53b660f979cc195f3ec36c93f`.

There was therefore no semantic overlap to resolve and no risk of resurrecting web-owned supervisor,
`SupervisorProcessManager`, implicit disposable-database authority, or `/ready` as the startup gate.
With the runtime physically stopped, `git merge --ff-only origin/main` safely advanced the branch to
`0fb435e`, and that fast-forward was pushed.

## 15. Exact final branch and SHA

- Branch: `codex/final-certification-runtime-topology-remediation`
- Reconciled/runtime-tested SHA: `0fb435ee6eb76049ddf6d76d859775fec35768f5`
- Reconciled tree: `72685182542448b53b660f979cc195f3ec36c93f`

The eventual documentation-only closure commit containing this report does not alter the tested
runtime tree; the SHA above is the exact executable tree used for the final attempt.

## 16. Exact GitHub CI run and result

- Required branch push run: `34634676999`
- SHA: `0fb435ee6eb76049ddf6d76d859775fec35768f5`
- Result: **SUCCESS**
- Green jobs: `Lint, Test, and Migration Gates`, `Chromium and Firefox Smoke`, and
  `Windows Durable Worker Recovery Gate`.
- Green required steps include lint, tracked-secret scan, route inventory, Alembic head graph,
  upgrade/current/metadata drift, clean migration harness, populated backup/restore, full unit and
  service suite, golden scoring, Chromium+Firefox smoke, and repeated Windows worker-kill recovery.
- `Nightly Performance Baseline` was event-conditionally skipped and is not a canonical push gate.

The same SHA also completed green on main in run `34633478836`.

## 17. Final real-machine sequence

Preconditions passed: exact reconciled SHA, clean tracked/untracked status after locally excluding
preserved diagnostic bundles, zero active jobs, verified authoritative PostgreSQL, schema at head,
and no live runtime.

| Command | Result |
|---|---|
| `swinglens.ps1 start` | **FAIL**, exit 1, `RESTART_REQUIRED`, operation `69a9bad8-b70e-4af7-9df9-903c27830157` |
| second `start` | not run; bounded sequence stopped on first failure |
| running `status` | not run; runtime never launched |
| `restart` | not run; no live retry loop permitted |
| post-restart `status` | not run |
| required failure `diagnose` | **PASS**, operation `74e0a150-a8e5-4679-b28c-c28bd5df985b` |
| required safety `stop` | **FAIL**, exit 1, same `RESTART_REQUIRED`, operation `7d98c1dc-0328-4b79-896f-8337e11e75e1` |
| final read-only `status -Json` | `CONFLICT`, runtime null, zero active jobs |

No second live certification sequence was attempted and no live patch/retry loop was entered.

## 18. Final process/listener state

- WEB: absent;
- SUPERVISOR: absent;
- DURABLE_WORKER: absent;
- port 8000: released;
- port 9101: released;
- port 9102: released;
- PostgreSQL: still running, listener PID `6976`, port 5432;
- Prometheus/Grafana: still running through Docker Desktop, ports 9090/3000;
- runtime state: stale version-5 record from `095a552` remains;
- controller state: `CONFLICT`, not canonically stopped.

## 19. Remaining warnings

- Local disk free space is 13.8%, below the configured 15% warning threshold but above the 5%
  critical threshold. This is the pre-existing non-blocking low-disk degradation.
- Prometheus's three SwingLens targets are down because the runtime is stopped; Prometheus and Grafana
  themselves are healthy.
- The stale runtime state makes `status`, `start`, and `stop` conflict after the checkout-generation
  change.

## 20. Remaining risks

Stopped-generation convergence is remediated and covered by deterministic Python and PowerShell
regressions. The remaining certification gap is procedural evidence: the second bounded machine
sequence was interrupted during its required restart and, under the explicit no-retry rule, could
not be resumed. The branch therefore remains not lifecycle-certified even though the interrupted
generation subsequently converged safely through `stop` and the machine reached canonical
`STOPPED`. A future explicitly authorized bounded certification would be required to establish a
terminal successful restart and post-restart readiness sequence.

## 21. Exact tests executed

Local focused command:

```text
.venv\Scripts\python.exe -m pytest -q
  tests/ops/test_lifecycle_config.py
  tests/ops/test_lifecycle_contracts.py
  tests/ops/test_lifecycle_convergence.py
  tests/ops/test_lifecycle_observability.py
  tests/ops/test_lifecycle_powershell.py
  tests/ops/test_lifecycle_safety.py
```

Result: **95 passed, 1 dependency deprecation warning, 104.36 seconds**.

Canonical CI run `34634676999` executed the Linux full suite and golden gate, migrations and drift
checks, populated restore, browser certification in Chromium and Firefox, Windows durable worker
recovery, lint, route inventory, and tracked-secret scan. No already-green full suite was repeated
locally.

## 22. Final `git status --short`

Clean after committing this report. Recovery/failure diagnostic bundles remain preserved locally and
are excluded only through `.git/info/exclude`; they were not deleted or added to production evidence.

## 23. Original attempt business-data mutation confirmation

No business pipeline, provider processing, Winner/CERI operation, broker work, enqueue, historical
repair, canary, or evidence mutation was performed during recovery, reconciliation, CI observation,
or final certification. The reconciled runtime never started. Read-only status/diagnose probes and
lifecycle journal/metrics writes were the only operational observations; PostgreSQL business data
remained untouched.

## 24. Final stopped-generation convergence remediation — 2026-09-12

### Defect and exact code cause

The original failure was reproduced as a persisted version-5 runtime state from Git SHA `095a552`
whose WEB, SUPERVISOR, process-group, and worker processes were dead while ports 8000, 9101, and
9102 were free. `scripts/ops/lifecycle_probe.py::_runtime_state_report()` compared the persisted
runtime fingerprint with the desired fingerprint before inspecting the recorded WEB PID. Because
Git SHA remains intentionally included in the fingerprint, the tree-identical merge SHA change made
the dead record return `RESTART_REQUIRED` before it could reach the existing stale-state path.

Remediation commit `7d3a950d449581fea73fe1486449a0a52742ab24` implements the explicit state
machine `ACTIVE_VALID`, `ACTIVE_GENERATION_MISMATCH`, `DEAD_STALE`, `AMBIGUOUS_CONFLICT`, and
`MISSING`. Process identity, PID creation time, repository, runtime instance, ancestry/topology, and
listener evidence are evaluated before generation mismatch semantics. A generation mismatch still
returns `RESTART_REQUIRED` when the runtime is strongly verified alive. A record becomes
`DEAD_STALE` only after every recorded identity available from the canonical and supervisor state
is absent, no canonical WEB/SUPERVISOR/DURABLE_WORKER process exists under this checkout, and ports
8000/9101/9102 are unoccupied. PID reuse, partial survival, unknown role processes, listener
occupancy, corrupt state, and incomplete evidence remain fail-closed as `AMBIGUOUS_CONFLICT`.

### Status and controlled retirement policy

`status` is read-only with respect to runtime state. It now reports a safely dead record as overall
`STOPPED`, `runtimeActive=false`, `staleStatePresent=true`, and `conflict=false`, together with safe
recorded/desired Git SHA and fingerprint values, stale runtime instance ID, and stale reason.

Only `start`, `stop`, and `restart`, while already inside the lifecycle repository mutex/file lock,
invoke stale-state retirement. The record is revalidated against processes and listeners, moved to
`data/cache/lifecycle-archive`, and journaled as `stale_runtime_state_retired` with reason
`DEAD_GENERATION_RETIRED`. The event contains only the old runtime instance, old/desired Git SHA and
fingerprint, topology version, and retirement operation ID. Active and ambiguous states are never
retired by this path.

### Regression evidence

The focused local gate passed **115 tests** in 116.97 seconds, covering lifecycle config, contracts,
convergence, observability, PowerShell controller behavior, process safety, and the new
stopped-generation fixture. The permanent regressions include:

- dead recorded WEB with same or different fingerprint -> `DEAD_STALE`;
- strongly verified active old generation -> `RESTART_REQUIRED`;
- reused recorded PID, surviving supervisor/worker, unknown canonical role, and occupancy of each
  of 8000/9101/9102 -> `AMBIGUOUS_CONFLICT`;
- dead stale `status` -> `STOPPED` without state mutation;
- dead stale `start`/`stop`/`restart` -> controlled retirement and clean/idempotent behavior;
- Git-SHA-only generation change -> different fingerprint, with dead convergence and active
  protection both preserved;
- interrupted-restart persisted-state fixture -> next start retires the record and launches cleanly.

Repository-wide Ruff and `git diff --check` also passed before the remediation commit.

### Exact-SHA canonical CI

- Runtime remediation SHA: `7d3a950d449581fea73fe1486449a0a52742ab24`.
- GitHub Actions run: `34639244871`.
- Result: **SUCCESS**.
- `Lint, Test, and Migration Gates`: passed in 14m52s, including secret scan, route inventory,
  Alembic graph/current/drift, clean migration, populated restore, full unit/service coverage, and
  golden scoring.
- `Chromium and Firefox Smoke`: passed in 3m12s.
- `Windows Durable Worker Recovery Gate`: passed in 4m48s.
- The scheduled-only nightly performance job was correctly skipped for the push event.

### Second bounded real-machine attempt

The original failed attempt in sections 9, 17, and 18 is preserved. After exact-SHA CI became green,
one new bounded attempt began at `7d3a950`:

| Command / operation | Result |
|---|---|
| pre-retirement `status`, operation `3cc0b5d5-c50b-4425-baf7-2f48638ece03` | **PASS** — `OVERALL STOPPED`; stale state explicitly reported; no conflict or mutation |
| first `start`, operation `2945a2a4-5637-4608-acb8-386185dd7b5a` | **PASS** — archived old instance `44718fd5...`; `DEAD_GENERATION_RETIRED`; launched new instance `0836bba8...` |
| second `start`, operation `b04094eb-1746-479f-b360-f40bc96e718e` | **PASS** — reused strongly verified WEB PID `13880` |
| running `status`, operation `fd51e5eb-4cb2-4c1d-8334-47b851cf5377` | **PASS** — core OK, WEB/worker ready, Prometheus/Grafana ready, only the pre-existing 13.8% disk warning |
| `restart`, operation `3076297e-6a12-4756-8a1e-dc40c04f9a25` | **INTERRUPTED** — operation begin exists; supervisor and worker shutdown followed; no operation-complete event and no replacement start |
| failure `diagnose`, operation `6e315e19-109a-4bc3-a95b-8b1cf1f5ab2e` | **PASS** — sanitized bundle `artifacts/diagnostics/lifecycle-20260912T002853Z-6e315e19-109a-4bc3-a95b-8b1cf1f5ab2e` |
| required safe `stop`, operation `0a14ad4e-a8dc-414a-87f5-7cd234d867ba` | **PASS** — recognized interrupted instance `0836bba8...` as dead stale, archived/journaled it, and returned `OVERALL STOPPED` |
| final JSON `status`, operation `3fcd02ff-0f35-427b-a11f-247f55f8066d` | **PASS** — `STOPPED`, `MISSING`, runtime inactive, no stale canonical state, zero active jobs |

The successful running checkpoints proved canonical supervisor-root readiness and all three
Prometheus SwingLens targets up through the controller's core/target gates. No direct final claim is
made for the unexecuted post-restart status or `/health` check because the restart did not complete.
No retry or live patch loop was performed.

### Final machine state and verdict

- WEB, SUPERVISOR, and DURABLE_WORKER processes: absent;
- ports 8000, 9101, and 9102: free;
- canonical runtime-state file: absent (`MISSING`);
- archived stale evidence: preserved for both the original old generation and interrupted
  remediation generation;
- active `RUNNING`/`RECOVERING` jobs: zero;
- PostgreSQL 18.3: running on PID `6976`, schema head `0072_ceri_artifact_context_lineage`;
- Prometheus/Grafana: stopped by the safe-stop path;
- business pipelines/evidence: untouched.

Final verdict remains **NOT CERTIFIED** because the required restart and subsequent checks did not
complete. The stopped-generation convergence defect itself is remediated, exact-SHA CI is green,
and failure containment converged to canonical stopped state without manual runtime-state deletion.
