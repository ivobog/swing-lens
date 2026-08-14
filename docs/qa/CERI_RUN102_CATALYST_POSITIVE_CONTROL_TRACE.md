# CERI Run 102 Catalyst Positive-Control Trace

## Positive fixture

| Stage | Evidence/result |
|---|---|
| Provider | EODHD article `scheduled-storage`; related symbols include `TEST.US` |
| Raw event | regulatory decision scheduled for `2026-09-15`; materiality `7` |
| Source projection | retains expected date, issuer relevance `true`, and `PROVIDER_RELATED_TICKER_MATCH` |
| Normalized | category `REGULATORY`; status `SCHEDULED`; issuer relevant; expected date retained |
| Eligibility | selected, pending/current, material, exact structured issuer match |
| Feature | Opportunity catalyst available; binary Event Risk eligible and nonzero |
| Component | selected evidence can feed Catalyst and Event Risk components |
| Price Response parent | eligible after structured issuer and review-state checks |

## Negative controls

- `relatedTickers=[OTHER.US]` yields `ISSUER_RELEVANCE_MISMATCH` and is rejected.
- Query ticker alone does not establish issuer relevance.
- Provider structured exclusion is retained and rejects the row.
- Completed/outcome-known events are not pending binary risk.
- Review-rejected or issuer-irrelevant events cannot become Price Response parents.

## Run 102 root cause

The provider adapter already derived structured issuer relevance, but the
licensed persistence projection omitted that boolean, its reason, and the
expected date. Normalized catalyst revisions therefore became issuer-unverified
and unavailable. The projection now preserves those licensed structured fields;
random absence is not used as proof of quality.
