# Canonical native CERI identity and ownership

The unit of current publication is one native score for one upload, company,
and persisted frozen calculation context. Its complete identity additionally
includes the exact effective configuration, calculation version, cutoff,
completed session, calendar, and context fingerprint. A context is not a
request to evaluate several configurations or cutoffs. A changed configuration
or cutoff requires an independent authorized context.

## Storage and retries

| Population | Unique storage key | NULL treatment |
|---|---|---|
| Native, context present | `(run_id, company_id, calculation_context_id)` | `NULLS NOT DISTINCT`; an orphaned upload does not disable context uniqueness |
| Legacy, context absent | `(run_id, company_id, config_hash, calculation_version)` | Historical PostgreSQL NULL-distinct behavior retained |
| Controlled replay | `(controlled_replay_id, company_id)` | Existing independent constraint retained |

The first two indexes exclude controlled replays. Candidate selection uses the
same native/legacy partitions and keys. Configuration, version, subject, and
temporal incompatibility within a native key raises an error, rather than
skipping that row or inserting another version in the same context. Immutable
envelope hashes and the sealed financial output are checked before accepting
a retry. Comparison pointers remain separate mutable projections.

Other contexts are not retry candidates. Historical comparison uses the
existing separate comparison selector and compatibility policy. Encountering
another generation is not corruption and does not reactivate excluded history.

The native snapshot writer also verifies that its physical key, company,
ticker, calculation version, session, calendar, cutoff, and frozen
configuration agree with its calculation identity before persistence.

## Authoritative owner

`bound_pipeline_id(upload, context)` resolves exactly one persisted relationship:

1. The context's main pipeline FK, with the same upload; or
2. For `CERI_POST_ACQUISITION_FROZEN` with a NULL pipeline FK, the pipeline's
   retained `result_json.ceri_calculation_context_id`, with the same upload.

Zero or multiple matches resolve to NULL. No upload-history heuristic grants
ownership. The dedicated context's pipeline FK stays NULL. New capture receives
the job's expected pipeline ID and verifies the reverse binding, active parent,
workflow, frozen cutoff/calendar/session, and retained configuration anchor.
The verified owner is embedded in new score identity and immutable evidence.

The shared persisted-context assertion takes an explicit CERI role for both the
core CERI writer and its native score-change writer. This role validates the
same dedicated reverse binding and every frozen temporal field. Other decision
writers retain the strict main-context role. A dedicated context cannot pass
that main-context path or an ambiguous reverse binding.

Old sealed UNKNOWN pipeline dimensions are never edited. Unresolved legacy
rows on an upload with pipeline history cannot become publishable evidence.
Failure reconciliation records their unresolved attribution explicitly, using
a snapshot incident reference rather than claiming a failed sibling owns them.
Known evidence is contained only by its actual bound owner. A failed sibling
does not exclude another owner's context. Safe `PARTIAL` owners retain the
existing `pipeline-outcome-v1` / `NO_INTEGRITY_HAZARD` policy.

## Current publication population

`certified_scores` is the shared population validator for capture continuation,
change/alert delivery, asynchronous completion, executor resume, and handoff.
It requires:

- A uniquely bound owner/context and the owner's original frozen CERI anchor.
- Native scores from precisely that upload and context, with no replay rows.
- A nonempty set with exactly one score per expected upload ticker.
- Exact score-ID equality when an upstream receipt is supplied.
- Explicit company and owner lineage, exact context/cutoff/session/calendar,
  configuration and version, and matching immutable identity and payload seals.
- Valid point-in-time provider source references, including those retained in
  the immutable source manifest.
- Existing disposition, owner eligibility, and purge requirements.

The change and alert payloads retain the certified score IDs. Current changes
are restricted to those IDs; independently authorized prior comparisons remain
available through the separate history selector. An excluded or unsealed
population cannot satisfy the barrier by supplying a matching count.

Historical handoff verification takes the membership frozen in the original
manifest. Manifest 41's 164 score IDs are not reconstructed from today's
eligibility. No existing manifest or evidence row is rewritten by this change.
