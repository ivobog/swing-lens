# Current CERI reader fixture ownership

The Chromium/Firefox smoke check selected an older decision run and expected the
independent current CERI projection dated October 21. The positive reader fixture
had persisted pipelines but no calculation contexts for its CERI snapshots. The
new exact-owner policy correctly treated those rows as unattributed and refused
current presentation.

The fixture now persists dedicated CERI contexts for the eligible October 16 and
October 21 snapshots, with the same upload/cutoff/session and the owning pipeline's
explicit reverse binding. Their context pipeline FKs remain NULL. The newer
October 22 row deliberately remains excluded legacy evidence with no context;
its exclusion is unchanged. No production reader, eligibility rule, financial
value, browser assertion, golden file or immutable historical artifact changes.
The tiny newly seeded read model is analyzed before concurrent browser reads so
PostgreSQL plans from its actual cardinalities. Original browser timeouts remain
unchanged.

This is a read-model/presentation fixture: its stored evidence payloads are test
doubles, not full financial-writer certification. The registry retains FAST /
UNIT_DOUBLES fidelity for the presentation controls even though the selected
tables are exercised in disposable PostgreSQL. Real writer, seal, ownership and
context uniqueness remain covered by the separate native CERI controls.

The existing positive assertion still requires the 8.20 current score, exact
October 21 date, retained change/alert fields and run-independent selection. A
new refusal control removes a snapshot's context link: the genuinely attributed
older owner may be selected, and removing that link too makes current evidence
unavailable. Upload history alone cannot authorize either row. Existing
quarantined-owner, excluded-only and missing-evidence refusals remain intact.

The shared seed change is exercised by the original Chromium and Firefox test
functions. It is not a change to the A04 financial oracle or an approval waiver.

Local validation: all 12 existing browser cases passed across Chromium and
Firefox after the final fixture update. All 14 reader cases, including the
positive and unattributed-owner controls, passed against that same final fixture.
Ruff and the registry check pass; the impact plan remains PLAN_READY with no
additional review required.
