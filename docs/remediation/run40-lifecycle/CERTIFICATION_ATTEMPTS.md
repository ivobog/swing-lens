# Run 40 remediation: certification attempt record

All databases in this record are disposable PostgreSQL databases. Original Run 40
was never retried or modified. Failed results remain available; later results do
not replace them. Times below identify actual failure observations in UTC.

| Evidence | Observation / disposition |
|---|---|
| `evidence/native-88.xml` | Historical fixture failed after 6,350.717s: its test anchor froze an empty alert-rule selection although native alert processing consumed enabled rules. Fixture now seeds/resolves native rules before anchoring; the configuration guard remains enabled. |
| Initial browser admission | Dirty shared checkout rejected with `UNVERIFIED_DEPLOYMENT_WORKTREE`. User then authorized isolated local certification snapshots. Main was not committed or pushed. |
| Initial clean browser setup | Startup health checks failed while other CPU-heavy certification was active. Startup deadlines were not increased. |
| Initial clean provider fixture | Native fundamental-admission guard rejected a decoy score without authentic evidence. Existing harness now produces native raw financial inputs, frozen authority and evidence before admission. No guard bypass. |
| `evidence/full-e2e-native-prerequisite.xml` (`09db995`) | Comprehensive eight-symbol scenario passed. Strict 88 failed the transaction observer during canonicalization (127.235s transaction; no fresh useful progress). Corrected native canonicalization into five-group batches under the same locks/transaction. |
| `evidence/full-e2e-bounded-checkpoints.xml` (`d538cfe`) | Comprehensive scenario passed. Strict 88 failed at 15:32:44 during technical scoring, before technical completion at 15:32:47. Later combining state was not the failure stage. |
| `evidence/full-e2e-serialized-88.xml` (`c18f205`) | Strict 88 reached all upstream/lifecycle stages without observer failures, but terminal pipeline was PARTIAL: all 88 Winner attempts failed with `NoneType.scalars`. Retained stage/lineage evidence: `evidence/e2e-serialized/20261017T153622Z-fe2e3cc6/anchor-and-lineage.json`. |
| `evidence/winner-trace-10.xml`, `winner-trace-serial-10.xml` | Two diagnostic trace-hook startup errors: hook assumed an optional trace-directory environment variable. Corrected to the existing child-log directory. These were test-hook errors, not evidence of production startup/resource failures. |
| `evidence/winner-trace-corrected-10.xml` (`3e339bf`) | Ten-symbol browser reproduced Winner failure. `evidence/winner-sector-session-trace.txt` proves Winner passed an explicit Session with a detached sector source; shared validator discarded that Session and dereferenced None. |
| `evidence/sector-session-before.xml` | Native PostgreSQL red reproduction of the same detached-sector failure. |
| `evidence/sector-session-after.xml` | Two native PostgreSQL cases passed: valid detached projection, missing-session rejection, SQL child-score tampering rejection, rollback and actual Winner insertion, with/without retained history. |
| `evidence/full-e2e-sector-session-final.xml` (`87cdbdc`) | Ten-symbol strict case and comprehensive eight-symbol case passed. Strict 88 failed at 17:21:22 during ranking (17:19:21–17:21:32); technical had completed at 17:18:44. Ranking lacked intra-stage progress; technical also lacked progress during final score-evidence persistence. Added optional completed-work callbacks every five items and pipeline cancellation adaptation. Observer thresholds stay unchanged. |
| `evidence/upstream-progress-postgresql.xml` | First new test incorrectly assumed input admission was uncommitted. Technical intentionally commits inputs before score publication. Corrected assertion to the interrupted artifact boundary. |
| `evidence/upstream-progress-postgresql-corrected.xml` | Two cases passed: actual flushed technical/ranking evidence remains invisible to a second Session and absent after injected control-plane interruption/rollback. Existing committed input rows remain. |
| `evidence/upstream-progress-unit.xml`, `upstream-progress-cancellation.xml` | 85 affected unit tests and one pipeline cancellation regression passed. |
| `evidence/full-e2e-upstream-progress-final.xml` (`80026eb`) | Both passed in 3,645.23s including fixtures/UI checks. Strict 88: all 13 stages completed, 88 native CERI/setup/Winner outputs, 528 provider source records, two FULL_PIPELINE jobs, 4,507 observer samples with zero failures. Comprehensive: 659 comparisons, 19 GUI surfaces, 51 database relationships. |
| `evidence/native-fixture-handoff.json` | The long-lived native fixture builder was stopped before current capture or timed evaluation, after producing all 321 historical snapshots and 88 current native upstream inputs. Its loaded code predated late validator/progress corrections. Authenticated input snapshot retained; final-runtime recheck runs real capture and the exact existing 34-statement interruption/publication/replay/Winner body. No production process was stopped. |
| `evidence/native-88-final-runtime-restore-argument.xml` | First cold-process recheck failed before application work: pg_restore required an explicit database argument. Corrected the test command, retaining database-safety checks and the original input snapshot. |
| `evidence/native-88-final-runtime.xml` (`d839b59`) | PASS, 3,335.90s including restore, actual 88-symbol capture, both injected interruptions, successful publication, completed replay and 88 Winner insertions. Evaluation: 630.539s, 21,738 SQL operations, 1,069 changes, 88 transitions, 44 alerts, zero failures. Application sources match passing browser candidate `80026eb`. |
| `evidence/final-replay-boundaries.xml` (final snapshot `4396ff7`) | 10 passed after the final read-only callback delta: completed replay now checks progress/cancellation after each validated episode/alert. Actual cancellation at both PostgreSQL boundaries preserves the original completed publication and all domain counts; subsequent replay passes. Queries, validators, result construction and all writer/authority behavior are unchanged. |

The comprehensive browser case verifies 659 comparisons, 19 GUI surfaces and 51
database relationships. Strict cardinality certification requires terminal
COMPLETED, all 13 steps completed, exactly two FULL_PIPELINE jobs, all 88 native
CERI/setup/Winner outputs, no unexpected retries/stalls, and the existing runtime
observer. No PARTIAL execution is treated as passing.

`evidence/test-scheduling.json` records pauses of the long historical fixture
strictly before its current run and measured evaluation. Browser runners resumed
that builder in `finally`; its later final-code handoff is recorded separately.
Production processes were never suspended or restarted.
`evidence/certification-source-version-matrix.json` distinguishes the long-lived
native process from later focused and clean-browser verification.
