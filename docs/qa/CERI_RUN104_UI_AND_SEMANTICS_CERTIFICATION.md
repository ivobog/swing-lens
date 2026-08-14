# CERI Run 104 UI and Semantics Certification

## Certification disposition

**Run 104 GUI population completeness is certified. Full Run 104 semantic
certification is BLOCKED pending a controlled rebuild of historical revision
feature lineage.**

The blocker is visible to the API/GUI and is not hidden by this remediation.
No Run 104 database snapshot was mutated during the audit.

## Population reconciliation

| Check | Result |
|---|---:|
| Supplied raw snapshots | 177 |
| Database/API population | 177 |
| GUI reachable population | 177 |
| Summary population | 177 |
| Missing tickers | 0 |
| Duplicate tickers | 0 |
| Raw-export vs database Opportunity mismatches | 0 |
| Raw-export vs database Event Risk mismatches | 0 |
| Raw-export vs database Confidence mismatches | 0 |
| Raw-export vs database Posture mismatches | 0 |
| Opportunity coverage/available-weight mismatches | 0 |
| Available components lacking direct lineage or documented exemption | 0 |

The API now returns explicit `total_items`, page, page size, total pages,
range, and previous/next offsets. The default page size is 50. Four pages
(1-50, 51-100, 101-150, 151-177) reach all rows without duplicates or
omissions. Sorting and filters are applied before paging and Opportunity DESC
uses NULLS LAST semantics.

Production-browser verification against the real Run 104 database rendered:

- page 1: `1-50 of 177`, page 1 of 4, summary 31 / population 177;
- the actual Next link preserved `limit=50` and navigated to page 2;
- page 2: `51-100 of 177`, with both Previous and Next;
- page 4: `151-177 of 177`, page 4 of 4, containing exactly 27 rows.

The browser also rendered rated coverage, explicit risk sufficiency, signed
revision/Breadth values, staged source semantics, warning severity, and visible
`rebuild required` blockers on affected rows.

## Summary reconciliation

The High Opportunity / Low Risk summary uses the full 177-snapshot population,
never the current page. Its exact production predicate is:

```text
Opportunity >= 7.0
AND posture == Positive
AND Event Risk <= 3.0
AND risk evidence state == SUFFICIENT
```

Run 104 result: 31 matches. The summary population equality test asserts that
the card count equals a fresh count over the full production population.

## UI semantics certified

- Every rated Opportunity shows score and coverage percent.
- Coverage is the sum of configured weights for available Opportunity
  components; unavailable components and reasons are inspectable.
- Numeric Event Risk and evidence sufficiency are separate fields. `0.0` is
  eligible for Low Risk only when risk evidence state is `SUFFICIENT`.
- Source freshness, normalized, eligible, and selected states are separate.
- Catalyst unavailable, stale, none-eligible, and evidence-ineligible are
  distinct stable states.
- Warnings show count, severity, and dominant warning; full arrays remain in
  detail output.
- Revision percentage and Breadth use signed two-decimal list formatting;
  Breadth has an inline definition while raw precision remains in detail.
- Available Price Response evidence is visible in ticker detail.

## Required audit appendix

| Case | Disposition | Evidence pointer |
|---|---|---|
| DBRG extreme revision | VALID_BY_DESIGN | revision audit: +139.285714% same-provider move |
| DHT extreme revision | BLOCKER | revision audit and selected-lineage mismatch |
| MSGE extreme revision | BLOCKER | stored +50.909091% does not reproduce |
| TER extreme revision | VALID_BY_DESIGN | revision audit: +64.115729% reconciles |
| AVT extreme revision | VALID_BY_DESIGN | revision audit: +40.026596% source trace |
| FORM extreme revision | VALID_BY_DESIGN | revision audit: +38.218805% reconciles |
| CNR divergence | VALID_BY_DESIGN | -39.470118% with +1.00 provider Breadth |
| OSCR divergence | VALID_BY_DESIGN | +10.441176% with -1.00 Breadth |
| HCI divergence | VALID_BY_DESIGN | +22.393822% with -1.00 Breadth |
| ASC divergence | BLOCKER | divergence valid; other selected lineage invalid |
| COP divergence | BLOCKER | direction semantics valid; selected values invalid |
| NVST divergence | BLOCKER | current-quarter trace valid; other selected lineage invalid |
| BURL zero magnitude / positive breadth | VALID_BY_DESIGN | 0.000000% with +1.00 (16/0) |
| XPEL coverage vs selected count | FIXED | 12 Breadth IDs plus aggregate exemption |
| CBL coverage vs selected count | FIXED | 6 Breadth IDs, 1 price parent, aggregate exemption |
| KTB confidence case | VALID_BY_DESIGN | High Confidence / Mixed Opportunity independence |
| PKE confidence case | VALID_BY_DESIGN | Low Confidence / Positive Opportunity independence |
| DBRG confidence case | VALID_BY_DESIGN | Low Confidence / Positive Opportunity independence |
| estimate_coverage_low distribution | VALID_BY_DESIGN | 175/177; INFO evidence-quality warning |
| Price Response visibility | FIXED | 138 available; 39 explicit first causes |
| catalyst unavailable vs none-eligible | FIXED | staged evidence-state contract and tests |

## Blocking evidence

The production code defect that created stale value/lineage combinations is
fixed for future rebuilds. The persisted Run 104 population still contains 525
non-reproducing selected revision features across 74 tickers. Until Run 104 is
rebuilt and the same 177-row parity, coverage, lineage, summary, and UI tests
are rerun, Opportunity semantics for those tickers cannot be certified.

## Invariants

Opportunity weights, the 60% threshold, posture bands, Confidence hard gate,
missing-versus-zero behavior, literal SEC guidance acceptance, same-provider
relative EPS rules, currency gates, PIT behavior, and Opportunity/Event Risk
independence were not changed.

## Verification commands

- `ruff check` over changed Python and all CERI tests: passed.
- `pytest -q tests/ceri`: **320 passed in 16.17s**.
- Playwright production-browser verification: pages 1, 2, and 4 rendered as
  described above.
