# Task 2.9.12l Setup Fingerprint Forensics

## Scope and retained evidence

This analysis used the retained Task 2.9.12k focused JUnit result and transaction
diagnostic. It did not rerun the baseline fixture or any browser test.

The retained failing comparison reported:

- expected fingerprint: `426cd833c04059f3aaf614ab0a9417ca42ffdd000a9188c1b2c0917efc1d3c7e`
- persisted fingerprint: `de20197073f4c90acb08fb541d604479e22cd558b15eb914fbfedf00cdc5e8c2`
- Setup baseline session: `2026-10-13`

## Exact reconstruction

The projection was reconstructed directly from the deterministic fixture contract:

- ticker: the exact 100-member tuple used by the focused test
- `data_as_of_date`: `2026-10-13`
- `source_data_hash`: SHA-256 of
  `<ticker>:2026-10-13:baseline`

The fixture-order projection reproduced the retained expected fingerprint exactly.
The same rows ordered by the persisted SQL key (`ticker`) reproduced the retained
actual fingerprint exactly.

| Check | Result |
|---|---:|
| Expected rows | 100 |
| Persisted-shape rows | 100 |
| Expected unique identities | 100 |
| Persisted unique identities | 100 |
| Missing identities | 0 |
| Unexpected identities | 0 |
| Field-level differences | 0 |
| Sequence equal | no |

The fixture sequence begins `BHE, BLLN, KLIC, LSCC, PDFS, ACMR, RDVT, AVT,
DVN, JNJ, F0000, F0001`. The persisted SQL sequence begins `ACMR, AVT, BHE,
BLLN, DVN, F0000, F0001, F0002, F0003, F0004, F0005, F0006`.

## Identity contract

`setup_signal_snapshots` defines its complete unique identity as:

`(run_id, ticker, timeframe, data_as_of_date, engine_version, config_hash,
source_data_hash)`.

The current-selection table defines its unique semantic key as:

`(ticker, timeframe, data_as_of_date)`

and separately requires a unique `selected_snapshot_id`.

The focused certification will therefore compare complete Setup identity rows and
current-selection semantics after explicit deterministic ordering. The existing
three-field fingerprint projection remains unchanged; only its input ordering is
made explicit and identical on both sides.

## Classification and permitted remediation

**Classification: ordering-only test-oracle defect.**

No production Setup behavior, fixture cardinality, fingerprint field, duplicate
handling, or canonical serialization behavior may be changed. The permitted fix
is limited to deterministic ordering plus explicit semantic equality diagnostics
in the focused certification.
