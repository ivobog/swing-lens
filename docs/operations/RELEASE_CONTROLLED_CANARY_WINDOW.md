# Release controlled idle/canary window

This is a *later* operational procedure. The remediation task certifies the
control mechanism but does not start the authoritative runtime, IB Gateway, a
pipeline, or a canary. The authoritative database must be migrated and backed
up only under a separately authorized gate before `start` is used.

## Process-local control

From the repository root in PowerShell 7.4 or newer:

```powershell
pwsh -NoProfile -File .\scripts\ops\release_controlled_window.ps1 verify
```

The command reads effective settings and must print `"status": "PASS"`.
It does not open a runtime. Once the separately authorized operational gate
has passed database/IB preflight, use these exact commands in order:

```powershell
pwsh -NoProfile -File .\scripts\ops\release_controlled_window.ps1 start
pwsh -NoProfile -File .\scripts\ops\release_controlled_window.ps1 status
# Observe idle stability, then run only the explicitly authorized pipeline/canary.
pwsh -NoProfile -File .\scripts\ops\release_controlled_window.ps1 stop
pwsh -NoProfile -File .\swinglens.ps1 status -RuntimeMode NORMAL -Json
```

The last normal `status` is for verifying the controlled runtime stopped; it
does not start another runtime. Entering or leaving the window never edits
`.env`. Each wrapper invocation sets the three scheduler overrides in its own
process, passes them to `swinglens.ps1` and its supervised children, and exits.
The `stop` command uses the same overrides so the lifecycle configuration
fingerprint matches the controlled runtime. On a later normal `start`, the
stored values become effective again automatically. Do not issue that start
until the next gate explicitly authorizes it.

| Setting | Stored normal value on this host | Controlled effective value | Effect on explicit pipeline | Restore |
| --- | --- | --- | --- | --- |
| `WINNER_PROBABILITY_AUTO_MATURATION_ENABLED` | `true` | `false` | No autonomous maturation; explicit pipeline capture unchanged | New normal invocation reads stored `true` |
| `WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED` | `true` | `false` | No autonomous cohort refresh; explicit pipeline unchanged | New normal invocation reads stored `true` |
| `MARKET_DATA_PREWARM_ENABLED` | `true` | `false` | No prewarm scheduling; explicit `REQUIRE_IB` fetch/preflight unchanged | New normal invocation reads stored `true` |
| `DURABLE_WORKER_PROCESS_ENABLED` | `true` | `true` | Required durable worker/fencing retained | Unchanged |
| `EMBEDDED_JOB_WORKER_ENABLED` | `false` | `false` | No embedded worker; standalone worker retained | Unchanged |
| `WINNER_PROBABILITY_ENABLED` | `true` | `true` | Winner calculation remains enabled | Unchanged |
| `WINNER_PROBABILITY_CAPTURE_IN_PIPELINE` | `true` | `true` | Winner capture within the explicitly requested pipeline remains enabled | Unchanged |
| `RUNTIME_MODE` | `NORMAL` | `NORMAL` | Normal certified pipeline semantics; no certification-only queue/plan bypass or altered claim scope | Unchanged |

The wrapper does **not** override calculation identity, scope/refresh,
readiness, effective-configuration authority, mutation authority, worker
fencing, CERI, the explicit pipeline, or Winner pipeline capture. Its
`verify` command checks the critical effective flags without printing secrets.
`SWINGLENS_LIFECYCLE_OVERRIDE_KEYS` records the process-local scheduler and
worker topology overrides in lifecycle provenance.

IB Gateway on port 4002 is a separate readiness/pre-enqueue dependency. If it
is down, `REQUIRE_IB` preflight must reject the later pipeline; this policy
does not replace it with cache fallback or start IB. The ordinary observability
IB check can report `optional_unavailable` when monitoring policy marks IB
optional, but `observability_ib_required=true` reports `failed` and explicit
`REQUIRE_IB` preflight remains fail-closed.
