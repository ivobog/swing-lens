# CERI P3 preserved baseline state

Captured before any P3 code change on 2026-10-03 (Europe/Zurich).

## Repository

- Branch: `codex/ceri-authority-remediation`
- HEAD: `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`
- Complete tracked P0/P1/P2 binary diff stored as Git blob: `e23fb5491cefa1c4fdeeed6a03123671b5cabb1c`
- SHA-256 of the current `git diff --binary` stream: `de4c766d9f08699c337b5063577bb0564660861502bc9d449296239b26d3c63b`
- Complete `git status --porcelain=v1 -uall` stored as Git blob: `a3905a0ec9aad6164ed8fc8b3ddf60b51979d95a`
- Complete `git diff --stat` stored as Git blob: `126d0a540fa6177261a55b5eca4c81c129eddf9c`
- Recovery/read command for any saved object: `git cat-file blob <blob-id>`
- No reset, clean, checkout, rebase, squash, commit, or push was performed.

The tracked boundary contains 15 modified files, 1,689 insertions, and 125 deletions. Untracked P0/P1/P2 reports, raw profiles, benchmark children, and diagnostic scripts remain present and are included in the saved full-status object.

## Layer ownership

- P0: source authority, fail-closed invalidation, retry chunking, bounded audit, deployment identity and associated authority/systemic/PostgreSQL tests; primary implementation in `source_mutation_authority.py`, `deployment_identity.py`, and `batched_job_handlers.py`.
- P1: retained-source/canonical subtree reuse, writer profiling/parity, and canonical serializer changes/tests; primary implementation in `canonical_evidence.py`, `source_mutation_authority.py`, and writer handlers.
- P2: qualified `FOR SHARE` source locks, raw membership shared lock, job-type-constrained worker claims, PostgreSQL ownership/lock tests, concurrency diagnostics, and `ceri_locking_concurrency_p2_report.md`.
- Shared files contain layered changes and must not be reset to HEAD.

Certified report hashes at this boundary:

- P0: `327b2e5ab4f25935b60df445d4f23b7f9ee3ae8d2a7ec25be2024fa473f2c9a8`
- P1: `33375a7039a55d74eb72f3a670aa063a4c85e702023be56292ad17bb0bb28d28`
- P2: `24f5a938abacea6155983d162f0ca5aa7688f74dbc05048a19d436a90286f33a`

## Runtime

- Python: 3.12.2
- PostgreSQL: 18.3 on Windows x86-64
- Schema: `0089_pipeline_execution_authority` (head)
- Default database isolation: `READ COMMITTED`
- Active worker at capture: `local-worker-1`, instance `e1e87190b9a14feeba72b5f318426325`, queues `interactive,broker,background`, one unconstrained normal worker
- Second feature worker: disabled/not present
- `.env` SHA-256 (contents not recorded): `a0e453c9eb7ad765945cceb3843da9586295f735271175e6fee41d28d167a6f4`

## Immutable evidence boundary

Runs 11, 13, and 14 remain read-only forensic evidence. P3 must not recover, resume, repair, mutate, or rewrite their statuses, checkpoints, feature evidence, or source manifests. Every P3 production-sized diagnostic must use independent Sessions and roll back unless the production gate explicitly authorizes a new pipeline.
