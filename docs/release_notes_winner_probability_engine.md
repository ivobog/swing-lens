# Outcome-Calibrated Winner Probability Engine Release Notes

## Phase 11 Controlled Rollout

Phase 11 keeps OWPE as a local research evidence layer and adds the final rollout
guardrails for capture, historical replay, maturation, evidence review, and governance.

Implemented controls:

- OWPE remains disabled by default through `WINNER_PROBABILITY_ENABLED=false`,
  `WINNER_PROBABILITY_CAPTURE_IN_PIPELINE=false`, and
  `WINNER_PROBABILITY_ADMIN_ENABLED=false`.
- Manual capture and outcome maturation continue to run through durable background jobs
  with heartbeat and fencing protection.
- The pipeline contains `CAPTURING_WINNER_PREDICTIONS` as a nonfatal final step, but
  pipeline capture remains guarded by the explicit capture flag.
- Historical backfill uses `WINNER_HISTORICAL_BACKFILL` and only plans completed runs
  whose point-in-time lineage is explicitly trusted.
- Reconstructed snapshots are marked with `reconstruction_method` and quality flags, and
  are excluded from production training by default.
- Reconstructed probability records use `AS_OF_REPLAY`, not `DECISION_TIME`, so replayed
  history is not presented as something that was actually displayed at the historical
  decision time.
- Decision-time estimates remain immutable; latest re-scores, model promotion,
  retirement, revised outcomes, and backfilled history do not mutate them.
- Similarity and shadow-model outputs remain supporting evidence only. Activation remains
  behind explicit Phase 9 promotion gates.

Validation included in this release:

- configuration and feature-schema validation,
- schema and append-only identity tests,
- capture idempotency and point-in-time snapshot hashing,
- pending outcome materialization,
- outcome maturation and revision lineage,
- cohort estimates, exact evidence manifests, and reproduction,
- run/ticker APIs, filters, exports, Outcome Explorer, operations, and model dashboards,
- calibration, drift, model registry, artifact validation, promotion, and retirement,
- similarity nearest-neighbor safety and shadow-model walk-forward validation,
- Phase 11 rollout gate and historical backfill acceptance fixtures.

Explicit rollout posture:

- Production pipeline capture must stay disabled until selected completed runs have been
  captured and their estimates reproduce from immutable evidence manifests.
- Historical replay must be labeled as reconstructed history and remain excluded from
  production training unless a future versioned configuration explicitly approves it.
- Backfill and administrative mutation routes remain local-only under existing app
  controls.
- No OWPE path may place broker orders or expose a trade button.

Deferred or waived for this local release:

- Automated recurring scheduling of daily outcome maturation is not enabled by this
  code change; the durable maturation job can be triggered manually and scheduled later.
- Saved filters and automated model activation remain deferred.
- External catalyst/news data and intraday same-bar ordering remain outside MVP scope.
