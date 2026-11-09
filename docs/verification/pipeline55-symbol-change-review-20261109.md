# Pipeline 55 symbol-change and recovery source review

This review records static source/test ownership for the November 9, 2026 change.
It does not authenticate a human approval, issue a certificate, or promote the
local checks to a current release verdict. All existing core release lanes,
source freshness requirements, golden approval rules, and live-provider limits
remain in force.

## Source boundaries

| Sources | Accountable domain | Required behavior |
| --- | --- | --- |
| `instrument_symbol_changes.py`, the dated JSON registry, and IB resolution/fetch services | IB acquisition and market-data maintainers | A provider rename requires exact sourced common-stock identity, preserved source ticker and reviewed session. |
| Pipeline recovery/subset services, run routes and progress/detail templates | Pipeline certification and stage-contract maintainers | Recovery preserves the original plan, scope, fundamentals and failed attempts; partial admission is explicit and cannot exclude unknown identities. |
| `ranking_profile_service.py` | Core calculation and configuration maintainers | Ranking uses only the admitted cohort; the original financial formulas and evidence checks remain unchanged. |
| Shared `app.js` recovery controls | Pipeline certification and stage-contract maintainers | Recovery and coverage are disclosed consistently with the server-validated scope. This conservative file-level association selects pipeline checks for future changes to the shared file. |

## Positive and refusal boundaries

The new unit cases reach real symbol-change validation, contract resolution,
plan serialization and fetch execution using broker/database doubles. Their
positive expectations explicitly name TCBI, TXCP, contract 21521400 and the
November 6 session. The refusal cases change contract ID, security type, currency,
listing, evidence or session; an unrelated provider symbol must cause zero
historical requests and zero cache writes.

The PostgreSQL recovery case uses migrated disposable databases, native source
writers, fetch attempts, bar persistence and readiness revisions. Only the broker
is simulated. It retains the first failed attempt and acquisition plan, then
requires both historical feeds under the original ticker and a READY revision.
The lifecycle case checks both observation-time and effective-session boundaries.

The existing subset case proves isolated derived uploads/scopes and idempotency
with PostgreSQL. Unit refusal cases continue to reject changed exclusions and
unknown identities. Template tests verify recovery controls and original coverage
denominators; they do not establish full browser interaction coverage. Existing
A04 browser/worker obligations remain separate and required.

The no-orders transport case keeps the actual conversion and read-only broker
calls, uses a broker double that rejects every order method, and isolates the
PostgreSQL acquisition coordinator. Its database double assigns flush IDs as the
native database does. Separate native process tests cover shared slot ownership,
handoff, cancellation and pacing; these unit fixtures do not supply that proof.

## Validation scope

The integrated change passed 64 focused unit cases, four selected PostgreSQL and
cross-process recovery/concurrency cases, full Ruff checks and route inventory
checks. A separately authorized read-only local Gateway probe confirmed both
feeds through November 6. That probe does not substitute for CI, a current A04
certificate, or future live-provider compatibility.

The registry additions retain `STATIC_INTENT`, attach the meaningful unit and
native selectors above, and record their normalized test-source identities.
Release preparation must recompute impact for the new commit and execute the
required lanes. No existing selector, historical artifact, financial expectation,
approval requirement, release predicate or unknown-source refusal is removed.
