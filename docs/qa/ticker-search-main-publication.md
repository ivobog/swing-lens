# Global ticker search and intelligence workspace: main publication

21 October 2026

Publication base: `900425bd43fc79d62449d28cf0764db369e97d8b` (`origin/main`).

## Scope

Publish the shared non-modal ticker search dock, qualified local identity search
and preview, independent ticker workspace, cached chart and frozen decision cards,
and CERI/Lifecycle/Rankings/Winner panels. Include the F01–F06, C01–C05 and RV01
corrections, their regression tests, and the implementation/review/remediation/
certification reports. The RV01 live sign-off records acceptance of the existing
NORMAL runtime; the earlier reports retain their historical verdicts.

The source checkout has diverged from main and contains unrelated uncommitted
work. Publication was assembled in a separate checkout of the base above. Shared
read dependencies include the pipeline-outcome contract, per-snapshot CERI owner
correlation and eligibility, bounded CERI evidence queries, canonical Winner
estimate selection, and frozen chart levels. No pipeline executor, worker,
producer readiness policy, schema migration, runtime configuration or unrelated
recovery change is part of this publication.

## Fixture compatibility

The feature reads frozen readiness envelopes rather than recalculating old
evidence using today's producer policy. The source branch's producer policy is
newer than main's. Its reader fixtures originally called that newer normalizer
to create READY evidence carrying the advisory `market_risk_off` warning.

The publication fixtures explicitly preserve that intended stored READY state
when the warning is the sole nonblocking producer warning. Native signals and
warning reasons are retained; incomplete, blocked and other degraded fixtures
are unchanged. This is confined to the two new test modules and avoids importing
an unrelated producer-policy campaign into main. Application feature code matches
the accepted implementation.

## Publication validation

Validation runs against this isolated publication tree using the existing Python
environment. PostgreSQL cases use guarded disposable databases; browser cases use
separate disposable servers. Local database credentials are supplied through the
process environment and are not included in the commit.

- Backend, existing UI/API, architecture inventory, CERI, Lifecycle, Rankings and
  Winner regressions: **241 passed**, one existing Starlette deprecation warning.
  This includes all three new ticker service/route test modules.
- Migrated PostgreSQL remediation and owner-correlation regressions: **28 passed**
  across `test_ticker_intelligence_remediation_postgresql.py`,
  `test_ticker_certification_remediation_postgresql.py`,
  `test_ceri_rv01_postgresql.py` and
  `test_ceri_evidence_quarantine_postgresql.py`. Only existing deprecation
  warnings were reported.
- Chromium and Firefox: **44 passed** in three separate pytest invocations:
  dock/workspace/intelligence journeys (**38**), certification remediation
  (**4**) and clicked CERI evidence links (**2**). Each invocation used
  `--browser chromium --browser firefox -q --tb=short` and a fresh disposable
  browser database. Only the existing Starlette deprecation warning was reported.
- Ruff on the feature's Python sources/tests and staged whitespace validation:
  **PASS**.

Total: **313 passing cases** against the publication tree. No case was skipped.

The NORMAL application checkout, running processes, production analytical data,
and unrelated local changes are preserved. No activation or restart is needed
for this Git publication.
