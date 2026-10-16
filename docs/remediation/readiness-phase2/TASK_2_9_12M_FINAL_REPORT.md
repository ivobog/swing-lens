# Task 2.9.12m Final Report

## A. Executive verdict

```text
TASK 2.9.12M:
FAIL — TARGETED CERTIFICATION FAILED

TASK 2.9.12M BROWSER ADMISSION
TRANSACTION REMEDIATION: NOT CERTIFIED

TASK 2.9.12M OBSERVER
RELIABILITY: NOT CERTIFIED

CHROMIUM-100:
NOT EXECUTED IN THIS TASK

FULL PHASE 2.9 RELEASE:
UNCERTIFIED
```

## B. Historical incident

Task 2.9.12l observed `120.035264` seconds on historical PostgreSQL backend
PID `15932`, `swinglens-test`, `idle in transaction`, `ClientRead`, with a
Setup snapshot SELECT as its last statement. The process and disposable
database were already deleted, so no direct PID inspection is claimed.

## C. Execution-path comparison

The passing focused preparation node performs setup/identity, price history and
retained provider history in explicitly committed/closed Sessions. The failing
browser path additionally executes the run-detail request and the complete
`POST /runs/{run_id}/pipeline` admission flow. That flow creates a durable
transition preflight plan and immediately performs the full pre-enqueue
re-verification in the same request dependency scope.

## D. Transaction-owner proof

The retained timing correlates the 120-second historical transaction to the
web admission request. The new observer correlates two high-cardinality Setup
read phases to web backends `25424` and `21900`, at 64.497914 and 65.490093
seconds respectively.

The required SQLAlchemy trace did not activate. Therefore Python Session ID,
creation stack, exact checkout/checkin events and task identity remain missing.
The exact Session owner is not conclusively certified, even though the request
and service owner class is strongly localized.

## E. Root-cause classification

Provisional classification: API dependency/admission service transaction
composition. The historical request composed preflight creation and
re-verification into one long database unit. The focused attempt supports this
classification but cannot elevate it to a certified exact-Session finding.

## F. Implementation attempted

Changed production boundary:

- `app/routers/run_routes.py`: after obtaining the immutable RESERVED plan ID,
  the route commits its owned transaction before calling `start_pipeline`.
  `start_pipeline` still re-reads, locks and fully verifies the plan before any
  mutation. Financial formulas, PIT inputs, source authority and the 120-second
  limit were not changed.

Added test-only harness:

- per-process SQLAlchemy ownership tracing;
- an autocommit append-only PostgreSQL observer;
- a browser admission node intended to stop before enqueue;
- an observer reliability node.

Because the focused certification failed, these changes are unaccepted and
must not be treated as certified remediation.

## G. Targeted PostgreSQL certification

One pytest invocation was started with exactly:

1. `test_browser_admission_closes_preflight_creation_transaction`
2. `test_pre_pipeline_preparation_closes_transactions_between_fixture_phases`
3. `test_task_2_9_12m_observer_reliability`

The first node failed because the test stop/trace fixtures did not propagate to
the imported environment fixture. The normal endpoint admitted a pipeline and
did not return the expected 409. Execution stopped; nodes 2 and 3 did not run.
The native JUnit file is absent because pytest was interrupted before session
finalization. It was not synthesized.

The observer nevertheless recorded 1,164 samples. Maximum transaction age was
65.490093 seconds, but unauthorized pipeline admission occurred, making the
node a failure.

## H. Observer certification

Supported `.mappings()` conversion, AUTOCOMMIT configuration, process-owned
append-only JSONL, flush and fsync all operated during the browser attempt. The
dedicated reliability node did not execute, so initial/repeated sampling,
residual observer transaction state and gap detection are not certified.

## I. Static and T14

- Python compilation for modified Python files: PASS before certification.
- Ruff lint for modified Python files: PASS before certification.
- Ruff format check: FAIL/not clean; four touched files would be reformatted,
  largely because of pre-existing mixed line endings. No unrelated reformat was
  performed.
- `git diff --check` on the touched files: PASS after failure.
- T14 source pins: NOT RUN after the mandatory certification stop. No source
  pins were regenerated.

## J. Safety

- Disposable database: `swinglens_pytest_cert_a1081da9319d`, verified absent
  after teardown.
- Test-owned dynamic port: 9217, no remaining listener.
- Test-owned supervisor/web/launcher processes: absent after teardown.
- Providers: deterministic only; no live IBKR, SEC or EODHD calls.
- Historical Runs 22–33 and unrelated disposable databases: untouched.
- No reset, stash, commit, push, merge or deployment was performed.

## K. Remaining blockers

1. Browser-admission transaction defect: promising split observed, but exact
   Session trace and corrected pre-enqueue test remain uncertified.
2. Observer reliability: dedicated node not executed/certified.
3. Chromium-100 terminal certification: still required and was not authorized
   in this task.
4. Full Phase 2.9 release certification: blocked on the preceding items.

Do not run Chromium-100 until a separately authorized task fixes only the
test-fixture opt-in propagation, captures the SQLAlchemy trace, and performs a
new targeted certification under an explicit new-test authorization.
