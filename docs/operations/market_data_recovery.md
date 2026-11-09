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

## Symbol changes and Pipeline 55

Reviewed symbol changes live in `config/instrument_symbol_changes.json`. Each entry binds the
previous and current symbols to one IB common-stock contract ID, listing exchange, currency,
security type, effective date, and public source. An exchange transfer or ticker rename does
not establish a terminal lifecycle state.

The initial entry records **TCBI → TXCP, effective November 9, 2026**, using IB contract
**21521400** on **TXSE**. Qualification uses that contract ID and rejects a different security,
including the preferred shares. Before the effective date the normal symbol lookup applies.
After the change, the resolver requalifies obsolete cached identities and keeps the uploaded
ticker separate from the qualified provider symbol.

For a frozen pre-change session, the fetch records the exact mapping evidence and current
provider identity, then retains the original ticker on admitted historical bars. The original
request dates, scope, contract fingerprint, and acquisition plan remain authoritative. A pinned
contract is never replaced by a current cache lookup. Unrelated provider symbols, missing
mapping evidence, and source-symbol requests for sessions on or after the change are rejected.
Use the current symbol on new uploads after its effective date.

Deferred contracts are qualified once per ticker before the historical-request pass. An
unresolved or ambiguous identity stops that pass with `IB_CONTRACT_PREFLIGHT_BLOCKED`, retains
failed feed items with zero historical attempts, and blocks the pipeline at market-data fetching.
It does not consume the historical pacing budget or silently reduce the requested population.

Pipeline 55 retains upload 54, the November 6 session, and completed fundamentals. After deploying
the symbol-change support, **Retry missing market data** creates a fresh attempt for its two
failed TCBI feeds. The prior fetch (38), failures, upload, and plan stay unchanged. The support
requires no schema migration and does not automatically resume a live pipeline.

The original `ADJUSTED_LAST` request is current-ended. If a later trading session has completed,
its reviewed-session expiry still blocks execution. Use a new admission with the current symbol
and current source data instead of changing the frozen cutoff or weakening response admission.

If the identity is resolved but historical data remains unavailable, the resulting explicit
data-availability disposition can support the existing reviewed partial-run action. An `UNKNOWN`
identity continues to prohibit that action. Keep symbol-change entries as reviewed history;
changing or removing an entry invalidates retained mapping evidence that no longer matches it.
