# CI443 fixture connection review

[CI443](https://github.com/ivobog/swing-lens/actions/runs/42367293297), source
`e992623c9dfe53d88110d0cd8da4e9bdabf5a9f6`, passed the 100-company case:
13 completed stages in 758.914436 seconds, zero retries, all original stage
budgets met. The following 88-company case failed the unchanged transaction
observer on backend 2906: blank application name, `idle`, `COMMIT`, reported
transaction age 147.522774 seconds. Retained records identify this backend during
fixture seeding. Worker ranking progress committed 37 milliseconds before the
failure; this evidence does not establish a worker progress stall.

A disposable PostgreSQL 18.3 probe reproduced the same signature with the original
120-second SQL predicate during cache catchup: age 125.191952 seconds, null
backend xid/xmin, committed data intact, transaction start null before and after.
CI used PostgreSQL 18.6; its exact interrupt was not traced. PostgreSQL's
[bug discussion](https://www.postgresql.org/message-id/CAHGQGwEQWk6J%2BvqQnnVFFngtMdMuw-AwuEFnq=u3Wb2STXu3TA@mail.gmail.com)
describes this internal catchup transaction reporting mechanism.

The helper now disposes its unused seed connections after its existing Session
closes and commits. Later assertions reconnect through the same engine. Active
clients and independent worker pools remain usable. Every original test function,
seeding statement, assertion, observer, deadline, financial oracle and policy
object is unchanged. Only the helper's completed resource lifetime changes.

The additive PostgreSQL regression executes the real frozen CERI writer and
normalizer, checks the idle backend closes, reads committed input from another
connection, verifies an existing active client survives, and reconnects for later
assertions. It fails on the original helper and passes on the repair. The original
transaction-policy control still detects unrelated, stalled and cancelled
transactions. Source review preserves all original function ASTs except the
helper's appended disposal call. This is a bounded fixture review, not a new
certification policy or a release certificate.

The reviewed module digest changes from
`d6aeda9ffceae7d7d76cfba398bb30f4f581a2716a94b76da0b15a7c5c6de64c`
to `2321f549e9f7cc6b076241e5dd33b86b31cc992635198fe8622c0c68ff6849f4`.
Historical review bindings retain their original digests. Current source and
dependency checks continue to reject stale evidence. A04 requires new complete
whole-source evidence; no CI443 A04/core pass or conditional deferral is claimed.

The exact review-document path and
`tests/integration/test_pipeline_authority_certification_postgresql.py` belong to
the existing `NATIVE_PIPELINE_CERTIFICATION` contract. Their reviewed impact
associations are exact paths. New neighboring documents and integration modules
remain unknown and retain the broader testing obligation. The first plan on
`001c294` exposed the missing associations; its CI run was stopped before the
ineligible plan could complete expensive prerequisites. No unknown-impact guard
or approval condition is relaxed by recording these two reviewed associations.

The native seed-connection regression is registered in this existing contract
and selected in the Python lane when the fixture or its authority test changes.
Its explicit engine fixture creates and cleans up a real disposable PostgreSQL
engine. The original 15 A04 prerequisites and conditional 14-case proof
acquisition are unchanged; this additive control supplies early feedback.

Retained authenticated failure archive SHA256:
`aa3ccbe02f138f8fae469a50ed602e0d73d7710dae04016987343e6d229596f7`.
Extracted native input SHA256:
`4532d040d55ed92e7317d2416cc19e8e3d32962eb8675590df5be018524acd25`.
Local receipts, source review, exact baseline restoration and catchup diagnostics
remain in `output/integration_recovery_20261106/` and the existing focused-test
output directory. No normal database or historical financial state was changed.
