# Setup Lifecycle systemic-remediation preserved baseline

Captured before lifecycle changes on 2026-10-03 (Europe/Zurich).

## Repository

- Branch: `codex/ceri-authority-remediation`
- HEAD: `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`
- Schema head: `0089_pipeline_execution_authority`
- Python: `3.12.2`
- Complete tracked P0/P1/P2/P3 binary diff stored as Git blob: `711c13101067448faa63ef5178d4456c8b487a0b`
- Complete `git status --porcelain=v1 -uall` stored as Git blob: `2d6fd226edd37016def1959518468b684e8347d0`
- Complete `git diff --stat` stored as Git blob: `484eeb60ec41e50ac2504c8b804d8815fc978520`
- `.env` SHA-256 (contents not recorded): `a0e453c9eb7ad765945cceb3843da9586295f735271175e6fee41d28d167a6f4`
- Runtime topology at capture: no worker heartbeat newer than 30 seconds; the configured P2 feature-only second worker remained disabled.
- No reset, clean, checkout-over, rebase, squash, commit, or push was performed.

The inherited tracked boundary contains 15 modified files, 2,319 insertions, and 127 deletions. Those files belong to certified CERI P0–P3 and must not be discarded. Lifecycle implementation files under `app/services/setup_lifecycle/` were clean at this boundary.

## Layer ownership

- P0–P3 inherited production files: `app/services/background_job_service.py`, `background_worker.py`, `canonical_evidence.py`, `ceri/batched_job_handlers.py`, `ceri/deployment_identity.py`, `ceri/feature_rebuild_service.py`, `source_mutation_authority.py`, and `app/worker.py`.
- P0–P3 inherited tests: the already-modified CERI, writer, worker, and source-authority tests shown in the captured status object.
- New lifecycle work is expected primarily under `app/services/setup_lifecycle/`, lifecycle-focused tests, and rollback-only forensic scripts/artifacts.

## Immutable historical boundary

Runs 11, 13, 14, and 15 are forensic evidence and must not be resumed, repaired, or rewritten. The pre-change direct run/CERI hash artifact is `setup_lifecycle_p4_runs_11_13_14_15_pre.json`; its combined table hash is `d605175d57dcbc1a6b8c431e2b1692063147f9e68bb333e5c23b19d52d7774c6`.

Run 15 remains `FAILED` at `EVALUATING_SETUP_LIFECYCLES` with `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH`, while its retained result records durable CERI `CERTIFIED`.
