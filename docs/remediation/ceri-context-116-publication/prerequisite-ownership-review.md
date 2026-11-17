# PR 82 prerequisite source ownership

The CERI release includes its prerequisite commits through `6d6952f`; the
comparison with GitHub main therefore includes older source changes as well as
the current CERI correction. The impact planner correctly refused ten paths
without an accountable contract association. The additions below assign exact
paths to existing domains. They add no wildcard, exception, new contract,
certificate or approval. Unknown high-risk paths continue to require review.

| Exact path | Contract | Reviewed boundary |
| --- | --- | --- |
| `app/services/background_queue.py` | Worker/supervisor recovery | SEC readiness repair uses the existing interactive queue and fairness controls. |
| `app/services/contextual_calculation_identity.py` | Core configuration | Sector input hashing gains progress callbacks while preserving canonical identities and source references. |
| `app/services/decision_mutation_authority.py` | Core configuration | The CERI change writer selects its dedicated context role; all other decision writers retain MAIN context validation. |
| `app/services/export_service.py` | CERI temporal lineage | Retained SEC coverage is disclosed only for the exact latest pipeline, scope and processor signature; no readiness is invented. |
| `app/services/job_progress_diagnostics.py` | Worker/supervisor recovery | Bounded stack/memory diagnostics exclude local values and cannot renew or manufacture progress. |
| `app/services/process_identity.py` | Worker/supervisor recovery | PID/creation-time checks distinguish reused/dead processes from denied or unavailable inspection. |
| `app/services/sector_serialization.py` | Regime/sector selection | Fresh per-call copies and streaming hashes preserve canonical bytes, unordered-field rules, cycle refusals and cancellation. |
| `app/services/technical_score_service.py` | IB admission | Known unavailable sources cannot publish stale numeric technical scores; hard readiness blockers still reject publication. |
| `app/templates/history_gap_repair.html` | Native pipeline certification | The exact bounded repair-review form submits the retained manifest fingerprint to the server-owned repair route. |
| `scripts/replay_sec_identity.py` | CERI temporal lineage | Manual wrapper requires explicit processing run, ingestion run and actor, then uses the locked reviewed replay service. It remains manual and is not a CI test or release producer. |

Source symbols are checked against the actual modules. The template uses one
exact impact association because HTML has no Python source symbol. Existing
contract tests and all mandatory lanes remain required; these static mappings
do not assert complete measured call coverage for every associated source.
The CERI-specific review adds explicit native context-key, ownership, sealed
population and refusal controls separately in `source-contract-review.md`.

The original review revisions, source fingerprints, financial golden values,
A04 finalizer, source freshness checks and authenticated golden update rule are
unchanged. Current CI must execute and finalize its own release evidence.
