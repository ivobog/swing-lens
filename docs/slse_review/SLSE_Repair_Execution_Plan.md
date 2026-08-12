# SLSE Repair Execution Plan

## Current authoritative status (2026-08-12)

| Work item | Original finding | First-pass disposition | Second-pass disposition | Current authoritative status |
|---|---|---|---|---|
| DEF-030 / WP-1 | No complete production-input proof | Not started | Not started | **COMPLETE** — executable adapter coverage audit passes. |
| DEF-031 / WP-2 / FR-031 | No typed history | PARTIAL | History transported | **COMPLETE** — temporal family behavior now consumes history and paired-history tests pass. |
| DEF-032 / WP-3 confidence | Conflated confidence evidence | PARTIAL | Top-level blend repaired | **COMPLETE** — exact agreement and freshness/lineage semantics pass. |
| DEF-033 / WP-3 actionability | Incomplete precedence | PARTIAL | Reduced posture repaired | **COMPLETE** — compound precedence truth table passes. |
| DEF-034 / WP-0 governance | Stale status prose | Addenda | Addenda | **COMPLETE** — current tables are authoritative. |
| WP-4 onward | Multiple functional/certification gaps | PARTIAL | PARTIAL | **IN PROGRESS / BLOCKED FOR RELEASE** — engine stays disabled; rebuild stays prohibited until golden and natural-run gates pass. |

Current identity: engine `slse-1.2.0`, config `2026-08-12`, schema `slse-snapshot-1.0.0`. Focused semantic gate: 206 tests and Ruff pass.

## Completed

- Read the full audit plan, SRS, and SDD.
- Extracted the complete requirement set and identified specification ambiguities.
- Audited schema, loader, snapshot mapping, coverage, canonicalization, registry, change detector, velocity, lifecycle, episodes, confidence, actionability, alerts, query/API, templates, exports, and current tests.
- Captured a pre-repair baseline of 151 passing SLSE tests.
- Created the traceability matrix, field lineage, GUI/API contracts, defect register, historical remediation decision, and golden-fixture matrix.
- Repaired required close/coverage, confidence composition, point-in-time context, rank direction, quality changes, alert truth tables/restrictions, stable DTOs, combined event sources, aggregates, ordering, filters, links and v2 exports.
- Passed 174 focused SLSE/PostgreSQL tests, an eight-ticker real pipeline run, and a populated Playwright Market Changes/Alert Center interaction fixture.
- Captured read-only historical impact counts; no retained data was mutated.

## Repair order

1. Correct snapshot required-field mapping and persist canonical required coverage in the normalized DTO.
2. Correct confidence composition and change-event confidence evidence.
3. Correct normalized rank delta and data-degradation signals.
4. Enforce alert truth tables, acceleration windows/thresholds, restrictions and explicit source semantics.
5. Build stable Market Changes and Alert DTOs and full filtered-scope aggregates.
6. Combine lifecycle and signal-change streams and implement explicit date/filter/sort/pagination semantics.
7. Update Jinja/HTMX fields, source links, review-state actions and enum options.
8. Expand CSV/JSON schemas and preserve filters/sort semantics.
9. Add disposable-PostgreSQL integration tests, golden sequences, property/invariant tests and Playwright assertions.
10. Run a real source evaluation, capture GUI/database evidence and make the final readiness decision.

## Remaining release gates

- Resolve or explicitly adopt the recommendations for SLSE-AMB-001 and SLSE-AMB-002.
- Complete the versioned 25-scenario golden fixture corpus with per-date snapshot→alert→DTO assertions.
- Pass a second real source run that naturally produces lifecycle and acceleration alerts (the populated browser fixture is synthetic, though persisted through real PostgreSQL/API/UI).
- Supply typed prior canonical snapshot history to family adapters where SRS FR-031 requires it.
- Complete operator scopes, timeline pagination/version labels, Alert blocker/actionability/date-range filters and the remaining DTO version/hash fields.
- Execute the chosen development/QA rebuild and document before/after counts.
- Meet or document the 1,000-ticker/100k-row performance targets and dedicated accessibility audit.

## Second-pass amendment (2026-08-11)

Completed in specification order:

1. Confirmed and fixed DEF-026; removed the unauthorized second-stage confidence averaging and added exact formula/boundary tests.
2. Confirmed and fixed DEF-027; reduced market posture is now WATCH_ONLY with explicit metadata, independent of LOW_CONFIDENCE and BLOCKED.
3. Confirmed and fixed DEF-028; freshness now uses completed US trading sessions and holiday-aware tests.
4. Confirmed and fixed DEF-029 in the reachable pure-engine/replay contract; FAILED/EXPIRED actionability differs correctly and prior confidence is preserved instead of set to 100.
5. Completed FR-031 typed prior canonical history with a configured 10-session bound, point-in-time validation, one batched query, and chronological roll-forward.
6. Bumped behavior/config identity to engine `slse-1.1.0`, config `2026-08-11`; snapshot schema remains `slse-snapshot-1.0.0`.

Safety disposition: historical SLSE tables were not purged, replayed, repaired, superseded, or rebuilt. The next permitted step is the complete full-layer golden corpus. Only after that and a natural multi-date source certification pass may dev/QA history be rebuilt.
