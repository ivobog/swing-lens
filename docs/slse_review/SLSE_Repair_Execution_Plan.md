# SLSE Repair Execution Plan

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
