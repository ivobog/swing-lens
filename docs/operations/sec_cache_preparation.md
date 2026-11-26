# SEC cache preparation

`python -m scripts.ops.sec_cache_preparation inspect` returns the candidate runtime,
operator and database identities using a read-only transaction.

To inspect one preparation's receipt and projection IDs and hashes, add
`--approval approve.json --signature approve.json.sig --ticker SYMBOL` to
`inspect`. The approval must match the current identities and remain within its
signed window. The output omits filing bodies and extracted payloads.

For the isolated Windows C06 qualification, both signed operators accept
`--qualification-trust`. It requires the exact loopback qualification database,
the verified `SwingLensC06R1Qualification` Windows service, and the
administrator-owned test signer under the separate qualification principal.
The ordinary invocation uses the existing production trust configuration.

Preparation requires a protected owner OpenSSH signature in the
`swinglens-sec-cache-preparation` namespace. The signed JSON binds the runtime
and recovery roots and Git identities, issuer manifest, replay identity, runtime
configuration, database fingerprint and role, zero provider budget and a UTC
window of at most 30 minutes. Verify the exact recovery artifact and release
scope before issuing approval.

Run the foreground operator with the signed manifest and the reviewed wire
receipt index.

```text
python -m scripts.ops.sec_cache_preparation prepare --approval approve.json --signature approve.json.sig --manifest cache-coverage-reconciliation.json --wire-proofs wire-proofs.json
```

The supplied manifest must retain the exact 514 required issuers, exemptions and
2,570 frozen selections. Each wire index item identifies an exact CIK, accession,
primary filename, retained journal path below the signed archive root and
one-based receipt line. Report a missing independent origin receipt or body as
blocked. The bytes and custody of the reviewed PBF quarantine are verified
without admission into the ordinary filing cache.

The operation uses the existing SHADOW ingestion, document claims and sealed
scope events. It writes preparation-only sources with no financial correction
or supersession linkage. Its typed completions can contribute to operational
readiness. Neither successful extraction nor attribution exclusion publishes a
financial source, advances financial freshness, or starts downstream work.

An interrupted operation resumes under the same verified preparation scope.
After the signed window expires, use a newly signed window for the same
preparation identity and immutable inputs. A different scope requires its own
approval. Keep the incumbent ACTIVE and the successor DEPLOYED throughout this
operation. Certification and recoverable promotion are independently controlled.
