**Run 41 forensic analysis — 18 October 2026**

**Conclusion: a recapture/ownership contract regression caused the failure.** The pipeline deliberately omitted its completed setup-capture handoff because its frozen configuration had `setup_capture_handoff_enabled=false`. The evaluator's new outer wrapper then recaptured the same inputs. The repository correctly reused the existing immutable snapshots, preserving their original capture owner, **58**. The recapture result identified a **new capture attempt**. The inner evaluator incorrectly required that new attempt to be the creator of every reused snapshot and raised:

```text
ValueError: Setup capture handoff snapshot ownership is inconsistent.
```

This is established by the persisted configuration and snapshot ownership, the production SQL sequence, the corresponding code, and an independently executed native PostgreSQL reproduction with a successful positive control. The immediate failure occurred before canonicalization and change detection. It has a different cause from run 40's heartbeat lock timeout.

Production inspection enforced PostgreSQL read-only transactions. No production retry, repair, settings change, restart, application edit, commit, or push was performed. Reproduction used a separate migrated disposable database and dropped it afterward. The previously existing working-tree changes were preserved.

**Identity and scope**

| Item | Verified value |
|---|---|
| Upload / pipeline | 41 / 41; 88 input companies |
| Original pipeline job | 1385; completed after scheduling the CERI dependency |
| CERI dependency | 56; `COMPLETED`, certified count 88 |
| Resumed pipeline job | **1416**; root job 1385, parent job 1415 |
| Completed capture | **58**; 88 snapshots, IDs **3498–3585** |
| Failed evaluation audit | **61**; exact ownership error |
| Failed stage | Step **532**, `EVALUATING_SETUP_LIFECYCLES` |
| Frozen market context | 74; cutoff `2026-10-18T10:51:39.665164Z`; session `2026-10-16` |
| Worker OS process | 69808; instance `c106fa041deb4b5e870597d19ea6569c` |
| Recorded runtime Git SHA | `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`; **dirty working tree** |
| Runtime schema | `0092_phase2_eligibility_ledgers` |

The runtime Git SHA does not identify all loaded code because the runtime was dirty. The reviewed source hashes and exact current diff are preserved in [reviewed-source-sha256.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/reviewed-source-sha256.json) and [reviewed-working-tree.diff](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/reviewed-working-tree.diff). Production SQL caller names and line numbers agree with the reviewed paths. The historical process's complete loaded source bytes were not archived; attribution to a particular uncommitted edit is therefore supported by behavior and the source comparison, rather than an immutable deployment artifact.

**Reconstructed timeline**

Times in this table are **Europe/Zurich, CEST, UTC+02:00**. Application and SQL evidence files use UTC.

| Time | Event and evidence |
|---|---|
| 12:51:42.324 | Pipeline 41 starts. |
| 12:58:14–13:26:16 | CERI dependency completes successfully; 88 certified subjects. |
| 13:26:16.994 | Worker claims continuation job 1416; application log line 688608. |
| 13:26:17–13:28:56 | Frozen decision handoff stage completes; application lines 688610–688611. |
| 13:31:55.471 | Setup capture stage starts. |
| 13:35:05.049 | Native capture-evaluation insert; SQL log line 26304. Its committed row is capture 58. |
| 13:41:24.639 | Capture is marked completed with 88 snapshots; SQL line 32301. |
| 13:41:25.030 | Step 531 commits the completed capture and saved handoff. |
| **13:41:25.282** | Lifecycle evaluation starts; application line 688614. Last job progress remains sequence 239 at 13:41:25.412. |
| 13:44:07.233 | A second capture-evaluation insert occurs inside the evaluation stage; SQL line **36409**. |
| 13:44:14.808 | `upsert_snapshots` reads the existing snapshots; SQL line **36442**. No new setup snapshot insert occurs during this stage. |
| 13:44:29.529 | The recapture attempt is marked completed; SQL line **36760**. |
| 13:44:29.950 | `_completed_publication` checks for a prior completed lifecycle publication; SQL line **36767**. |
| 13:44:29.955 | The inner evaluator inserts its evaluation attempt; SQL line **36770**. |
| **13:44:30.154** | The handoff validator reads the 88 exact snapshot IDs; SQL line **36777**. |
| **13:44:30.354** | The semantic writer rolls back its savepoint and parent work after the ownership exception; SQL line **36779**. |
| 13:44:30.502–.514 | Separate failure audit 61 is inserted and completed; SQL lines **36786–36795**. |
| **13:44:30.524** | Step 532 is recorded `FAILED`; parent pipeline fails at 13:44:30.591. |
| 13:44:31.808 | Job 1416 becomes terminal `FAILED`, classification `DETERMINISTIC`, `retryable=false`. |
| 13:44:33.098 | A post-job memory recycle is requested, after failure bookkeeping has completed. |

