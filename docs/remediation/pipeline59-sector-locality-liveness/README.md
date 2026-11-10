# Sector evidence locality and worker liveness repair

Base release: `de4873c92a292f415842b8f66cbc71d75cd35bb7`.
Implementation date: 10–11 November 2026.

The bounded Sector reader prefetched evidence by ID while production visited it
in profile/ticker order. The 64-record cache repeatedly discarded bodies before
use. Validation now visits monotonically ordered evidence IDs and restores the
original role and output positions. SQL result order is explicit. The existing
64-record and 64 MiB decoded-residency bounds remain in force. Source locking
order, seals, serving-value comparisons, eligibility, frozen configuration,
context, ownership and independent declaration/persistence checks remain intact.
Successful authority decisions are never retained between passes.

Each adapter pass builds one evidence-ID permission index. A shared manifest
object is fingerprinted once inside the stable, callback-free manifest collection
loop; distinct content fingerprints are still checked independently. These are
local representation optimizations, not a reusable validation result.

The supervisor now distinguishes positive process identity, physical exit/PID
reuse, unavailable inspection, delayed registration/control heartbeat, and a
fresh, authorized job execution with an unexpired lease. A positive matching OS
identity and fresh control/job signal preserve ownership while the existing
memory and business-progress guards remain active. Unknown inspection has a
bounded confirmation window; an alive but unresponsive worker has a shorter
window. Neither weak signal grants execution authority. Decisions are recorded
in the durable lifecycle journal before fencing and on the affected authorized
job. Historical memory evidence no longer describes a later process-loss event.

Heartbeat capability reflection is cached by Engine and observed Alembic heads;
the small schema-version marker is still checked. Legacy stores without that
marker use reflection without issuing an invalid query. Registration health
fields load together. No database schema or production configuration changed.

Sector status shows the current operation's own count, hash bytes when relevant,
operation duration, business-progress age and worker-heartbeat age. Operational
receipts retain bounded phase history and actual fetch batches, decoded body
rows/estimated bytes, hits, misses, evictions and resident peaks. The previous
`evidence_bulk_load_count` value was an address count; those concepts are now
separate. Cache eviction counts are scoped to the shared transaction residency
cache, while load counts belong to the reader instance.

## Verification

Receipts are preserved in the workspace's
`docs/remediation/pipeline59-sector-locality-liveness/` directory. They contain
no database credentials. Source hashes accompany this document.

- Final focused unit selection: **213 passed**, including new liveness,
  generation-bound confirmation, schema capability, phase history and status
  presentation regressions. A preceding expanded selection passed 372 checks;
  the final selection includes the subsequently added cases.
- Native PostgreSQL regression selection: **30 passed**. It covers bounded
  evidence loading in an adversarial five-group order, exact source seals,
  oversized evidence, tampering, residency, continuation, rollback, late owners
  and terminal publication. The final supervisor isolation selection was rerun:
  **3 passed**; unrelated owners remain untouched.
- Architecture registry: **passed, zero exceptions**. Ruff and JavaScript
  syntax checks passed. Existing dependency/deprecation warnings remain.
- Restored 466-company/five-profile qualification: **passed** through actual
  replacement-worker checkpoint validation and committed Sector publication.
  All 3,729 upstream evidence records and native upstream table digests were
  preserved. No providers or upstream recalculations ran. A separate verifier
  checked every published child value, legacy serialization parity and the
  exact before-fix publication fingerprint:
  `0706f054b8d773cc798f72e6817dd66f7e4782753ea8f82103d162ca29a7d6c7`.
  Worker peak private commit was **1,194,622,976 bytes**; independent verifier
  peak was **1,257,795,584 bytes**, both below the unchanged 6 GiB budget.
  Universe traversal loaded 3,760 bodies for 3,728 addresses in 118 batches.
  Each independent declaration/persistence reader loaded 2,797 bodies for 2,797
  addresses in 89 batches. Peak retained cache was 64 records / 51,502,734
  estimated decoded bytes. The owned restored database was removed after zero
  connected clients were verified; its source dump and receipts were preserved.

The restored qualification intentionally stops after Sector commits, at the next
handoff boundary. Its QA-derived predecessor receipts and retained native cohort
are explicitly recorded. It proves Sector continuation/publication parity, not
completion of later CERI/Setup/Winner stages or a contiguous full pipeline.
Elapsed worker qualification was approximately 39 minutes, including checkpoint
certification/resume; expensive independent canonical validation still takes
minutes. This repair does not promise an instantaneous Sector step.

The separate fresh 466-company producer-to-Sector qualification is pending at
the time this document is prepared. It uses real Fundamental, Technical,
Combined, five-profile Ranking, Regime and Sector producers with admitted cached
facts and forbidden provider calls. Its fixture deliberately separates score
order from ticker order and asserts a nonlocal Ranking evidence traversal before
Sector runs. Release activation remains pending its result and a safe live
lifecycle boundary.
