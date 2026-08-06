# Manual Test Procedures

Automated evidence covers deterministic product contracts. The following checks require human,
environmental, licensed-provider, or long-running observation and are not represented as automated
passes.

## M-01 Edge Visual and Interaction Smoke

Open the dashboard, upload, run detail, pipeline progress, market regime, sector rotation, Setup
Lifecycle, Winner Probability, CERI, settings, help, and export flows in current Microsoft Edge.
Check 390 px, 768 px, 1280 px, and 1920 px widths. Verify no overlap or horizontal loss, visible
focus, logical keyboard order, named form fields, table headers, readable alerts, and chart fallback
text. Compare behavior with the automated Chromium and Firefox lane.

2026-08-06 status: **PASS** on Microsoft Edge `151.0.4129.59`. A headed Playwright CLI session
using the installed `msedge` binary exercised 13 core surfaces and eight run-scoped surfaces at all
four required widths (84 page/width checks). Every response was 200, every surface retained its
title, H1, main landmark, named controls, and table headers, and no page-level horizontal overflow
remained after DEF-006 was fixed. Screenshots were visually inspected at 390, 768, 1280, and 1920
px. The skip link received a visible 2.67 px focus outline and Enter moved focus to `main-content`.

The Edge flow uploaded a two-row UTF-8 fixture, preserved `SAP München`, opened the run detail,
created the run-scoped regime and sector views, downloaded the 545-byte raw CSV with a 200 response,
and rendered an empty-CSV failure as an alert. With a disposable durable pipeline and its worker
disabled, the confirmation dialog, queued eight-step progress view, and queued-job cancellation all
passed without contacting IB. The final database state showed two preserved raw rows, one sector
snapshot, and matching terminal `CANCELLED` pipeline/job records. Settings showed `Market data only`
and `Read-only`, exposed no database URL or credentials, and contained no order action. The clean
post-fix Edge session logged zero console errors. Both explicitly named disposable databases, the
localhost server, and both browser sessions were removed after evidence capture.

M-01 found and closed DEF-006 (responsive CERI table containment), DEF-007 (missing favicon), and
DEF-008 (failure messages lacked alert semantics). Automated regressions now run in both Chromium
and Firefox; the focused browser lane passed 10 tests.

## M-02 Assistive Technology

Use Windows Narrator or NVDA with keyboard-only navigation. Verify the skip link, page/section
headings, primary navigation name/state, form labels, alert announcements, table header context,
loading status, errors, and non-color warning cues. Record browser and screen-reader versions.

2026-08-06 status: **PARTIAL**. Automated browser evidence now covers 13 representative surfaces
in Playwright Chromium, Firefox, and installed Microsoft Edge. Each surface returned 200 and passed
main landmark, single H1, named primary navigation, named enabled form controls, table-header,
non-color status/warning text, and rendered WCAG AA normal-text contrast checks. The first run found
muted explanatory text at approximately 4.41:1 and warning badges at approximately 4.43:1. DEF-009
darkened the shared muted and warning tokens; all 18 focused tests then passed in each browser, and
the complete regression passed with 1,132 tests and one skip.

The remaining step is a human auditory check in Narrator or NVDA. Confirm that the skip link and
heading/navigation structure are understandable when traversed, and that upload errors, pipeline
progress, reconnect messages, and loading-state changes are announced at the expected time without
duplicate or excessively verbose speech. Automation does not constitute evidence that a person
heard and understood those announcements.

## M-03 Live IB Paper Connectivity

Execute `LIVE_IB_PAPER_VALIDATION.md`. This is read-only and must never use a live account or order
API.

2026-08-06 status: **PASS**. Read-only connection, contract resolution, uploaded-run benchmark
coverage, daily bars, cache reuse, real localhost transport isolation/reconnect, failed-item
export/retry, live cancel/resume, output redaction, and runtime/static no-order checks passed. The
network-isolation alternative used a disposable localhost proxy, so the authenticated Gateway and
paper session remained intact.

