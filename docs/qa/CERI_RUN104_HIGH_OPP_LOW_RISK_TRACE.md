# CERI Run 104 High Opportunity / Low Risk Trace

This trace records the production rule before Run 104 remediation.

## Production paths

### CERI dashboard/run page

`GET /runs/{run_id}/ceri` calls `CeriQueryService.run`, whose default list
query is `limit=100, offset=0`. The route passes only `payload["items"]` to
`ceri_routes._dashboard_summary`. The template renders
`summary.high_opportunity_low_risk` in the `High Opp / Low Risk` card.

### Run overview widget

The main run page calls `run_routes._ceri_context`, which reads the full run
snapshot population and exposes `high_opportunity_low_risk_count`.

### API and DTO fields

`GET /api/ceri/run/{run_id}` exposes the paginated CERI rows. The production row
DTO fields consumed by the predicate are `opportunity_score` and
`event_risk_score`. `data_confidence` is present in the DTO but is not used by
either production predicate. There is no separate summary API response at the
baseline.

## Exact pre-change predicate

Both widgets use hard-coded thresholds:

```python
(snapshot.opportunity_score or 0) >= 7
and (snapshot.event_risk_score or 10) <= 3
```

The CERI dashboard applies it to its paginated `items`; the run overview applies
it to all database snapshots.

The thresholds correspond to the existing Positive posture boundary (7.0) and
the current low-risk ceiling (3.0), but they are not named policy values in the
baseline configuration. The defect is Python truthiness: numeric Event Risk
`0.0` becomes fallback `10` and fails the predicate.

## Run 104 reconciliation

- Pre-change production predicate count: **1**
- Pre-change matching ticker: **GOLD** (`Opportunity 7.9259475`, Risk `1.5`)
- Literal `Opportunity >= 7 and Event Risk <= 3` count: **31**
- All 31 literal matches have posture `Positive` and persisted
  `event_risk_ledger.accepted_evidence = true`.
- KTB: Opportunity `4.23808680952381`, Risk `0.0`, Confidence `High`, posture
  `Mixed`; it does not satisfy High Opportunity and must remain excluded.

Literal Run 104 matches:

```text
AEIS AMG AMN AMZN APH AVT CNR DAL DBRG DHT EME ET FLYW FORM FTNT GOLD HCI
HWM IESC LNG MSGE OSCR PKE PR RAMP STX TER TFIN TSEM VSXY WTS
```

## Required remediation contract

The corrected predicate must:

1. test `None` explicitly so zero remains numeric;
2. use Opportunity, never Confidence;
3. require an explicit sufficient risk-evidence state;
4. run against the full filtered snapshot population before pagination;
5. be shared by the CERI dashboard and run-overview widget;
6. expose the full summary population count so the result is reconcilable.

## Post-remediation result

The thresholds are now named constants and the shared population summary uses
explicit `None` checks, `Opportunity >= 7.0`, posture `Positive`, Event Risk
`<= 3.0`, and risk evidence state `SUFFICIENT`. It runs before pagination and
returns both `population_count` and the matching ticker list. The dashboard and
run-overview widget consume this same result.

- Summary population: 177
- Matching count: 31
- KTB: excluded (Opportunity 4.238087)
- Confidence used by predicate: no