The pipeline's persisted elapsed time was **52m48.267s**, including the CERI wait. The failed stage lasted **3m05.241s**. Audit row 61's **15ms** duration describes creation/completion of the failure audit, not the attempted evaluation's runtime.

The SQL log above is `logs/db-monitor/worker/sql-2026-10-18-p69808.6.jsonl`; application references are to `logs/lifecycle-worker.log`. Selected original records, including their file and line addresses, are in [sql-publication-sequence.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/sql-publication-sequence.json) and [application-events.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/application-events.json).

**The causal proof, step by step**

1. **The production behavior switch was false, not merely a local default.** Job 1416 and pipeline 41 are bound to configuration anchor `d0f2266758cd3d46984c1601a154908feba3904c61c35839a01175824f028632`. Its retained `execution.settings` record explicitly contains `setup_capture_handoff_enabled=false`. The application default is also false at [settings.py:344](C:/Users/Ivica/Documents/SwingLens/app/settings.py:344). See the retained [configuration anchor](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/configuration-anchor.json) and [execution settings](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/execution-settings.json).

2. **The valid stage handoff existed, but the pipeline suppressed it.** Step 531's `result_json.setup_capture_handoff` names capture 58 and the exact 88 IDs. The resumed path passes `capture_result` only when the switch is enabled, at [pipeline_executor.py:1620](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_executor.py:1620). The evaluator therefore receives no handoff on this path. The same conditional exists in the non-resumed pipeline at line 1146.

3. **The new outer wrapper converts that absence into a fresh internal handoff.** At [evaluation_service.py:146](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/evaluation_service.py:146), a real SQLAlchemy `Session` with no supplied capture/IDs triggers `capture_snapshots_for_run`. The wrapper assigns its result to `capture_result` and forwards it into `_evaluate_run`. Consequently the inner call takes its handoff-validation branch even though the pipeline's handoff optimization was disabled.

4. **Idempotent recapture preserves the snapshots' creator.** [repository.py:546](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/repository.py:546) finds snapshots by semantic identity. That identity includes run, ticker, timeframe, data date, engine, config and source hash, but excludes evaluation-attempt ID ([repository.py:2373](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/repository.py:2373)). Existing entries are validated and reused. Retry comparison deliberately excludes `evaluation_run_id` through `_SETUP_PROJECTION_FIELDS` ([repository.py:782](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/repository.py:782), [decision_evidence.py:41](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/decision_evidence.py:41)). The capture result nevertheless reports the **new attempt's** ID at [snapshot_builder.py:1300](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/snapshot_builder.py:1300).

5. **The consumer confuses capture attempt with artifact creator.** The validator computes `expected_evaluation_run_id = capture_result.evaluation_run_id`, then demands equality with every `snapshot.evaluation_run_id` at [evaluation_service.py:642](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/evaluation_service.py:642). Reused snapshots still belong to 58, so a new attempt ID fails that condition. This is the exact exception message preserved in the pipeline, step, job, evaluation audit and application failure log.

6. **The original artifacts are internally consistent.** All 88 saved IDs exist, all belong to run 41 and owner 58, and all match capture 58's config and engine. Every snapshot carries context 74, the same frozen cutoff and session. These checks are recorded in [causal-checks.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/causal-checks.json). There is no evidence that another process reassigned ownership or that the saved original handoff was corrupt.

Production telemetry records three evaluation-row inserts during the failed stage, but only the separate failure audit survives. IDs 59 and 60 are absent from persisted evaluations, consistent with rolled-back recapture/inner attempts. Their precise assignment is inferred from the SQL order and sequence gaps; parameter telemetry stores keyed digests, not returned IDs. The root-cause proof does not depend on assigning those two numbers.

**Native reproduction and positive control**

[reproduce_recapture.py](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/reproduce_recapture.py) was executed against migrated disposable database `swinglens_pytest_83a633e16dc6`. It uses the repository's synthetic upstream financial fixtures and the actual capture, upsert, evaluator, pipeline invocation adapter and downstream publication writers. It calls no market-data providers and patches no financial validator. A capture subclass only records the native recapture return value.

| Witness | Executed result |
|---|---|
| First capture | Attempt **1** creates snapshot **1**, owner **1**; original handoff validation passes. |
| Invoke evaluator with omitted handoff | Native recapture attempt **2** returns the **same snapshot ID 1**, preserving owner **1**. |
| Inner evaluation | Raises the **identical production ValueError**. A separate failed audit is retained. |
| Change only the expected attempt owner | The real handoff validator raises the same ownership exception. |
| Positive control, pass original handoff | Same invocation adapter completes native evaluation **5**, `COMPLETED`, one canonical snapshot, one active episode, zero failures. Snapshot creator remains **1**. |
| Cleanup | Disposable database dropped. |

