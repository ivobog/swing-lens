# CERI Run 104 Baseline

Captured before Run 104 production or test changes on 2026-08-14 (Europe/Zurich).

## Repository and schema

- Branch: `codex/ceri-run101-remediation`
- HEAD: `a30f18f2f49ceca122277712ce57edfe339472ac`
- Dirty state: three pre-existing, untracked user-owned verification artifacts:
  - `docs/verification/ceri-nwe-run96.7z`
  - `docs/verification/ceri-nwe-run96/`
  - `docs/verification/ceri-queue-cleanup/`
- Alembic head/current: `0043_ceri_run102_relative_evidence` / `0043_ceri_run102_relative_evidence`
- Baseline command: `pytest -q tests/ceri`
- Baseline result: `299 passed, 1 warning in 66.38s`

The prior Run 101 remediation/forensic artifacts and all Run 102 baseline,
diagnostic, remediation, price-response, and traceability artifacts were read
before implementation. The Run 101/102 invariants remain controlling: literal
`accepted_for_scoring is TRUE` for SEC guidance, missing is not zero,
same-provider relative EPS comparability, absolute/cross-provider currency
gates, PIT selection, the 60% Opportunity coverage gate, the Confidence hard
gate, and Opportunity/Event Risk independence.

## Run 104 population baseline

| Surface | Count | Evidence |
|---|---:|---|
| Supplied raw JSON snapshot array | 177 | `C:/Users/Ivica/Downloads/ceri_export (4).json` |
| Production database snapshots | 177 | read-only query of `ceri_score_snapshots.run_id = 104` |
| Unique database tickers | 177 | read-only distinct ticker reconciliation |
| Duplicate database tickers | 0 | read-only grouped ticker reconciliation |
| API list items reachable with the route default | 100 | `/api/ceri/run/{run_id}` declares `limit: int = 100`; query page slices to `offset:offset+limit` |
| GUI rendered rows | 100 | supplied rendered page evidence has 100 `Run 104`, evidence-count, and warning row markers |
| GUI rows omitted from first/default page | 77 | 177 minus 100 |
| Observed High Opportunity / Low Risk card | 1 | primary Run 104 specification and reproduced production predicate |

The live development server on `127.0.0.1:8000` accepted connections but did
not complete either a direct Run 104 request within 30 seconds or the browser
navigation within 108 seconds. Source inspection explains the baseline latency:
the query service serializes every full row, including per-row evidence queries,
before sorting and applying its list slice. This timeout does not change the
count evidence above.

## Snapshot distribution

- Rated Opportunity: 173
- Unrated Opportunity: 4
- Event Risk exactly `0.0`: 169
- Rows with one or more warnings: 177
- Confidence: High 1, Normal 169, Low 5, Insufficient 2
- Posture: Positive 31, Improving 55, Mixed 73, Deteriorating 14, Unrated 4
- Opportunity coverage: 70% for 133, 65% for 37, 60% for 3, 55% for 2,
  35% for 1, and 30% for 1.

## Baseline defect locations

1. `app/routers/ceri_routes.py`: both the run HTML route and run API route
   default to `limit=100`.
2. `app/services/ceri/query_service.py`: `_page` sorts serialized rows and then
   slices `ordered[offset:offset + limit]`; descending sort uses Python reverse
   ordering that puts `None` first.
3. `app/templates/ceri_dashboard.html`: no page navigation controls exist.
4. `app/routers/ceri_routes.py`: the dashboard summary receives only
   `payload["items"]`, the paginated subset.
5. The same summary helper uses `(event_risk_score or 10)`, which treats the
   valid numeric value `0.0` as missing.
6. `app/routers/run_routes.py` repeats the same truthiness predicate over the
   full database population.

No production database rows were changed during baseline capture.
