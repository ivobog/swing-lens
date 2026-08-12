# SLSE Golden Fixtures and Test Matrix

Each versioned fixture is a sequence of original source values by trading date. Assertions are required for snapshot scalars/signals, warnings, coverage/quality, confidence components/final score, family/phase/state, actionability/blockers, signal changes, lifecycle events, alerts, Market Changes DTO and Alert DTO.

| Fixture | Core dates/condition | Required assertions |
|---|---|---|
| clean breakout | developing → tightening → ready → trigger → two-session confirm | ordered single transitions; NEW_READY/NEW_TRIGGER/NEW_CONFIRMATION once; actionable |
| failed breakout | triggered then hard failure | immediate FAILED, episode closed, NEW_FAILURE RISK |
| clean bull pullback | support approach/hold/reversal/follow-through | pullback phases and confirmation persistence |
| deteriorating pullback | support loss | immediate FAILED, no positive alert |
| VCP | contraction 1/2/3, dry-up, pivot ready, breakout | VCP phases; close authority |
| continuation | pause/tight range/trigger/follow-through | continuation phases and actionability |
| extended momentum | valid post-trigger extension | EXTENDED after allowed predecessor; NEW_EXTENSION RISK |
| choppy score oscillation | thresholds around READY enter/exit | no READY/TIGHTENING flapping or duplicate alerts |
| missing required data | each required field removed separately | null, exact warning/coverage, insufficient/no unsafe transition |
| missing optional context | market or sector missing | required coverage unchanged; context confidence component reduced only |
| stale data | old completed bar | stale warning; staleness alone LOW_CONFIDENCE |
| stale-to-fresh | quality improves across dates | explicit change; no degraded alert |
| fresh-to-stale | coverage/freshness crosses minimum | one DATA_DEGRADED; no repeat unchanged stale day |
| market gate block | READY ACTIONABLE → BLOCKED | state unchanged; one GATE_BLOCKED |
| earnings block | READY WATCH/ACTIONABLE → BLOCKED | state unchanged; earnings blocker |
| liquidity block | READY with risk flag | state unchanged; liquidity blocker |
| sector acceleration | rank 9 → 5 over configured condition | delta +4, NOTABLE, real confidence |
| score acceleration | configured 3-session rise + tracking crossing | exact window/amount/crossing; NOTABLE; real confidence |
| one filtered absence | one missing session | episode remains active |
| prolonged absence/expiry | gap exceeds family threshold | one EXPIRED and no duplicate |
| same-day revision | two different source hashes | both snapshots retained; one canonical; audit event |
| direct initial READY | first observation strongest READY | EPISODE_OPENED READY, skipped progression reason, no NEW_READY because no transition occurred |
| direct initial TRIGGERED | first observation strongest TRIGGERED | EPISODE_OPENED TRIGGERED; no fabricated READY/NEW_TRIGGER transition alert |
| retry/idempotency | repeat same evaluation | stable counts; no duplicate rows/alerts |
| canonical revision | canonical changes after later complete run | superseding version; sequential comparison uses selected canonical only |

## Mandatory regression mapping

1. Fully populated/no false missing warning: snapshot builder truth test.
2. Exact required coverage: snapshot/confidence component test.
3. No initial false GATE_BLOCKED: alert truth table.
4. No fabricated acceleration confidence: signal alert truth table.
5. NOTABLE end-to-end: enum/config/query/template/export test.
6. Explicit Alert Type: joined DTO/template/export test.
7. Full filtered-scope summaries: pagination integration test.
8. Combined Market Changes streams: query integration test.
9. Correct source ID links: DTO/template route test.
10. CSV/JSON semantic parity: export round-trip test.

## Release evidence matrix

| Layer | Unit | Property/sequence | PostgreSQL integration | API/template | Playwright |
|---|---|---|---|---|---|
| Snapshot/coverage/PIT | required | hash/null invariants | source-run capture | timeline payload | evidence expansion |
| Canonical/change/velocity | required | revision/no-repeat | unique/index/FK | combined changes | filters/sorts/counts |
| Lifecycle/episode | required | all states/hysteresis/rearm/gaps | active uniqueness/locking | Market DTO | rows/timeline |
| Confidence/actionability | required | boundary/truth tables | persisted components | explicit fields | badges/blockers |
| Alerts | required | all nine rule tables | dedupe/cooldown/FKs | Alert DTO/export | type/severity/ack/dismiss |

## Execution status

Implemented automated coverage includes the mandatory close/coverage cases, GATE_BLOCKED predecessor table, NEW_READY/NEW_EXTENSION boundaries, score and sector acceleration, DATA_DEGRADED crossings, canonical revisions, retry/idempotency, family state sequences, observation gaps, PostgreSQL DTO/count/export parity, and populated browser interactions. The complete versioned per-trading-date fixture data for every row above is not yet implemented; this matrix is therefore a release plan, not a claim that the golden-fixture gate has passed.

## Second-pass mandatory cases and current evidence

| Case | Concrete expectation | Automated status |
|---|---|---|
| exact confidence weighting | `1.00/.80/.50/1.00/1.00` at `30/25/20/15/10` = 85 | PASS (pure formula) |
| no double weighting | changing only adapter `confidence_score` does not change final transition confidence | PASS |
| confidence labels | 69 LOW; 70 NORMAL; 84 NORMAL; 85 HIGH | PASS |
| persistence component | bounded monotonic values for sessions 0/1/2/3 | PASS |
| reduced market posture | READY, confidence 90, NEUTRAL/YELLOW/MIXED/CAUTION = WATCH_ONLY + reduced metadata | PASS |
| hard market block | READY with explicit/derived bearish block = BLOCKED | PASS |
| trading freshness | Friday-Monday/Tuesday, weekend-only, Good Friday, Independence Day, Thanksgiving, Christmas, New Year | PASS |
| Friday-next-Friday | five completed sessions = NEAR_STALE under the configured 3-session grace | PASS |
| terminal EXPIRED | locked EXPIRED remains WATCH_ONLY and preserves supplied prior confidence | PASS |
| terminal FAILED | locked FAILED remains BLOCKED; missing prior confidence is 0, never 100 | PASS |
| typed prior history | ordered/bounded/no-future; batch loaded once and rolled chronologically | PASS |

These regressions correct the semantic core but do not complete the release corpus. The 25 named fixtures still lack concrete source-to-snapshot-to-event-to-alert-to-Market/Alert DTO assertions for every trading date. Golden corpus gate: **FAIL / INCOMPLETE**.
