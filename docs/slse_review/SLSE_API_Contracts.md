# SLSE API Contracts

## Market Changes item (v2)

```json
{
  "id": 333,
  "source_type": "SIGNAL_CHANGE_EVENT",
  "lifecycle_event_id": null,
  "signal_change_event_id": 333,
  "ticker": "FIX",
  "company": "Example Inc.",
  "sector": "Industrials",
  "data_as_of_date": "2026-08-10",
  "comparison_date": "2026-08-07",
  "missing_session_gap": 0,
  "episode_id": 123,
  "setup_family": "BREAKOUT",
  "phase": "PIVOT_READY",
  "previous_state": "READY",
  "current_state": "READY",
  "transition": null,
  "state_age_sessions": 2,
  "actionability": "ACTIONABLE",
  "confidence": 86,
  "confidence_label": "HIGH",
  "technical_score": 8.1,
  "technical_score_previous": 7.6,
  "technical_score_delta": 0.5,
  "score_velocity_1d": 0.5,
  "score_velocity_3d": 0.9,
  "score_velocity_5d": null,
  "score_velocity_10d": null,
  "trigger_distance_pct": -0.4,
  "sector_rank": 5,
  "sector_rank_previous": 9,
  "sector_rank_delta": 4,
  "market_regime": "GREEN",
  "earnings_risk": "LOW",
  "liquidity_risk": false,
  "required_feature_coverage": 1.0,
  "freshness": "FRESH",
  "data_quality_label": "HIGH",
  "blockers": [],
  "latest_reason": "technical_score threshold crossed",
  "reason_codes": ["THRESHOLD_CROSSED"],
  "warning_flags": [],
  "snapshot_id": 999,
  "previous_snapshot_id": 988,
  "source_run_id": 77,
  "source_event_key": "...",
  "evaluation_run_id": 50,
  "source_url": "/setup-lifecycle/ticker/FIX#signal-change-333"
}
```

List envelope: `items`, `total`, `page_item_count`, `limit`, `cursor`, `next_cursor`, `sort`, `direction`, `selected_date`, and `summary`. `summary` is computed from the full filtered scope.

When `transition=NO_MATERIAL_CHANGE` is explicitly requested for a selected date, rows use `source_type=SNAPSHOT_OBSERVATION`, point to the canonical snapshot/ticker timeline, and are included only when neither a material signal event nor a current lifecycle transition exists for that snapshot. They are not mixed into the default changes stream.

## Alert item (v2)

```json
{
  "id": 1001,
  "ticker": "FIX",
  "effective_date": "2026-08-10",
  "alert_type": "NEW_TRIGGER",
  "severity": "ACTIONABLE",
  "review_status": "UNREAD",
  "source_type": "LIFECYCLE_EVENT",
  "lifecycle_state": "TRIGGERED",
  "actionability": "ACTIONABLE",
  "confidence": 86,
  "confidence_label": "HIGH",
  "reason_codes": ["NEW_TRIGGER_ALERT"],
  "blockers": [],
  "evidence": {},
  "episode_id": 123,
  "lifecycle_event_id": 1234,
  "signal_change_event_id": null,
  "source_event_key": "...",
  "evaluation_run_id": 50,
  "source_url": "/api/setup-lifecycle/episodes/123"
}
```

Alert list envelope contains full-scope `summary` counts. Mutations return the complete updated Alert DTO or, at minimum, `id` and `review_status` with stable field names.

For compatibility, mutations and list items may temporarily include deprecated alias `status` equal to `review_status`; templates and new consumers use `review_status` exclusively.

## Validation

Invalid dates, enums, thresholds, cursors and sorts return HTTP 400 with stable SLSE error codes. Missing source records return 404. JSON preserves nulls. List/export endpoints accept the same filter and sort parameters; CSV is a projection of the same DTO set and declares a versioned schema ID.
