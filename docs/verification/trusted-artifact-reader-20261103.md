# Trusted artifact transport and complete browser evidence

The existing verifier rejects GitHub Contents responses whose encoding is `none`.
For files above 1 MiB, GitHub returns regular-file metadata with an immutable blob
ID and omits the base64 body. This prevents PR #78 from verifying an unchanged
historical artifact before financial owner authorization.

The bounded fallback uses only the same repository's Git Blob endpoint. It
requires the commit-bound 40-character object ID, an integer size at most 10 MiB,
matching blob metadata, decoded byte length and recomputed Git object identity.
Existing SHA256 provenance seals remain independently mandatory. No download URL,
redirect expansion, arbitrary host, mutation, owner acknowledgement or approval
rule is introduced.

A read-only probe of main commit
`e246f60efd71f7fd4db1edcede5248ad7d75c7ed` fetched these actual artifacts with
the proposed reader. Neither artifact was edited or regenerated:

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `golden/v3/market-data.json` under the native certification corpus | 3,646,969 | `18fbe874def6e2ce7a925a21a23fd05d4b3f4c7427a94ddddd58186eaee1d95b` |
| `config/ib_market_intelligence.yaml` | 2,672 | `9d9dba1499294d15fbb8a0a3af6a29af58e7d96e93df7ce85b8797c608ea5430` |

The eight transport controls cover both successful paths and nonregular,
oversized, substituted object/body and invalid metadata cases. The eight geometry
controls retain complete screenshot coverage under a native PNG dimension budget.
Actual Chromium and Firefox probes also capture 40,000-pixel pages, including
device-pixel-ratio two. Every tile is a native browser image; the index enumerates
all regions. Financial, GUI and database assertions are unchanged.

The real Playwright cleanup regression now places its stale marker in the exact
output directory configured by the workflow. It still executes independent
collection and test subprocesses, requires scratch cleanup and verifies that the
source-bound release plan remains byte-identical. An obsolete assumed scratch
directory cannot supply that proof.

PR-associated CI run [41552139345](https://github.com/ivobog/swing-lens/actions/runs/41552139345)
passed all protected financial/release checks for preceding source `ff54ff2`.
The finalized head must obtain its own fresh successful checks; that earlier run
is not a substitute. No historical golden, active pin or workflow is changed.
