# Current Phase-5 authority derivative

The immutable Phase-5 commit `f587b63e4e35e81486e53ffa0f13b9cd7369f963`
certified 252 callers in 220 operation families. The terminal Phase-7 commit
`d5066e979ac3958c7e5a4cd125c08106856f1026` retains the same exact
initiator membership, writer families, domains, semantic modes, transaction
model, authority requirement set, and final dispositions: 252/252 callers,
220/220 families, zero incomplete/partial authority, potential bypass,
confirmed bypass, or unknown families.

The machine CSV maps every changed family: 61 are
`SEMANTICALLY_EQUIVALENT_EVOLUTION` already represented by the committed
Phase-7 T14D family records and T16E's explicit Phase-5 regression PASS. Their
source proofs/implementation adapters evolved in Phase 6/7, but the semantic
writer/authority contract fields and caller membership did not. One further
`IDENTITY_ONLY_DRIFT` is `EF_PRIVILEGED_DATABASE_RESTORE`: the old
`AF_680ae1fd6dda2a7d` ID includes a CRLF working-tree file hash, while the
canonical committed blob yields `AF_a9ea1ba926a3cbe9`. The restore script's
committed blob is identical at Phase 5 and terminal main. No operation was
added or removed. A separate `AF2_` semantic key, independent of file bytes,
is recorded for every family; its mapping is stable across all 220.

The derivative JSON also reconciles all 190 Phase-5 review source pins to
current canonical Git blob IDs. Of these, 171 still point to the same
committed content after accounting for checkout conversion, 17 legacy
precommit worktree hashes have no exact committed-blob match, and two pins are
release tooling checkers changed here. The 17 are **not waived or silently
represented as portable**: each has its old hash, the unresolved old materialization
classification, and its current committed blob ID. Current authority is
certified by the complete family mapping, the Phase-6/7 integration
certificates, and current native tests—not by pretending those old raw bytes
are recoverable. No active supported-current defect or new finding is inferred
from their historical materialization gap.
