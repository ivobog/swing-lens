# Release certificate hash reconciliation

The historical Phase-5/6/7 certificates remain byte-for-byte in
`docs/remediation/calculation-lineage`. They are historical, immutable
certificates; this report and the machine-readable bridge are a **current
derivative revalidation**, not edits to their claims.

## Canonical committed-content contract

`git-committed-source-freeze-v2` resolves a commit SHA, enumerates the ordered
`app/`, `alembic/`, `config/`, `scripts/`, selected root files and `tests/`
paths in that commit, records each Git blob ID and mode, then SHA-256 hashes a
canonical JSON envelope containing the commit, ordered path set, blob IDs,
schema version and tool version. It never reads checkout bytes. The full
terminal-main manifest is `RELEASE_source_freeze_v2.json`; its fingerprint is
`c92d0c976d729869e4fee78cf221212fb38d723f024ea0702c5eeb4e6eb665b9`.
The Phase-5 commit fingerprint is
`cc83628aa45d60cd1ce75c11c41a8b59f66f0467a658dd4067f67c79f0b0b8ba`;
the T15E committed artifact/source tree is
`ef5739e5f3ee9039fa089aadd9e56122bce03fe53cf9d0b6cfd7b9988b59ab4a`.

## Historical bridge and nonportable evidence

T15E records `c562f4b` as HEAD, but its 668 implementation and 450 test paths
match the subsequent `2bafa33` certificate commit, not the parent tree. T16E
similarly records the `5a2f92b` precommit HEAD while its 674 implementation
and 460 test paths match terminal `d5066e9`. Their v1 source hashes used
working-tree bytes and cannot be recomputed reliably from a clean Git tree.
They are explicitly retained as `LEGACY_WORKTREE_HASH`; their old numbers are
not silently replaced or treated as v2 fingerprints. The derivative binds the
actual certificate artifact commits and their exact committed path/blob sets,
then reruns the current semantic tests. This proves the current terminal tree
inherits the certified invariants, while preserving the historical hash gap.

Individual old artifact pins are narrower and independently reproducible:
the T16E Phase-6 certificate pin
`460456f0195433f8e4a3b9c8bb205e7087c6ed1a069d6673689c2ebcac23dac2`
is exactly the `2bafa33` Git blob after `core.autocrlf=true` checkout
filtering. Its committed blob SHA-256 is
`3d635eec889d674227b98e1e050715b3b6df3cdce2a50b3ff7411d0a334fae97`.
The Phase-5 privileged restore-family proof has the same CRLF checkout cause;
the script's committed blob did not change between Phase 5 and terminal main.
The T15C handoff test's T15B JSON pin is likewise reproduced exactly by
applying Git's `core.autocrlf=true` checkout filter to the T15B artifact
commit `0c8e566`; its four other T15A/T15B pins match committed blob bytes
directly. The historical values remain unchanged.
Every machine record states whether its historical hash was portable, its
artifact commit, the concrete mismatch reason, and its current validation.

## Adversarial checks

`tests/test_committed_source_identity.py` clones one commit twice with LF and
CRLF checkout behavior. The raw source bytes differ; the v2 fingerprint and
full blob list are identical. Committing a real source-byte change produces a
different blob ID and v2 fingerprint. Historical tests now validate recorded
artifact commits and concrete Git checkout-filter materialization where their
stored hashes used it. Current-head certificates use a separate v2 identity.

Certificate types are kept distinct: historical immutable certificates,
current derivative/revalidation certificates, and the current release-gate
certificate. A historical certificate does not become invalid merely because
the repository HEAD advances.
