# Runtime-mode matrix

This matrix traces actual callers. Settings declarations alone were not treated as proof.

| Subsystem/path | NORMAL | CERTIFICATION | Test/integration | Actual enforcement / finding |
| --- | --- | --- | --- | --- |
| Web application/lifespan | enabled; health/resource samplers | enabled by supervisor; same routes mounted | `create_app` commonly used in-process | lifespan checks process role; samplers do not write pipeline state |
| Mutating HTTP routes (52 registrations) | enabled subject to local-admin/CSRF/domain guards | still mounted and callable | TestClient bypasses real process topology | queued jobs pass central certification enqueue gate; direct synchronous writers do not share a mode/session gate (GAP-001) |
| Full pipeline POST | enabled | only with consumed preflight plan and certification root payload/session | dependency overrides/fakes possible | `pipeline_service.start_pipeline` checks runtime mode and transition plan |
| Background enqueue | all supported job types | authorized root or descendant of active exact-session root only | usually NORMAL unless settings overridden | `certification_runtime.require_enqueue_authorized` |
| Worker claims | all selected queues | exact explicit authorization + session + live root | unit tests may call `run_worker_once` directly | `claim_next_job(certification_only=True, certification_session_id=...)` |
| Worker stale recovery | enabled | disabled | callable directly in unit tests | actual caller branch in `run_worker_once` |
| Worker abandoned recovery | enabled | disabled | callable directly | actual caller branch in `run_worker_once` |
| Worker registration/heartbeat | enabled | enabled control-plane | in-memory/fake sessions often substitute | allowed control activity; worker instance is separate from certification session |
| SEC processor deployment registration | every worker startup writes/updates release | also executes before loop and commits | often patched/faked | not listed as allowed certification control activity and not session-scoped (GAP-002) |
| Winner H5 maturation scheduler | optional setting, evaluated each worker iteration | disabled by actual branch | callable directly | correct worker-local mode check |
| Cohort refresh / rescore / capture automation | via jobs/planners and continuations | no independent scheduler claim; descendant enqueue only if authorized | direct handler tests | enqueue gate is effective; direct service APIs remain separate |
| Durable evidence retention | periodic worker mutation | disabled by actual branch | cleanup service callable | correct worker-local mode check |
| Supervisor acquisition/heartbeat | enabled | enabled | supervisor unit tests mock sessions/processes | control-plane writes allowed |
| Supervisor watchdog | global worker scope | current tree supplies certification session predicate | direct unit calls may omit session | previously split-brain; fixed only in uncommitted tree; primitive still accepts optional authority |
| Supervisor dead-worker reconciliation | global owned jobs | current tree scopes to exact certification claim and reports untouched rows | tests cover mocked and PostgreSQL paths in untracked additions | mode propagated from `main -> _supervise_once -> _fence_worker` |
| Supervisor memory-critical recovery | enabled | scoped as above | unit tested | same reconciliation path |
| Supervisor shutdown recovery | enabled | scoped as above | unit tested | same reconciliation path |
| Worker child restart | enabled | enabled | mocked | restart itself is process control; new worker claims exact session only |
| Market prewarm automatic/user job | API/worker enabled | enqueue rejected unless authorized descendant; not automatically scheduled | direct tests | listed disabled workflow, but route remains mounted |
| CERI provider/normalizer/feature DAG | pipeline/API enabled | only authorized pipeline descendants; direct synchronous CERI review remains callable | extensive unit/PG tests | child authorization inherited at enqueue |
| CERI unrelated backlog | claimable | invisible to certification claim | test can call handlers directly | worker isolation is correct |
| SEC readiness repair | pipeline conditional branch | allowed only as authorized descendant | integration tests | carries pipeline/root/session causality |
| Winner maturation continuations | scheduler/API/handler | only authorized descendants; primary scheduler disabled | handler tests | continuation enqueue gate |
| Readiness/preflight | reads global operational state; may block | certification-specific queue isolation and plan checks | often dependency-injected | reads can be affected by historical residue even when mutation is scoped |
| Certification cleanup | explicit scripts/services only | explicit operator operation | disposable fixtures | no automatic broad cleanup in runtime |
| Embedded worker | settings-supported legacy topology, normally disabled | configuration reports disabled/unsupported for isolated run | may be enabled in tests | must not be assumed equivalent to durable worker; verify deployment settings |
| Legacy/direct scripts | explicit operator | process settings/database safety vary | many require disposable local DB | mode enforcement is script-specific; 49 mutation-indicator entrypoints inspected |
| Migrations/bootstrap | external operator | external operator | ephemeral DB setup | outside runtime-mode policy |

## Split-brain policy inventory

| ID | Policy stated in one component | Independent component | Current result |
| --- | --- | --- | --- |
| MODE-001 | worker disables stale/abandoned recovery in certification | supervisor watchdog/death/shutdown recovery | current uncommitted tree threads session scope; HEAD did not; shared primitives remain optionally scoped |
| MODE-002 | certification disabled-workflow list excludes non-control startup writes | worker always calls `register_deployed_processor` | unresolved GAP-002 |
| MODE-003 | background enqueue enforces certification lineage | direct synchronous API writers bypass enqueue | unresolved GAP-001 |
| MODE-004 | worker disables Winner scheduler | manual Winner mutating routes remain mounted | queued operations are rejected by enqueue gate; direct model retirement/publication-like services require individual review |
| MODE-005 | worker disables durable retention | health cleanup API remains mounted | explicit local-admin action can still mutate during certification |
| MODE-006 | certification queue isolation checks runnable jobs | readiness queries also observe historical state/registrations | read-only but can block certification; operational cleanup must be explicit and scoped |

## Test runtime differences

Tests materially differ in four ways:

1. SQLite/fake sessions do not reproduce PostgreSQL `FOR UPDATE`, `SKIP LOCKED`, JSON predicate, advisory lock, or bind-parameter behavior.
2. Handler unit tests often invoke services directly, bypassing HTTP, enqueue, claim, process-role, and certification-session gates.
3. Supervisor tests commonly mock process liveness and `SessionLocal`; they prove branching but not competing database transactions unless marked PostgreSQL integration.
4. TestClient mounts the full web surface without the real supervisor/worker topology.

Therefore only direct PostgreSQL tests count for recovery exclusivity, certification isolation, source-bundle revalidation, and publication-generation locking.

## Mode verdict

All actual mode branches are enumerated, but NORMAL/CERTIFICATION safety is not complete. GAP-001 and GAP-002 leave certification-capable direct writers outside the session capability, and GAP-003 leaves lower-level recovery authority optional.
