# CERI Changes and Alerts UI Semantics

## Changes

The primary card now renders ticker, humanized change, semantic summary, coverage/confidence or canonical-event details, importance, signal class, and time. Examples produced by the DTO include:

- `AEIS · Became rated` / `Unrated -> 9.42 Positive` / `Coverage 0% -> 70%` / `Confidence Insufficient -> Normal`
- accepted event: category, subtype, subject, lifecycle, materiality, confidence, and expected date, without a previous/current row
- revision: selected 7/30/90d metric/period percentage changes, breadth, acceleration, and the applicable configured threshold rather than snapshot foreign keys

Snapshot IDs, revision IDs, change IDs, dedup keys, and hashes are inside **Technical details**. The template has no primary `From <snapshot_id> to <snapshot_id>` or `N/A -> N/A` rendering path.

The page uses the central eight-group mapping and shows comparable context such as `Comparing Run 102 -> Run 104` plus the excluded non-comparable count.

## Alerts

Primary columns are Ticker, Alert, Importance, Change, Risk, Confidence, When, Status, and Action. Raw evidence JSON and identities are in Technical details. `ACKNOWLEDGED` and `DISMISSED` remain supported; `INVALIDATED` rows show the reason and no action buttons.

## Filters and pagination

Implemented filters cover run pair, ticker, group, change type, importance, signal class, minimum delta, event category, alert status, and current/historical scope. Explicit forensic toggles expose non-comparable and ineligible rows. Pagination uses the current request URL with only the offset replaced, preserving every active filter.
