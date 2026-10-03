# CERI P2 preserved baseline state

Captured before any P2 code change on 2026-10-03 (Europe/Zurich).

## Repository

- Branch: `codex/ceri-authority-remediation`
- HEAD: `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`
- Tracked P0/P1 binary-capable diff stored as Git blob: `34da38768608d55144e8825af49bb7d3865ca222`
- SHA-256 of that exact `git diff --binary` stream: `ecdb90d79455e53a8c57dd4b847d9f99e65ef97dbeac49d1af37e41164b25af4`
- Recovery/read command: `git cat-file blob 34da38768608d55144e8825af49bb7d3865ca222`
- No reset, clean, checkout, rebase, commit, or push was performed.

### Exact tracked status

```text
 M app/services/canonical_evidence.py
 M app/services/ceri/batched_job_handlers.py
 M app/services/ceri/deployment_identity.py
 M app/services/source_mutation_authority.py
 M tests/ceri/test_batched_workflow_v2.py
 M tests/integration/test_ceri_batched_workflow_v2.py
 M tests/integration/test_ceri_systemic_remediation.py
 M tests/integration/test_t14d_source_bundle_postgresql.py
 M tests/test_canonical_evidence_serializer.py
 M tests/test_source_mutation_authority.py
```

### Exact tracked diff stat

```text
 app/services/canonical_evidence.py                 |   36 +-
 app/services/ceri/batched_job_handlers.py          |   83 +-
 app/services/ceri/deployment_identity.py           |    8 +-
 app/services/source_mutation_authority.py          | 1051 ++++++++++++++++++--
 tests/ceri/test_batched_workflow_v2.py             |   37 +
 tests/integration/test_ceri_batched_workflow_v2.py |   67 +-
 tests/integration/test_ceri_systemic_remediation.py|   12 +-
 tests/integration/test_t14d_source_bundle_postgresql.py | 76 +-
 tests/test_canonical_evidence_serializer.py        |   34 +
 tests/test_source_mutation_authority.py            |  132 ++-
 10 files changed, 1421 insertions(+), 115 deletions(-)
```

### Preserved untracked P0/P1 files

```text
artifacts/forensics/ceri_source_authority_p0_report.md
artifacts/forensics/ceri_writer_manifest_p1_report.md
artifacts/forensics/run11_ceri_performance_forensics.md
artifacts/forensics/run11_ceri_source_profile_job287.json
artifacts/forensics/run11_ceri_source_remediation_job287.json
artifacts/forensics/run11_ceri_source_remediation_job288.json
artifacts/forensics/run11_ceri_source_timing_job287.json
artifacts/forensics/run11_ceri_source_timing_job288.json
artifacts/forensics/run13_p1_controlled_after_job351.json
artifacts/forensics/run13_p1_controlled_after_job352.json
artifacts/forensics/run13_p1_controlled_before_job351.json
artifacts/forensics/run13_p1_controlled_before_job352.json
artifacts/forensics/run13_p1_writer_after_rowcache_job351_A.json
artifacts/forensics/run13_p1_writer_after_subtree_job351_A.json
artifacts/forensics/run13_p1_writer_baseline_job351_A.json
artifacts/forensics/run13_p1_writer_baseline_unprofiled_job351_A.json
artifacts/forensics/run13_p1_writer_digest_parity_job351_A.json
artifacts/forensics/run13_p1_writer_final_profile_job351_A.json
artifacts/forensics/run13_p1_writer_final_unprofiled_job351_A.json
scripts/profile_ceri_source_bundle.py
scripts/profile_ceri_writer_manifest.py
```

The original files remain individually present. The P0/P1 report hashes at this boundary are:

- P0 report: `327b2e5ab4f25935b60df445d4f23b7f9ee3ae8d2a7ec25be2024fa473f2c9a8`
- P1 report: `33375a7039a55d74eb72f3a670aa063a4c85e702023be56292ad17bb0bb28d28`
- P0 profiler: `b4ee638304bf55f8f2ff909258d089fe0b5b28e414a417e1daa598e60a098d52`
- P1 profiler: `284998e6c0346ed30fc484be3cee7e0dce36f9ac11f87070fd0ea49a3fb05345`

## P0/P1 ownership map

P0 primarily changed `source_mutation_authority.py`, `deployment_identity.py`, `batched_job_handlers.py`, the CERI workflow/systemic/PostgreSQL authority tests, and `profile_ceri_source_bundle.py`.

P1 primarily changed `canonical_evidence.py`, `source_mutation_authority.py`, `batched_job_handlers.py`, canonical/writer/PostgreSQL tests, and `profile_ceri_writer_manifest.py`. Shared files contain layered P0 and P1 work and must not be reset to HEAD.

## Runtime

- Python: 3.12.2
- PostgreSQL: 18.3 on Windows x86-64
- Schema head: `0089_pipeline_execution_authority`
- Default isolation: `READ COMMITTED`
- Active worker: one logical `local-worker-1`, queues `interactive,broker,background`, runtime instance `452bcf0c07544c1296061c5e1e069814`
- Effective CERI batch settings: provider 25; normalization 50; feature 50; checkpoint interval 5
- `.env` SHA-256 (contents not recorded): `a0e453c9eb7ad765945ccbeb3843da9586295f735271175e6fee41d28d167a6f4`

## Forensic run boundary

Runs 11, 13, and 14 are read-only evidence for P2. P2 must not recover, resume, repair, rewrite, or change their statuses, manifests, checkpoints, or artifacts.
