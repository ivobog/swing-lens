# CERI Run 102 Requirement / Test Traceability

| Requirement | Primary proof | Result |
|---|---|---|
| Baseline and code map precedes implementation | `CERI_RUN102_BASELINE_AND_CODE_MAP.md` | Complete |
| Same-provider EPS missing currency remains usable | `test_same_provider_eps_relative_revision_allows_unknown_currency`; `test_same_provider_relative_eps_preserves_numeric_value_without_currency` | Pass |
| Provider raw → component ledger EPS vertical | `test_eodhd_raw_relative_eps_survives_to_component_ledger_without_currency` | Pass |
| Zero and negative revision are numeric | `test_relative_revision_preserves_zero_and_negative_direction` | Pass |
| Period/scale/provider/PIT fail closed | `test_same_provider_relative_comparability_fails_closed`; `test_provider_relative_revision_is_excluded_before_response_known_at` | Pass |
| Absolute/cross-provider missing currency rejected | `test_absolute_missing_currency_comparison_remains_rejected`; comparability parametrization | Pass |
| Near-zero baseline guarded | `test_relative_revision_near_zero_baseline_is_unavailable` | Pass |
| Analyst count does not invalidate magnitude | `test_missing_analyst_count_does_not_invalidate_relative_revision_magnitude` | Pass |
| Provider slot valid after fiscal close | `test_provider_period_slot_selects_latest_fiscal_end_even_after_period_end` | Pass |
| Breadth independent of currency/baseline | `test_dimensionless_breadth_survives_missing_magnitude_baseline` | Pass |
| Acceleration uses relative revision histories | Run 101 acceleration tests plus NVDA trace | Pass |
| Historical and upcoming earnings paths separated | `test_earnings_acquisition_separates_reported_history_from_upcoming_calendar` | Pass |
| Official earnings schema and zeros retained | `test_eodhd_official_earnings_schema_maps_reported_result_and_zero_values` | Pass |
| Reported row survives to Surprise | `test_official_reported_row_survives_provider_storage_normalization_and_surprise` | Pass |
| Future event excluded from Surprise | `test_upcoming_event_is_excluded_from_surprise_trend` | Pass |
| Post-report estimate excluded | `test_post_report_estimate_is_not_selected_as_pre_report_consensus` | Pass |
| Reported lookback only | `test_last_four_selects_only_reported_events` | Pass |
| Structured catalyst issuer relevance | `test_structured_related_symbols_control_issuer_relevance` | Pass |
| Catalyst licensed projection retains eligibility | `test_structured_catalyst_relevance_survives_licensed_storage_projection` | Pass |
| Completed event not pending risk | `test_completed_result_article_is_not_pending_binary_risk` | Pass |
| Positive pending catalyst | `test_scheduled_future_binary_event_is_eligible` | Pass |
| Rejected event cannot feed Price Response | `test_price_response_parent_selection_rejects_unusable_events`; `test_rejected_parent_event_excludes_price_response` | Pass |
| Positive Price Response fixture | `test_ibkr_price_response_is_relative_to_benchmark_and_does_not_use_other_sources` | Pass |
| Exact Price Response first causes | `test_price_response_exposes_exact_first_cause_codes`; `test_price_response_future_reaction_window_is_not_elapsed`; no-parent selector test | Pass |
| SEC null/false rejected, true accepted | `test_guidance_scoring_is_explicit_true_allow_list`; `test_explicitly_accepted_clean_guidance_can_score` | Pass |
| Run 101 SEC false positives remain rejected | `test_run101_golden_false_positive_passages_fail_closed` | Pass |
| SEC migration fail closed | `test_sec_acceptance_migration_is_safe_and_fail_closed` | Pass |
| Exact component blocker ledger | `test_component_ledger_uses_exact_first_cause_rejection_reason` | Pass |
| API exposes source/normalized/eligible/selected/blocker | `test_snapshot_api_distinguishes_source_normalized_eligible_and_selected` | Pass |
| UI renders stage diagnostics | `test_ceri_ticker_detail_renders_provenance_and_warnings` | Pass |
| 60% threshold and missing-not-zero unchanged | scoring regression suite; Run 102 Golden 10 certification | Pass |
| Golden 10 complete verticals | `test_run102_golden_certification.py`; `ceri_run102_golden10/vertical_traces.json` | Pass |

Verification commands:

```text
pytest -q tests/ceri
299 passed in 35.62s

ruff check app/services/ceri app/templates tests/ceri scripts/qa/certify_ceri_run102_golden.py alembic/versions/20260814_0043_ceri_run102_relative_evidence.py
All checks passed!

python -m alembic heads
0043_ceri_run102_relative_evidence (head)
```
