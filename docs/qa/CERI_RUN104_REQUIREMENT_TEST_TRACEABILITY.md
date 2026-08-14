# CERI Run 104 Requirement/Test Traceability

| Requirement | Production implementation | Focused evidence |
|---|---|---|
| Pagination completeness and metadata | `CeriQueryService._snapshot_page`, run routes, dashboard controls | `test_pagination_metadata_and_all_rows_are_complete_without_duplicates`, route UI pagination test |
| Opportunity DESC NULLS LAST | `_ordered_nulls_last` | `test_descending_opportunity_sort_places_nulls_last_across_pages` |
| Summary/table full-population equality | `snapshot_population_summary` | summary full-population route and query tests |
| Exact High Opportunity / Low Risk predicate | named constants plus risk sufficiency | summary explicit-zero predicate test |
| Rated coverage display | Opportunity DTO and dashboard template | rated coverage route test |
| Coverage equals available configured weights | `_opportunity_payload` | snapshot coverage reconciliation test; 177-row audit |
| Risk score vs evidence state | `_risk_evidence_state`, `low_risk_eligible` | snapshot risk semantics test |
| Freshness vs normalized/eligible/selected | `_evidence_diagnostics` | staged source API test |
| Catalyst none-eligible vs unavailable/stale/ineligible | `_dataset_evidence_state` | parameterized catalyst-state test |
| Evidence lineage invariant | `_lineage_reconciliation` | lineage reconciliation and 177-row audit |
| XPEL regression | evidence ledger accepts selected Breadth dimension | XPEL selected-unavailable-pair regression test |
| Warning count/severity/dominant | `_warning_summary` and dashboard | blocker warning presentation test |
| Revision percent formatting | `_format_signed`, templates | signed revision parameter test |
| Breadth formatting/definition | `_format_signed`, templates | signed Breadth parameter and route tooltip tests |
| Extreme revision trace | revision detail plus selected-value reconciliation | revision audit and mismatch regression test |
| Direction divergence | revision detail exposes magnitude, counts, Breadth | named audit table; deliberately non-blocking |
| Price Response visibility | component DTO and ticker template | ticker detail Price Response test |
| Confidence edge cases | unchanged confidence/direction separation | production Run 104 audit for KTB/PKE/DBRG |
| 177 snapshot/API/UI parity | pre-page sorting/paging and full summary metadata | raw/database parity audit plus 177-row pagination test |
| Rebuild value/lineage atomicity | `_copy_revision_derived` copies all derived lineage | feature rebuild regression test |

The focused suite is additive to the existing CERI suite. Final result:
`320 passed in 16.17s`; changed Python and the full CERI test tree also pass
Ruff.
