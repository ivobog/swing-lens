# Retrying a Gateway failure before fetch-plan admission

A pipeline can complete validation and fundamental scoring, then fail its first
Gateway connection before a market-data plan exists. **Retry missing market
data** supports that checkpoint as a first admission. It reuses the completed
fundamentals, original ticker scope, execution configuration and market cutoff.

The first-admission path requires retained evidence of
`IB_GATEWAY_API_UNREACHABLE` before acquisition, zero planned/executed requests,
no retained fetch authority, and no prior fetch run, `IB_FETCH` job or child
`ib-price-acquisition` scope for the upload/pipeline. Existing fundamental
evidence and context are validated before skipping their completed stage.

The current completed market session must still match the frozen session. After
planning, the new plan's upload, requested ticker population, per-item freshness
date, request end date and current-ended expiry are checked before admission.
The durable absence checks are repeated immediately before creating authority.
No prior acquisition checkpoint is replaced through this path.

The pipeline retains an owner/session-bound `market_data_resume_checkpoint`
with `fetch_plan_state=NOT_ADMITTED` and an `initial_admission` contract. A
planning error before admission can therefore be retried even after the Gateway
health projection changes to ready. That contract cannot waive prior acquisition
facts or a changed scope, context or session. A `first_plan_admitted` marker
prevents treating a lost admitted plan as a fresh acquisition.

An existing plan uses the `RETAINED` path. Its required shape, upload and ticker
scope are checked; its exact frozen contents are reused. Older optional request
fields are not silently reinterpreted under a newer request policy.

Missing or contradictory acquisition evidence remains an error. A changed
market session requires a new admission rather than silently updating the old
run's cutoff. No schema migration is required.
