# Task 2.9.12o CERI Reconciliation

## Finding

The previous values `CERI capture = 100`, `ceri_score_snapshots = 0`, and
`ceri_completion_state = CERTIFIED` described the **same intended certified population**. The zero
was not a different scope or a scoring result; it was the pre-provider default retained by the
asynchronous pipeline continuation roll-up.

The persisted CERI snapshots were present. The defect was reporting/continuation state, not CERI
scoring or evidence persistence.

## Authoritative population

At the CERI completion barrier:

- expected count = distinct `raw_company_rows.ticker` for the exact pipeline upload run;
- certified count = distinct `ceri_score_snapshots.ticker` where:
  - `run_id` equals that upload run;
  - `calculation_context_id` equals the retained CERI context;
  - `evidence_id IS NOT NULL`.

The barrier rejects zero expected members and any `certified != expected` result. It does not count
latest, cross-run, context-free, or uncertified rows.

## Correction

On both a newly created continuation and an already-existing/coalesced continuation,
`pipeline_runs.result_json` now publishes:

```text
ceri_completion_state = CERTIFIED
ceri_certified_capture_count = <exact barrier count>
ceri_score_snapshots = <same exact barrier count>
```

The correction is confined to the roll-up contract. It does not change CERI formulas, input
selection, normalization, feature construction, evidence requirements, or score publication.

## Verification

- The real PostgreSQL completion-barrier regression proves the public and certified counters are
  equal (`1 == 1`) for the exact run/context and that only one continuation exists after replay.
- The missing/incomplete certified capture negative still fails before downstream continuation.
- Initial and resumed executor tests retain the CERI count through result merge.
- The 163-test cross-stage selection, including CERI barrier and decision/Setup/Winner consumers,
  passed with 0 failures and 0 errors.
- The final Chromium-100 execution completed the CERI child workflow and advanced through the
  frozen decision handoff into Setup. It did not fail a CERI count or evidence assertion.

## Status

**CERI RECONCILIATION: PASS.** This does not imply overall Chromium or release PASS; the final
execution failed the independent Setup transaction-age invariant.

