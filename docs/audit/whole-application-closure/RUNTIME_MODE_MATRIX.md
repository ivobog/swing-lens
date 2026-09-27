# Runtime-mode matrix

This matrix traces actual callers. Settings declarations alone were not treated as proof.

| Subsystem/path | NORMAL | CERTIFICATION | Test/integration | Actual enforcement / finding |
| --- | --- | --- | --- | --- |
| Web application/lifespan | enabled; health/resource samplers | enabled by supervisor; same routes mounted | `create_app` commonly used in-process | lifespan checks process role; samplers do not write pipeline state |
| Mutating HTTP routes (52 registrations) | enabled subject to local-admin/CSRF/domain guards and explicit capability | still mounted; pre-handler authority middleware allows only explicit certification capability | TestClient exercises the same middleware; direct calls are wrapped by `unsafe_route` | generated registry: 49 NORMAL_ONLY, 1 CERTIFICATION_SESSION_SCOPED, 2 READ_ONLY, 0 unclassified (GAP-001 closed) |
| Full pipeline POST | enabled | session header must match active certification session; classified root creation then still requires consumed preflight plan/root payload | dependency overrides/fakes possible | central authority gate plus `pipeline_service.start_pipeline` transition-plan validation |
| Background enqueue | all supported job types | authorized root or descendant of active exact-session root only | usually NORMAL unless settings overridden | `certification_runtime.require_enqueue_authorized` |
| Worker claims | all selected queues | exact explicit authorization + session + live root | unit tests may call `run_worker_once` directly | `claim_next_job(certification_only=True, certification_session_id=...)` |
| Worker stale recovery | typed NORMAL authority; enabled | disabled and primitive rejects certification authority | missing/untyped authority tests | caller branch plus `RecoveryAuthority.require_normal_worker_recovery` |
| Worker abandoned recovery | typed NORMAL authority; enabled | disabled and primitive rejects certification authority | missing/untyped authority tests | caller branch plus primitive enforcement |
| Worker registration/heartbeat | enabled | enabled control-plane | in-memory/fake sessions often substitute | allowed control activity; worker instance is separate from certification session |
| SEC processor deployment identity | startup registers/updates deterministic release under typed NORMAL authority | startup performs exact-signature read-only lookup and fails closed if missing/mismatched | unit startup proof plus byte-for-byte PostgreSQL table snapshot | `establish_worker_processor_identity` (GAP-002 closed) |
| Winner H5 maturation scheduler | optional setting, evaluated each worker iteration | disabled by actual branch | callable directly | correct worker-local mode check |
| Cohort refresh / rescore / capture automation | via jobs/planners and continuations | no independent scheduler claim; descendant enqueue only if authorized | direct handler tests | enqueue gate is effective; direct service APIs remain separate |
| Durable evidence retention | periodic worker mutation | disabled by actual branch | cleanup service callable | correct worker-local mode check |
| Supervisor acquisition/heartbeat | enabled | enabled | supervisor unit tests mock sessions/processes | control-plane writes allowed |
| Supervisor watchdog | mandatory typed NORMAL authority, global worker scope | mandatory typed CERTIFICATION authority and exact-session live-root predicate | direct calls cannot omit authority | `fence_stalled_jobs` rejects missing/untyped authority |
| Supervisor dead-worker reconciliation | mandatory typed NORMAL authority over owned jobs | exact certification claim and untouched reporting | unit plus committed PostgreSQL isolation proofs | mode/session propagated from `main -> _supervise_once -> _fence_worker` into typed authority |
| Supervisor memory-critical recovery | enabled | scoped as above | unit tested | same reconciliation path |
| Supervisor shutdown recovery | enabled | scoped as above | unit tested | same reconciliation path |
| Worker child restart | enabled | enabled | mocked | restart itself is process control; new worker claims exact session only |
| Market prewarm automatic/user job | API/worker enabled | enqueue rejected unless authorized descendant; not automatically scheduled | direct tests | listed disabled workflow, but route remains mounted |
| CERI provider/normalizer/feature DAG | pipeline/API enabled | only authorized pipeline descendants; direct CERI mutations are NORMAL_ONLY | extensive unit/PG tests | child authorization inherited at enqueue; HTTP gate denies unrelated direct writers |
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
| MODE-001 | worker disables stale/abandoned recovery in certification | supervisor watchdog/death/shutdown recovery | resolved: all primitives require typed authority; fencing uses exact-session live-root scope and requeue uses exact-session recoverable-root scope |
| MODE-002 | certification disabled-workflow list excludes non-control startup writes | worker processor deployment identity | resolved: NORMAL registers; certification startup is read-only/fail-closed |
| MODE-003 | background enqueue enforces certification lineage | direct synchronous API writers | resolved: pre-handler capability middleware and per-route metadata cover all 52 registrations |
| MODE-004 | worker disables Winner scheduler | manual Winner mutating routes remain mounted | resolved for certification mutation: routes are `NORMAL_ONLY`; queued descendants still use enqueue gate |
| MODE-005 | worker disables durable retention | health cleanup API remains mounted | resolved for certification mutation: cleanup execute is `NORMAL_ONLY` and denied before dependency/DB access |
| MODE-006 | certification queue isolation checks runnable jobs | readiness queries also observe historical state/registrations | read-only but can block certification; operational cleanup must be explicit and scoped |

## Test runtime differences

Tests materially differ in four ways:

1. SQLite/fake sessions do not reproduce PostgreSQL `FOR UPDATE`, `SKIP LOCKED`, JSON predicate, advisory lock, or bind-parameter behavior.
2. Handler unit tests often invoke services directly, bypassing HTTP, enqueue, claim, process-role, and certification-session gates.
3. Supervisor tests commonly mock process liveness and `SessionLocal`; they prove branching but not competing database transactions unless marked PostgreSQL integration.
4. TestClient mounts the full web surface without the real supervisor/worker topology.

Therefore only direct PostgreSQL tests count for recovery exclusivity, certification isolation, source-bundle revalidation, and publication-generation locking.

## Mode verdict

All actual mode branches are enumerated. R1 mode safety is closed: GAP-001, GAP-002, and GAP-003 have shared typed authority, runtime fail-closed enforcement, structural drift guards, and PostgreSQL nonmutation/transition proofs. This permits R2 work; GAP-004/GAP-005 still prohibit a live canary.
