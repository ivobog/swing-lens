# Market-data recovery and explicit partial results

Open the blocked pipeline's progress page. The market-data section records the missing tickers, the required session, and the provider's response. IB Gateway is sufficient for these actions; TWS is not required.

## Continue with reported company data gaps

New normal pipeline admissions freeze `CONTINUE_KNOWN_DATA_GAPS_V1` separately from the IB connectivity policy. A recognized, instrument-specific no-data response leaves that company unavailable and its dependent outputs incomplete, while other companies continue. Every requested company remains in the admitted population; unavailable technical inputs produce explicit null/ineligible diagnostic rows. The final run is `PARTIAL`, not a complete-coverage success. Identity ambiguity, invalid responses, expired request authority, systemic provider failures and required benchmark failures still block.

Daily response admission `ib-daily-gap-aware-v4` retains individually validated, completed, in-scope bars even when required historical sessions are missing. A partial response is recorded as `PARTIAL` with the exact missing sessions. Current-session availability does not establish complete historical coverage: calculations requiring a complete window remain incomplete or ineligible until their inputs are sufficient. Wrong identities, invalid OHLCV, duplicate dates and unauthorized future observations retain their strict handling.

Each V4 response seals an append-only acceptance receipt with exact accepted bar versions, owner, contract, scope, cutoff and missing sessions. Cache changes, receipt and item outcome are atomic. Frozen readers accept only the exact authorized versions and combine original and repair receipts; a later repair cannot replace an earlier result's recorded source evidence.

Previously admitted pipelines without the new marker retain their original strict policy and frozen V1/V2/V3 fetch plans. On a market-data-blocked pipeline, **Start new run with data gaps** explicitly creates an isolated new upload and pipeline for the same complete company population. It admits a new cutoff and configuration, retains source-row addresses and fingerprints, and preserves the original run. Repeated requests open the already-created recovery run.

## Repair recorded history gaps

On a terminal V4 run with historical gaps, **Review missing history** lists the missing company sessions and price series. **Fetch missing days** queues one bounded child acquisition under the original context, configuration and exact contract. Its reviewed manifest must still match when submitted. The planner uses acceptance receipts rather than the latest cached date, so a current November 9 bar cannot hide a missing November 4 session.

TRADES can request an explicit older missing interval. IB's current-ended ADJUSTED_LAST request remains bounded by the original reviewed completed session and its expiry; after that session expires, admit a new run rather than changing the old cutoff. Empty responses cannot create acceptance receipts or manufacture readiness. Repairs retain the old failure/partial items and existing calculation evidence. They do not automatically retry forever or recalculate completed results.

The receipt table is added by migration `0095_ib_bar_admission_receipts`. Run normal guarded migrations before starting this release. Existing bars and old admissions require no backfill or rewriting.

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
- `POST /runs/{run_id}/pipeline/{pipeline_id}/recover-with-gaps`: admit the complete retained population in an isolated new run with the current data-gap policy.
- `GET /runs/{run_id}/pipeline/{pipeline_id}/history-gaps`: review receipt-backed outstanding sessions.
- `POST /runs/{run_id}/pipeline/{pipeline_id}/repair-history-gaps`, form `manifest_fingerprint`: queue the exact reviewed bounded repair.

An IB support packet can use the contract identity, required session, and exact callback messages from the investigation. Sending a support message remains an operator action.
