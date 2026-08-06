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

## M-02 Assistive Technology

Use Windows Narrator or NVDA with keyboard-only navigation. Verify the skip link, page/section
headings, primary navigation name/state, form labels, alert announcements, table header context,
loading status, errors, and non-color warning cues. Record browser and screen-reader versions.

## M-03 Live IB Paper Connectivity

Execute `LIVE_IB_PAPER_VALIDATION.md`. This is read-only and must never use a live account or order
API.

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

## M-06 Populated Multi-Module Restore

Create a disposable completed run containing raw uploads, bars, technical/fundamental/combined
results, jobs/pipelines, regime, sector, SLSE, OWPE, and CERI evidence. Back it up, restore it into a
new clean database, run `validate_restore.py`, compare critical row counts and hashes, start the app,
and confirm `/ready` is healthy before reopening writes.

## M-07 Product/Model Sign-off

The product owner and scoring reviewer compare labels, thresholds, warnings, ordering, setup
semantics, regime/sector policies, probability definitions, and CERI policy against approved
business intent. Any golden re-baseline requires a separate rationale and version change.

## Evidence Template

Record procedure ID, date/timezone, commit, OS/Python/browser/provider versions, flags, disposable
database name, input fixture/hash, exact steps, expected/actual result, screenshots or redacted logs,
defect IDs, and reviewer/sign-off.
