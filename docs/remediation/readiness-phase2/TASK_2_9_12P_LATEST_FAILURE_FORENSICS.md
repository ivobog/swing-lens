# SwingLens Task 2.9.12p Latest Failure Forensics

## Scope and clock

- Task start: `2026-10-15T17:24:51.4615365+02:00`
- Absolute deadline: `2026-10-15T19:54:51.4615365+02:00`
- Reviewed revision: `6656c55dd59c294c2ea471571863046e02bf7c35`
- Branch: `codex/ceri-authority-remediation`
- Worktree: dirty before this task and preserved; no reset, stash, discard, commit, merge, push, or deploy was performed.

## Evidence available before the 2.9.12p run

No accessible runtime export, worker log, GUI report, or certification artifact described a CERI
failure newer than Task 2.9.12o. The newest preserved incident was execution
`20261015T143136Z-f710dce5`, and its CERI stages had completed. It failed later in Setup at
`CAPTURING_SETUP_SIGNALS` when PostgreSQL reported backend 33844 idle in transaction for
`120.326891` seconds after `RELEASE SAVEPOINT sa_savepoint_91`.

Accordingly, there was no evidence basis for inventing a new CERI root cause. Task 2.9.12p used the
single authorized Chromium-100 run to obtain a newer execution and retained its native evidence.

## Latest failed execution

| Identity | Value |
|---|---|
| Execution ID | `20261015T155847Z-ea97e8f1` |
| Execution directory | `test-results/single-run-certification/20261015T155847Z-ea97e8f1` |
| Upload run ID | 3 |
| Pipeline run ID | 1 |
| Root job ID | 1 |
| Continuation / failed job ID | 32 |
| Workflow key | `pipeline:1:ceri-continuation` |
| Worker ID | `certification-20261015T155847Z-ea97e8f1` |
| Worker instance / generation | `dfb13ff9644c438fa3bf71afaa772405` |
| Root correlation | `root-b18d3fbe32ab4430a71e0dc13a212e81` |
| Continuation causation | `cause-41764589380b48c9b4cde381ec84e41d` |
| Request ID | `request-0bfebcabd7274f2ca1ae842698e4bbbd` |
| Database | `swinglens_pytest_cert_15b5cc8f2fee` |
| Provider | deterministic |
| Fixture | `single-run-certification-1.0.0` |
| Fixture hash | `84c1a6c3917dcdd8e9a6d99b74ed2f8830a6b8647a0292919698e8ba55339487` |
| CSV hash | `91300f83aab34215265dd1149f57e9e4b469362adadbd5584db3df6fd222cb22` |
| Alembic revision | `0092_phase2_eligibility_ledgers` |

The disposable database was removed by the certification fixture. The numeric market calculation
context ID and the complete persisted result JSON were not exported before deletion. They are not
reconstructed or guessed in this report. The run/context binding is instead evidenced by the exact
run/pipeline lineage, immutable environment hashes, successful completion barrier, and continuation
workflow key.

## First failed invariant and error chain

At `2026-10-15T16:15:59.144991Z`, the production runtime observer raised:

1. `POSTGRES_TRANSACTION_AGE_EXCEEDED`
2. `POSTGRES_IDLE_IN_TRANSACTION`
3. `PRODUCTION_RUNTIME_INVARIANT_FAILED`
4. Native pytest failure at
   `tests/e2e/single_run_certification/test_single_run_certification.py:998`

Observed PostgreSQL state:

| Field | Value |
|---|---|
| Backend PID | 12344 |
| State | `idle in transaction` |
| Age | `120.292053` seconds |
| Wait | `ClientRead` / `Client` |
| Last SQL | lookup of `execution_configuration_anchors` by `anchor_id` |
| Pipeline stage | `CAPTURING_SETUP_SIGNALS` |

