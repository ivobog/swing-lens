# CERI prospective revenue capture

Use this foreground command from the clean, certified SwingLens release root with the normal runtime profile. The owner must separately approve deployment, reservation and execution. The first prospective scope is the F8 grant for AAPL.US, AMZN.US, ABBV.US, LIN.US, HG.US and SPOT.US. A narrower selection requires the owner's explicit scope in both signed approvals. The preflight reports a local evidence conflict for any affected issuer and blocks reservation and execution of that scope.

```text
python -m scripts.ops.ceri_prospective_revenue inspect
python -m scripts.ops.ceri_prospective_revenue inspect --approval reserve.json --signature reserve.json.sig
python -m scripts.ops.ceri_prospective_revenue reserve --approval reserve.json --signature reserve.json.sig
python -m scripts.ops.ceri_prospective_revenue execute --approval execute.json --signature execute.json.sig
```

Unsigned inspection returns the observed identities and issuer cache statuses with `ready=false`. A signed inspection verifies the owner and checks the window, exact issuer scope, entitlement evidence and local PostgreSQL identity and schema. It uses a read-only transaction and makes no provider requests.

## Owner approvals

Provision the owner's OpenSSH public key as principal `ivobog`, restricted to namespace `swinglens-ceri-prospective`, in the machine-managed `ceri-allowed-signers` file. On Windows the file belongs under `Program Files/SwingLens` on the OS drive and the deployment administrator must protect the file and its parent from operator writes or replacement. On Unix it belongs in `/etc/swinglens` and the command checks root ownership and restrictive write permissions. The OpenSSH program comes from the OS installation. Keep the private key with the owner and only distribute the signed envelopes and detached signatures.

Build the owner envelope from the inspection output and approval record. The complete field schema is the `Approval` model in the command. It requires these fields.

| Evidence | Fields |
| --- | --- |
| Operation | `operation` is `reserve` or `execute`. `ingestion_run_id` is null at reserve and the returned positive integer at execute. |
| Authority | `source_grant` is `f8:e6c5ba72e1de8190e48e296bdf72ea3de1411688:sec-reporting-currency-v1`. `request_key` is the grant prefixed by `ceri:prospective:`. Include the named `operator`, observed Windows token SID or POSIX effective UID in `principal`, and `approval_reference`. |
| Target | `git_sha`, `runtime_fingerprint`, `database_fingerprint`, `database_principal` and `alembic_head` from inspection. The database role requires read access to PostgreSQL cluster identity through `pg_control_system()` as well as the existing table rights. |
| Exact scope | `starts_at` and `cutoff_at` as UTC timestamps no more than 30 minutes apart, `symbols` as a list, and the integer `sec_attempts`, `eodhd_attempts` and `observations` caps. |
| Entitlement | `entitlement_reference`, UTC `entitlement_checked_at`, UTC `entitlement_valid_until` through the cutoff, and `included_calendar_units` sufficient for the calendar cap. The evidence must establish access for the configured account to Calendar Trends and ordinary included quota. |

The owner signs each JSON file separately with OpenSSH `-Y sign` and namespace `swinglens-ceri-prospective`. Reserve during the approved UTC window and retain the run ID, request key, scope, plan and refresh IDs. For the execute signature, copy the approved reservation envelope and change only `operation` and `ingestion_run_id`. The command binds all other fields to the existing durable reservation. A unique key and the capture claim prevent replay.

## Closure

Retain the terminal receipt and apply the before/after preservation checks. The command ends after source persistence and normalization. Ctrl+C requests cooperative cancellation. Exit status 124 indicates the hard UTC cutoff and requires read-only reconciliation by request key or run ID before any further action. Treat a missing receipt as an unknown outcome and inspect the durable claim, terminal checkpoint and capture-owned rows. An interrupted approval cannot be reused to trigger a second acquisition. Keep the source-policy scope and historic financial evidence intact.