## M-04 Licensed CERI Provider Certification

Only when a licensed adapter and approved test credential exist, verify credential-missing health,
provider health, rate limiting, outage degradation, restricted-field policy, source URL policy,
raw-payload prohibition, and export/log redaction. Use canary credentials and destroy them after
the test. Do not copy licensed payloads into QA artifacts.

## M-05 Restart and Long-Running Resilience

Against a disposable environment, run a 250-ticker pipeline while restarting the web process,
worker, and PostgreSQL one at a time. Verify lease recovery, progress, retry counts, coalescing,
terminal status, and absence of duplicate evidence. Then execute repeated daily-style runs for at
least eight hours and inspect CPU, memory, cache growth, stale jobs, and database growth trends.

2026-08-06 status: **PARTIAL**. The finite scale and real restart portions are automated and were
executed against disposable PostgreSQL environments at commit `13c87b1`:

- `run_m05_scale.py` processed 50, 250, and 1,000 tickers with 756 deterministic daily bars per
  symbol and zero IB requests. Exact raw, fundamental, technical, combined, and five-profile
  ranking evidence counts passed at every size. Seven of ten documented performance checks passed;
  the 250-ticker technical step (182.932 s), 1,000-ticker run-detail p95 (2.838 s), and 1,000-ticker
  combined-export p95 (2.288 s) missed their 60 s, 1.5 s, and 2 s targets. DEF-010 remains open S2.
- `run_m05_restart.py` passed in 337.2 s using its own PostgreSQL 16 container. Web restart, two
  worker lease recoveries, PostgreSQL stop/start, request coalescing, and final evidence integrity
  passed. Readiness returned HTTP 200/degraded in 3.063 s during outage and HTTP 200/ok after
  restart. The drill planned zero IB requests and left one terminal job, zero active jobs, 250 rows
  in every core scoring table, and one regime and sector snapshot. DEF-011 was fixed and regressed.
- A two-cycle `run_m05_soak.py` shakedown passed in 97.5 s: 100 technical/combined rows, 500 ranking
  rows, zero active/stale jobs, stable 214 SQL statements per pipeline, and 191,356,928-byte peak
  RSS. This is harness validation, not eight-hour evidence.

The remaining M-05 action is the actual eight-hour observation. Run the following from a stable
local session and retain `test-results/m05-soak.json` plus the console log:

```powershell
uv run python scripts/qa/run_m05_soak.py `
  --duration-hours 8 `
  --interval-seconds 900 `
  --output test-results/m05-soak.json
```

Only a report with `mode=RELEASE_SOAK`, `status=PASS`, and
`completed_target_duration=true` closes the duration requirement.

## M-06 Populated Multi-Module Restore — Automated PASS

`tests/integration/test_populated_restore.py` now creates two safely named disposable PostgreSQL
databases, migrates the source, seeds raw uploads, bars, technical/fundamental/combined results,
jobs/pipelines, regime, sector, SLSE, OWPE, CERI, and administrative audit evidence, then performs a
real custom-format backup and restore. It requires all tracked table counts and canonical SHA-256
digests to match, validates foreign keys and evidence hashes, and requires readiness to be healthy.

The 2026-08-06 execution passed locally against the PostgreSQL 16 Compose service using PostgreSQL
18.3 client tools. The operational PowerShell runbooks also passed with 20 populated evidence and
audit tables, no row/hash mismatches, and no blank required hash fields. Repeat this automated gate
for every release candidate; it no longer requires manual sign-off when the CI gate is green.

## M-07 Product/Model Sign-off

The product owner and scoring reviewer compare labels, thresholds, warnings, ordering, setup
semantics, regime/sector policies, probability definitions, and CERI policy against approved
business intent. Any golden re-baseline requires a separate rationale and version change.

## Evidence Template

Record procedure ID, date/timezone, commit, OS/Python/browser/provider versions, flags, disposable
database name, input fixture/hash, exact steps, expected/actual result, screenshots or redacted logs,
defect IDs, and reviewer/sign-off.