The inferred transaction start is approximately `2026-10-15T16:13:58.852938Z`. This coincides
with the first 100-member pre-mutation decision-handoff verification query. Setup publication began
at `16:14:23.313679Z`; at and after the invariant failure, SQL telemetry still showed
`persist_setup_evidence`, `validate_setup_inputs`, `validate_source_configuration`, core evidence
publication, and savepoint activity. The last successful pipeline stage was the decision handoff.
Winner and terminal cardinality were not reached.

The execution-owned worker/job/stage is proven. SQL telemetry does not include PostgreSQL backend
PID, so it cannot conclusively distinguish whether PID 12344 remained the bounded verification
Session's connection or was reused by the parent publication Session. Exact SQLAlchemy Session-object
attribution therefore remains a blocker; this report does not claim more than the evidence supports.

## CERI job chain in the latest execution

The latest execution did not fail in CERI:

| Job | Type | Result / time (UTC) |
|---:|---|---|
| 26 | `CERI_FEATURE_BATCH` | completed `16:11:31.256735` (50 members) |
| 27 | `CERI_FEATURE_BATCH` | completed `16:11:47.408743` (50 members) |
| 28 | `CERI_RUN_FINALIZE` | completed `16:11:48.115818` |
| 29 | `CERI_CAPTURE_RUN` | completed `16:12:29.562188` |
| 30 | `CERI_CHANGE_DETECTION` | completed `16:12:44.726123` |
| 31 | `CERI_ALERT_REBUILD` | completed `16:12:45.445973` |
| 32 | `FULL_PIPELINE` continuation | claimed `16:12:45`; later failed in Setup |

The exact-membership barrier admitted the continuation. No missing capture, cross-run substitution,
stale continuation, duplicate capture, false CERI completion, or CERI calculation exception appears
in the retained evidence.

## Baseline comparison

| Dimension | Last certified baseline / 2.9.12o | Latest failed execution | Difference |
|---|---|---|---|
| Source population | Deterministic Chromium-100; upload run 3 | Deterministic Chromium-100; upload run 3; two 50-member feature batches | No population-class change |
| Calculation context | Frozen pipeline/CERI contexts; numeric IDs were execution-local | Frozen pipeline/CERI contexts; numeric ID not exported after cleanup | No evidence of semantic drift; numeric IDs are not compared across databases |
| CERI provider evidence | Certified deterministic acquisition | Provider/normalization workflow completed | No observed difference |
| Normalization | Completed before feature fan-out | Completed before jobs 26/27 | No observed difference |
| Feature evidence | Completed and certified | 100 feature checkpoints completed | No observed difference |
| Capture snapshots | Capture completed | Job 29 completed | No observed difference |
| Certified count | Exact run/context barrier reconciled in 2.9.12o | Barrier admitted job 32; numeric result was not exported | Semantically complete; numeric export limitation retained |
| Workflow/job lineage | Root plus durable CERI continuation | Root 1, CERI jobs 26-31, continuation 32 | New execution-local IDs only |
| Continuation | Restart-safe/coalesced path certified | Exact workflow `pipeline:1:ceri-continuation` claimed once | No observed CERI continuation defect |
| Source fingerprints | Deterministic fixture/provider pins | Fixture, CSV, git, migration, and provider identities retained above | Different execution artifact IDs; no unexplained financial fingerprint change |
| Execution configuration | Frozen deterministic configuration | Same feature family; exact environment exported | No CERI-authority change identified |

## Evidence and cleanup

- Native failed JUnit:
  `docs/remediation/readiness-phase2/TASK_2_9_12P_CHROMIUM_CERTIFICATION.xml`
- Runtime invariant export:
  `test-results/single-run-certification/20261015T155847Z-ea97e8f1/runtime-invariants.jsonl`
- Environment and process logs remain under the same execution directory.
- Worker SQL evidence remains in `logs/db-monitor/worker/sql-2026-10-15-p7220*.jsonl`.
- Cleanup verification: `TASK_DATABASE_EXISTS=false` and `NO_TASK_OWNED_PROCESSES`.
- Chromium-100 was not retried.

