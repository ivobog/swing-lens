# CERI prospective revenue capture

Use this foreground command from the clean, certified SwingLens release root with the normal runtime profile. The owner separately approves deployment, reservation and execution. The reviewed issuer universe is AAPL.US, AMZN.US, ABBV.US, LIN.US, HG.US and SPOT.US. Both signed approvals bind the exact issuer subset and its reviewed evidence. The preflight blocks reservation and execution if the required local evidence is missing or conflicted.

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
| Authority | For a new cycle, propose a fresh lowercase UUIDv4 as `source_grant` in the form `cycle-v1:<uuid>`. Set `request_key` to the grant prefixed by `ceri:prospective:`. Include the named `operator`, observed Windows token SID or POSIX effective UID in `principal`, and `approval_reference`. The owner signature authorizes that exact proposed cycle. |
| Target | `git_sha`, `runtime_fingerprint`, `database_fingerprint`, `database_principal` and `alembic_head` from inspection. The database role requires read access to PostgreSQL cluster identity through `pg_control_system()` as well as the existing table rights. |
| Exact scope | `starts_at` and `cutoff_at` as UTC timestamps no more than 30 minutes apart, `symbols` as a list, and the integer `sec_attempts`, `eodhd_attempts` and `observations` caps. |
| Reviewed filings | `reviewed_filings` is a list of `[symbol, filing]` pairs, exactly one for each authorized symbol. Include `cik`, SEC `entity` name, local `registry_id`, `accession`, cached-body `sha256`, statement `fiscal_end`, `family` (`ANNUAL` or `QUARTERLY`), ISO `currency`, and UTC `accepted_at`. Include `fiscal_targets` as four `[role, YYYY-MM-DD]` pairs and `fiscal_reference` identifying the issuer evidence used to review those dates. |
| Entitlement | `entitlement_reference`, UTC `entitlement_checked_at`, UTC `entitlement_valid_until` through the cutoff, and `included_calendar_units` sufficient for the calendar cap. The evidence must establish access for the configured account to Calendar Trends and ordinary included quota. |

The owner signs each JSON file separately with OpenSSH `-Y sign` and namespace `swinglens-ceri-prospective`. Reserve during the approved UTC window and retain the run ID, request key, scope, plan and refresh IDs. For the execute signature, copy the approved reservation envelope and change only `operation` and `ingestion_run_id`. The command binds all other fields to the existing durable reservation. A unique key and the capture claim prevent replay.

Review the exact current consolidated filing and the fiscal calendar before preparing the signed envelope. Record `CURRENT_QUARTER`, `NEXT_QUARTER`, `CURRENT_FISCAL_YEAR` and `NEXT_FISCAL_YEAR` as reviewed period ends. The first quarter target must cover the signed cutoff, and two roles can share the same date when a fiscal year closes. The operator independently checks the cached bytes, issuer and currency, revalidates filing freshness around the Calendar Trends request, and accepts provider rows only on an exact role-and-date match. A new filing or rollover requires newly reviewed evidence in a fresh signed cycle. Ambiguous fiscal roles or evidence that cannot be verified fail closed. The two original fixed grants retain their original keys and approval contracts.

## Closure

Retain the terminal receipt and apply the before/after preservation checks. The command ends after source persistence and normalization. Ctrl+C requests cooperative cancellation. Exit status 124 indicates the hard UTC cutoff and requires read-only reconciliation by request key or run ID before any further action. Treat a missing receipt as an unknown outcome and inspect the durable claim, terminal checkpoint and capture-owned rows. An interrupted approval cannot be reused to trigger a second acquisition. Keep the source-policy scope and historic financial evidence intact.

Use two SEC Submissions attempts and one EODHD Calendar Trends attempt per planned issuer, with at most four new observations per issuer. The signed caps and configured client budgets still apply. A duplicate source response adds no new observation or authority. A response with both new and already seen periods retains the new observations and reports the skipped roles. Provider errors remain in the terminal receipt. Reconcile the run before proposing a fresh cycle with new signatures.
