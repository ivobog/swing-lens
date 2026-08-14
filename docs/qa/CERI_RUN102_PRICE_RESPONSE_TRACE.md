# CERI Run 102 Price Response Trace

## Run 102 root cause

The rebuild and capture paths admitted null/upcoming earnings, unaccepted
guidance, and issuer-unverified catalysts as candidate parent events. Old NVDA
rows then failed with only `reaction_bars_unavailable`, while a company with no
usable event produced no diagnostic feature at all. Thus `price_response_unavailable`
mixed parent-eligibility defects with genuine price-window dependencies.

Both parent selectors now require:

- reported earnings with a non-null actual;
- guidance with `accepted_for_scoring is TRUE`; or
- issuer-relevant, non-rejected catalyst evidence.

No eligible parent now persists `NO_ACCEPTED_EVENT`. Other exact codes are
`EVENT_TIMESTAMP_UNRESOLVED`, `PRICE_DATA_MISSING`, `WINDOW_NOT_ELAPSED`, and
`PIT_UNSAFE` (reserved for the PIT guard).

## Deterministic positive vertical

| Field | Result |
|---|---|
| Accepted parent | reported earnings event 7 |
| Event/session | `2026-08-04` |
| Stock/benchmark source | IB daily bars only; MSFT / SPY |
| Gap | `0.0` |
| 1d stock return | `0.03` |
| 1d benchmark return | `0.01` |
| 1d relative return | `0.02` |
| Volume ratio | `1.0` |
| Close location | `0.5714285714` |
| Price Response quality | `6.0` |
| Reason | `positive_relative_1d` |
| 3d state | unavailable because the deterministic window has not elapsed |

The positive fixture proves an accepted event and valid elapsed price window
produce selected Price Response evidence. It does not fabricate an event for
coverage. For the current live NVDA evidence, the post-fix exact first cause is
`NO_ACCEPTED_EVENT`; a future accepted event may then expose a genuine IB price
history/window dependency.