The exact results are saved in [reproduction-results.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/reproduction-results.json). This is a one-symbol causal witness, not a claim that the whole production universe has been replayed successfully. It isolates the ownership contract without scale or concurrency as prerequisites.

The existing evaluator unit suite also passed: **8 passed** ([JUnit](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/existing-evaluation-tests.xml)). Those tests do not invalidate the reproduction: they use fake sessions or already-valid handoffs and do not exercise this real-Session recapture sequence.

**Regression origin and coverage gap**

The working-tree diff against `96204fb` introduces the outer pre-capture wrapper while moving the previous body into `_evaluate_run`. The committed version captured internally when the handoff was absent; that path did not turn the recapture result into a separately validated handoff. The new placement exposes the incompatible ownership assumptions. This change is part of the existing, uncommitted run-40 lifecycle remediation in the checkout. The production SQL sequence corroborates the new wrapper's behavior.

The previous certification did not cover the production switch combination:

- The complete-pipeline certification explicitly forces `SETUP_CAPTURE_HANDOFF_ENABLED=true` at [test_single_run_certification.py:237](C:/Users/Ivica/Documents/SwingLens/tests/e2e/single_run_certification/test_single_run_certification.py:237); retained run-40 E2E environment artifacts also show true.
- The native run-40 lifecycle integration cases pass `capture_result=capture` explicitly.
- The evaluator's fake-session tests bypass the outer wrapper's `isinstance(db, Session)` condition.

These successful checks establish other behavior, but leave the deployed/default **handoff-disabled + real Session + already-captured run** path uncovered.

**Persisted impact and alternative explanations**

| Run-41 artifact | Persisted count |
|---|---:|
| Captured setup snapshots / immutable SETUP evidence | **88 / 88** |
| Current canonical selections / selection events using those snapshots | **0 / 0** |
| Signal changes / lifecycle events | **0 / 0** |
| Lifecycle evaluation evidence / transition evidence | **0 / 0** |
| Episodes currently using those snapshots | **0** |
| Setup alerts owned by run-41 evaluations | **0** |
| Winner prediction snapshots | **0** |

Capture was a previously committed stage and survives. Lifecycle publication failed at input validation, so the later business outputs were never published. Winner's step 533 remains `PENDING`. Counts are scoped to run 41 and its snapshots/evaluations; they do not assert that the entire application has no historical lifecycle data. The exact count queries are preserved in [database-before.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/database-before.json).

The worker records one actual attempt for job 1416, with deterministic non-retryable classification. `retry_count=1` is incremented during failure handling; it is not evidence of a second executed attempt. The terminal state is appropriate for this repeatable ValueError; increasing retry counts would repeat the defect.

The recorded evidence distinguishes this failure from several plausible alternatives:

- **Watchdog/lock timeout:** the last supervisor sample at 13:44:28 reports `HEALTHY_WITHIN_PROGRESS_TIMEOUT`, progress age 182 seconds against the lifecycle threshold of 1,800 seconds. The failed stage contains **3,743 attributed SQL operations, zero failed SQL records**, and only evaluation-audit inserts. The server log scan found no `ERROR`, `FATAL` or `PANIC` in the wider 13:31–13:44 window. The exact Python ownership error and native reproduction supply the positive causal evidence; absence of database errors alone would not prove it.
- **CERI/provider failure:** dependency 56 completed, certified all 88 subjects, and the decision handoff plus setup-capture stages completed afterward.
- **Missing sector context:** capture reported 176 warnings, `MISSING_OPTIONAL_CONTEXT` and `MISSING_SECTOR_ROTATION` for the 88 symbols. They were warnings in a successful capture. The one-symbol positive control also completes with two warnings. They merit a separate data-quality review but do not explain the ownership predicate.
- **OOM or worker death:** failure and job bookkeeping preceded the orderly memory recycle. The recycle event records 2,434,572,288 private bytes against a 2,048 MiB threshold. Memory pressure is operationally relevant, but the observed terminating exception was deterministic validation, not process death.

Evaluation-stage cursor timers sum to **12.521s** against approximately **185.241s** wall time. This excludes Python work, ORM materialization, telemetry and scheduling; it does not establish a CPU allocation. Recapture added redundant reconstruction/verification of 88 already captured inputs. Its wrapper also omits the available progress callback, explaining the absence of useful progress after the initial evaluation-stage checkpoint. SQL telemetry additionally reports a 553.444s transaction spanning part of the original capture. Its lock scope cannot be inferred from elapsed time alone and should be examined separately.

