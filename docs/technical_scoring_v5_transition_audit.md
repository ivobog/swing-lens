# Technical Scoring v5 classification/action transition audit

- Exact classification change rate: 61.73%
- Exact action wording change rate: 97.95%
- Canonical decision-bucket action change rate: 25.89%

The gap between wording changes and decision-bucket changes is terminology churn; the
remaining 25.89% is a genuine change among Entry, Wait/Confirm,
Avoid and No Trade semantics. Every exact transition includes count, percentage,
5d/10d return and MFE/MAE in the two transition CSVs. The full exact matrices are
retained; canonical buckets are an audit aid, not a rewrite of either engine.
