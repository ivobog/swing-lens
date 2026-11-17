Pipeline scheduler repair — pipelines 59 and 60

The worker could repeatedly claim normalization before the provider stage it
required, leaving ready ingestion and a newer pipeline's SEC preparation starved.
This repair preserves the October 12 ordering correction and adds a database
eligibility filter for closed CERI stage barriers, including queued jobs with old
priorities. Unsuccessful terminal upstream stages still reach their deterministic
failure handler. Python classification and SQL queue filtering both classify SEC
readiness repair as interactive, subject to the existing four-claim fairness cap.

Claims and lease heartbeats no longer increment useful progress. A separate
execution-attempt baseline protects newly claimed jobs from an immediate stall
after a long queue wait. A running attempt that never produces its first useful
checkpoint remains subject to its stage timeout. Dependency deferral counts and
unchanged-wait timestamps remain visible; repeated waits emit bounded warnings.
Pipeline status prioritizes the running child and exposes prerequisite waits
without representing worker activity as processing progress.

Run `python -m scripts.qa.qualify_pipeline_scheduler --output <empty-directory>`
with local disposable PostgreSQL credentials configured through
`SWINGLENS_TEST_POSTGRES_ADMIN_URL`. The native fixture creates and drops only
safely named test databases and verifies their identities before migration. Any
selected test skip, error or failure blocks a passing receipt. The receipt binds
the exact scheduler, status, qualification and regression-test sources (normalizing
CRLF to LF). `python -m scripts.qa.verify_pipeline_scheduler_qualification --receipt
<qualification.json>` rejects a different revision, omitted fix or changed file.
The live launch wrapper must verify this receipt before start or restart.

Coverage includes the retained October regression; one general worker with an
aged workflow and a new SEC child; 150 and 466 company plans; old normalization
priorities; missing, unfinished, PARTIAL and unsuccessful prerequisites; payload
workflow fallback; malformed expected-count input; fairness; cancelled parent
authority; and first-checkpoint/after-deferral watchdog behavior. Broader native
CERI parity and worker ownership regressions remain selected.

This is scheduler and CERI regression qualification. The scale test advances
controlled terminal markers and invokes the native barriers; it does not claim
to generate 466 companies' financial/provider outputs or certify the entire
financial pipeline. Production pipelines 59 and 60 must remain cancelled during
activation and verification. Operational receipts live in the workspace's
matching remediation directory.
