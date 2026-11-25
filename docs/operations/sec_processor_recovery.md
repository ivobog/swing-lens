# Exact SEC processor recovery

This runbook covers an owner-authorized reversal of one recorded SEC processor promotion on the canonical Windows launcher. A recovery commit leaves the predecessor active in PostgreSQL. The launcher starts that qualified predecessor afterward, and the operator must verify its runtime readiness.

## Record the promotion

Qualify both releases with the native launcher and verify their source trees, processor signatures and runtime profiles. Bind SEC certification to the exact approved upload and scope. Preserve the passing predecessor qualification and the approved launch paths. Drain and stop the relevant runtime through the canonical launcher, and reconcile outstanding work and leases.

Use the candidate release's existing promotion command with --rollback-run-id set to its exact certified upload ID. The option records a promotion ID, the candidate Git revision, both processor signatures, certification digests and signature-bound readiness seals in the same transaction as activation. A promotion without a recoverable record remains subject to the ordinary retired-processor rule.

## Reverse the exact promotion

Read the durable receipt with python -m scripts.manage_sec_processor transition-status --candidate SIGNATURE. Obtain a separate approval from the owner. Sign the JSON with the swinglens-sec-processor-rollback OpenSSH namespace. The existing administrator-protected allowed-signers file must have an explicitly approved entry for that namespace and the existing owner principal. The financial acquisition namespace has separate authority.

Bind the approval to the promotion ID, expected signatures, candidate and predecessor SHA, exact absolute checkout roots, both native runtime fingerprints, the predecessor launcher-qualification file SHA-256, the operator SID, database fingerprint and role, Alembic head, recovery reason and a current UTC window of at most thirty minutes. The fingerprint command of the canonical lifecycle probe supplies each runtime fingerprint. Keep the predecessor checkout and its verified runtime available.

With both runtimes stopped, run python -m scripts.ops.sec_processor_rollback --approval APPROVAL.json --signature APPROVAL.json.sig --qualification QUALIFICATION.json from the recorded candidate checkout. The operator holds both launcher locks through commit, checks physical runtime absence and refuses active database jobs, leases or recent worker registrations. Queued work stays durable and requires reconciliation before resuming claims.

On ROLLBACK_COMMITTED, restore the qualified predecessor's launcher pins and start it through the canonical launcher. Verify the exact running Git SHA, HTTP core readiness, worker and supervisor, database and Alembic identities, the only ACTIVE SEC signature and the exact predecessor cohort readiness before resuming work.

When output is lost, read transition-status for the candidate and compare the recorded rollback approval digest and active signature. A committed receipt is the authoritative recovery result. If output is missing before a commit, recheck runtime absence, locks and the signed window before any retry. Repeating a committed operation is rejected. An expired approval requires fresh authorization for the same still-current transition.
