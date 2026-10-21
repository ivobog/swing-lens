# Ticker Intelligence Workspace — RV01 Live Sign-Off

21 October 2026 · `C:\Users\Ivica\Documents\SwingLens`

## Verdict

**Feature: CERTIFIED. Runtime activation: ACTIVATED. Normal research readiness: YES within the verified single-user operating scope. Release decision: APPROVED.**

RV01 is closed on the actual NORMAL server. All required evidence, browser, smoke and focused regression checks pass. The earlier292 and146 test totals overlap and are not added together here. This task ran **19 passing pytest cases**, plus the actual-server API and two browser journeys. No application code or existing test was changed.

## Restart

Branch `codex/ceri-authority-remediation`; HEAD `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`, with existing uncommitted work preserved. The immediate17:01:24 UTC precheck found zero authoritative queued/running jobs, zero actionable recovery invariants, no dependency reconciliation due and schema already at head. Historical49 QUEUED rows, stale RUNNING job182 and three historical invariants were non-executable under the actual authority predicates. Scheduler polling would reuse BLOCKED job968 without enqueueing work.

Executed **once**, without overrides or manual process termination:

```powershell
pwsh -NoProfile -File .\swinglens.ps1 restart -Json
```

Exit0. The supported lifecycle established its durable claim fence, obtained worker acknowledgment and confirmed zero active jobs before shutdown. PostgreSQL remained running. New ownership:

| Runtime | Verified identity |
|---|---|
|Instance|`e56dba0cae004fd291edfad6d3939d3c`|
|Web|PID64400, sole127.0.0.1:8000 listener|
|Supervisor|PID57216, generation54; previously53|
|Worker|PID43096, generation126; previously125|
|Schema|`0092_phase2_eligibility_ledgers`, unchanged|

Fresh registry heartbeats and `/ready/core` confirm supervisor→web+worker ownership and metrics listeners. `/health`, `/ready/core` and `/ready` return200 with healthy payloads. Corrected behavior is proven by the formerly failing URL returning500 before activation and200 afterward, not inferred from a new PID.

## RV01

Every request below used **http://127.0.0.1:8000**. Expected identities and provenance were independently loaded using explicit READ ONLY PostgreSQL transactions.

| Endpoint | Result | Evidence comparison |
|---|---|---|
|`/api/ceri/revisions/117847`|200|EOG/company399; observations112825/112826; recorded snapshot/source IDs, configuration/evidence hashes and lineage match|
|`/api/ceri/revisions/117848`|200|EOG/company399; observations112825/112827; same provenance checks|
|`/api/ceri/events/5325`|200|EOG/company399; current revision5325/source112911; returned revision set equals persisted rows owned by this event|
|Unknown revision/event999999999999|404 each|Established structured `TICKER_NOT_FOUND` responses|
|`/api/ceri/events/5325/revisions?sort=revision_number&direction=asc&limit=100&offset=0`|200|Correct total, ordered IDs and event ownership|
|`/api/ceri/ticker/EOG`|200|Existing ticker-detail endpoint operational|

No loader exception or unrelated evidence substitution was observed. Complete successful responses and expected persisted values are retained under `output/rv01-live-signoff/`.

## Browser

**Chromium: PASS. Firefox: PASS.** Both used the actual NORMAL server without mocked responses or disposable-server substitution.

Each journey opened Dashboard→Ticker Search→EOG preview→independent qualified workspace. It expanded CERI/source details and **clicked** revision117847 and event5325. Both navigation responses returned200; their complete JSON matched the independently verified live API responses. Back restored a functional workspace after each visit. The original dashboard remained usable. No page JavaScript exceptions or failed responses were recorded.

## Whole-feature smoke

Both engines passed default Run45→44→Back45→Forward44→refresh44, comparing URL, selector, scores, immutable evidence, chart levels, profile values and Winner context against persisted data. Run45 uses combined46423/score8.0815; Run44 uses45121/8.0877. The chart renders758 cached bars with truthful current-cache labeling and run-specific stop/target levels. Decision cards and all ten sections render; CERI, Lifecycle, Rankings and Winner load successfully.

CERI retains snapshot5136/context83 and exactly12 pinned revisions117847–117858. Source-specific CERI/lifecycle dates remain independent of the selected decision. Both runs have five frozen profiles; Winner947/763 remains correctly insufficient. Screenshots are `output/playwright/rv01-signoff-*.png`; the Firefox workspace screenshot was visually inspected.

Focused regression command:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_ceri_rv01_postgresql.py tests/ceri/test_ceri_routes_api.py -q --tb=short
# 19 passed, 11 existing deprecation warnings
```

The migrated cases retain production foreign keys and verify scoped reads, missing-source/audit behavior,404s and the unchanged production query-scope guard. Historical certification suites were not repeated.

## Preservation

Before/after comparisons confirm unchanged1,206 source/test/configuration files, schema, full pipeline/job row-content hashes, inspected global evidence ID sets/counts and selected evidence content across12 tables. Final inspection still finds no authoritative pending work, actionable recovery or dependency reconciliation due. No new pipeline run or historical job recovery occurred in the inspected state.

Browser ticker interactions were GET-only. No provider acquisition, scoring refresh, publication, order execution, historical repair or analytical mutation was invoked. Expected lifecycle registration, heartbeat, journal and telemetry changes are separate from analytical evidence. One authoritative web listener remains;8779 is free. Task-owned browsers closed. No second restart, commit or push occurred. Supporting fingerprints are scoped checks, not a byte-for-byte database image.

Reproducible probes and results: `output/rv01-live-signoff/live_api.py`, `browser.py`, `api-results.json`, `live-browser.json`, `preservation.json`, `activation.txt` and readiness/status snapshots.

## Remaining issues and limits

No blocking evidence-integrity or feature-functional defect remains in the executed checks. Concurrent large-history capacity and OS peak memory remain **NOT VERIFIED**; approval does not certify those scenarios or the entire SwingLens application.

The standard restart reported HEALTHY with Prometheus/Grafana READY; later snapshots also show observability OK. No separate monitoring repair was performed. **An operational status discrepancy remains:** final lifecycle summaries report DEGRADED/Application failed with no application payload, although direct quiet `/ready` returned200/`ok` with healthy checks, and a native PowerShell request using the same five-second timeout also returned200. Quiet direct readiness took4.36s; the short PowerShell request about5.01s. Probe timing is a possible explanation, not a proven root cause. This warning is retained without changing lifecycle controls or starting an unrelated investigation; it did not prevent the measured single-user research workflow.

## Release decision

**APPROVED** for the verified normal single-user Ticker Intelligence Workspace workflow. All RV01 live URLs, identity/provenance checks, unknown-ID handling, real Chromium/Firefox clicked-link journeys, whole-feature smoke and relevant regressions pass. Runtime activation is complete; capacity/peak-memory limits and the separate lifecycle-summary warning remain explicitly documented.

**TICKER INTELLIGENCE WORKSPACE: CERTIFIED AND RELEASE APPROVED.**