See [sql-statistics.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/sql-statistics.json), [supervisor-decisions.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/supervisor-decisions.json), and [postgresql-errors-in-stage-window.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/postgresql-errors-in-stage-window.json).

**Recommendations, in priority order**

1. **Repair the attempt/creator contract.** Keep immutable snapshot creator provenance. For pipeline execution, reuse the completed stage's validated exact-ID handoff when that path is selected. The supported handoff-disabled path must also handle idempotent recapture: represent capture-attempt membership separately from snapshot creation ownership, or resolve and validate the original capture provenance for reused artifacts. Validate exact IDs, source evidence, config, scope and frozen market context. Account for mixed old/new snapshots rather than assuming all artifacts were created by one new attempt. Do not fix this by rewriting owners, disabling the ownership check globally, or setting the expected owner to null without equivalent provenance proof.

2. **Contain the deployed mismatch and recover run 41 through supported authority.** Enabling handoff for newly enqueued, newly anchored runs avoids this particular recapture path; the native positive control supports that containment, not a complete production-release certification. Existing run 41 has an immutable **false** configuration anchor. Editing `.env` alone does not change that anchor, and forcing conflicting dependency flags is rejected by configuration delivery. First certify a code correction under the existing false anchor and frozen context in isolation. Then use the supported [resume_pipeline](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_service.py:1128) entry point from `EVALUATING_SETUP_LIFECYCLES`, preserving capture 58 and IDs 3498–3585, prior completed stages and frozen inputs. The current resume path also applies the false handoff flag and would reproduce the defect without that correction. Do not manually queue job 1416 or rewrite terminal/ownership records.

3. **Close the exact regression gap.** Promote the native reproduction into a durable PostgreSQL regression. Exercise both flag values, root and CERI-resumed pipelines, preexisting capture plus evaluation without a handoff, explicit original handoff, duplicate recapture, mixed snapshot creators, cancellation/failure rollback and replay. Assert publication success where valid, unchanged creator IDs/evidence bytes, no duplicate financial artifacts and no partial serving output. Repeat representative 88-symbol/history certification with the production/default configuration, preserving the actual flags in the evidence.

4. **Expose structured provenance diagnostics.** Replace the bare ownership ValueError with a typed deterministic error carrying pipeline/run IDs, capture-attempt ID, expected creator provenance, offending snapshot ID/ticker, actual creator and context. Emit the resolved handoff switch, supplied-versus-internal handoff path, and recapture/reuse counts. Preserve redacted traceback frames; the production `job.failed.stack` currently contains only the exception's one-line text. Distinguish failed audit duration from attempted-stage duration.

5. **Remove redundant work and restore bounded progress.** Forward progress and cancellation through any required recapture, including preparation and validation. Measure reused/new counts and reconstruction costs; profile before attributing the non-SQL time to CPU. Inspect the original capture's long transaction and memory growth separately. Preserve atomic lifecycle publication and exact-source revalidation while reducing duplicate work. Changing lock/progress timeouts does not repair this ownership mismatch.

**Preservation and reproducibility**

The evidence collector enforces `default_transaction_read_only=on`, verifies `transaction_read_only`, and uses a repeatable-read snapshot. It records the complete pipeline, ordered steps, related jobs excluding raw execution tokens, evaluations, dependencies, configuration bindings and anchor, impact counts, and SHA-256 fingerprints of every complete run-41 setup snapshot row. Large financial payloads are represented by their full-row hashes plus relevant provenance fields.

Before/after protected-state hashes match:

```text
4e762bc00d53045c71eb61a0e8c3582e150bf6cea07b39d7c8f8982f4eba9ee5
```

See [preservation-before.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/preservation-before.json) and [preservation-after.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/preservation-after.json). Captured source-log prefix lengths and hashes are in [source-log-sha256.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/source-log-sha256.json). [evidence-sha256.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run41-lifecycle-failure/evidence-sha256.json) hashes the investigation artifacts.

Commands executed from `C:\Users\Ivica\Documents\SwingLens`:

```powershell
.venv\Scripts\python.exe docs/forensics/run41-lifecycle-failure/collect_evidence.py before
.venv\Scripts\python.exe docs/forensics/run41-lifecycle-failure/reproduce_recapture.py
.venv\Scripts\python.exe -m pytest tests/setup_lifecycle/test_evaluation_service.py -q --junitxml=docs/forensics/run41-lifecycle-failure/existing-evaluation-tests.xml
.venv\Scripts\python.exe docs/forensics/run41-lifecycle-failure/collect_evidence.py after
```

The preserved before/after snapshots bracket the final evidence collection; the reproduction had already completed before that final collection pass. Earlier read-only extracts are also retained. No claim is made that the command list is a chronological transcript of every exploratory read.
