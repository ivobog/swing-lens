# Historical incident tools

The 29 incident tools in `standalone_lifecycle.csv` have no current release
certification authority. Their final A10 category, original provisional decision,
source archive hash, replacement regressions and entry policy are recorded in that
existing registry. The original incident reports, failed outcomes and source
snapshots remain evidence. No historical PASS or constructed example supplies an
A04 certificate or an A16 release result.

Category A retains historical evidence. Category B retains the specified reusable
regression while archiving historical execution. Category C retains a restricted
manual utility. Category D retires execution while retaining its source and
incident artifacts. Archived and retired Python entry points stop before their
original imports. The historical SQL file remains explicitly read-only.

Four modules retain bounded helper imports: the two Run101/102 constructed-example
helpers and the Run41 recovery fingerprint/evidence-row helpers. Their historical
CLIs are disabled. The constructed helpers explicitly return
`CONSTRUCTED_EXAMPLE_ONLY` and `current_release_certificate=false`. Their ordinary
tests check dictionary structure and values; they do not run production handlers.

## Retained manual operations

The seven manual Python utilities refuse imports, CI execution and certification
runtime execution before loading application settings. No configured database is
an implicit target. A manual operator must supply:

- `--historical-target-url` with an explicit loopback PostgreSQL target. Unknown
  database names and remote hosts are refused. Use the known incident database
  `swinglens`, or a specifically named `swinglens_pytest_*` disposable target.
- `--allow-normal-database` when deliberately targeting the known incident
  database. This flag alone authorizes no operation.
- `--historical-authorize` equal to the exact script path, operation and target
  database, joined by colons. Clone operations also include the explicit clone.
- `--historical-clone` for tools that create, inspect or drop a clone. Retained
  clone metadata must match this separately authorized target.

Consequential operations additionally require a human terminal and entry of the
exact displayed `APPLY` phrase. There is no unattended confirmation flag. The
original action arguments, incident preconditions, ownership checks, schema
checks, production mutation authority and database safety checks still apply.
Authorization does not bypass a retired production operation or make an old
schema or incident fixture applicable to the current application. Refusals and
uncaught operational failures return a nonzero exit status.

Read-only Run43 observation, Run159 analysis and Run41 monitoring remain available
with explicit authorization. Their original transaction read-only enforcement is
retained. They also write evidence files, so these actual manual modes require
human confirmation before any application import, just like database mutations.
The Run40 bad-lock reproduction remains a dated manual fault witness;
the healthy watchdog behavior belongs to its canonical PostgreSQL regression.
Do not overwrite retained incident evidence when conducting a separately approved
investigation; preserve fresh operator output separately.

## Release separation

The A02 checker verifies all 29 entry policies, the shared manual guard source
digest and the final category counts. It rejects direct and indirect references to
these incident paths in workflow or A16 lane invocations. Canonical regression
tests retain their active or extended selections. The A04 finalizer remains the
authority for deterministic pipeline certification, and A16 revalidates its inputs
against the current source and invocation. Static registration grants no result.
