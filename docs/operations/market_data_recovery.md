# Market-data recovery and explicit partial results

Open the blocked pipeline's progress page. The market-data section records the missing tickers, the required session, and the provider's response. IB Gateway is sufficient for these actions; TWS is not required.

## Retry the missing data

Use **Retry missing market data** after addressing the provider problem. The pipeline reuses its frozen market context, scope, configuration, and completed fundamentals. It validates the retained fundamentals before continuing. It creates a new fetch attempt for failed ticker/feed items from the retained plan, preserving the previous failure records and completed fetches.

This action requires the original daily-data request to remain valid for its reviewed session. A changed trading session, changed fundamentals, or unavailable retained evidence requires a new admission; recovery never substitutes a current cutoff into the old run.

The exact IB error `BEST queries are not supported for this contract` is a non-retryable, instrument-specific `PROVIDER_REJECTED` response. It does not establish delisting or inactivity. Pacing and connection failures retain their existing bounded retry policy. Error classification improves handling but cannot restore unavailable provider data.

## Process the ready subset

Use **Start partial run — exclude …** to explicitly accept the displayed market-data exclusions. The server validates the selected exclusions against the source pipeline's current readiness evidence and rejects stale selections, unresolved instrument identities, and an empty ready population.

The action creates an isolated derived upload and a new pipeline with an immutable subset scope linked to the source scope. Source-row addresses, content fingerprint, exclusion reasons, and original requested tickers are retained. The original pipeline, upload, fundamentals, and failure evidence remain available for later recovery. Repeated submissions for the same source open the existing partial pipeline.

Excluded tickers are outside fundamental scoring, technical scoring, combined results, ranking, and the downstream research population. They are never scored using stale bars. The new run is marked `PARTIAL` even when every admitted ticker succeeds. Progress and run detail show admitted versus originally requested tickers and list exclusions; the outcome summary records the coverage gap.

HTTP actions:

- `POST /runs/{run_id}/pipeline/{pipeline_id}/resume`: continue the existing checkpoint.
- `POST /runs/{run_id}/pipeline/{pipeline_id}/partial`, form `excluded_tickers=ROKU`: explicitly admit the ready subset and redirect to its derived upload/pipeline.

An IB support packet can use the contract identity, required session, and exact callback messages from the investigation. Sending a support message remains an operator action.
