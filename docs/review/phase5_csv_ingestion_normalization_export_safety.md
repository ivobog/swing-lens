# SwingLens Phase 5 CSV Ingestion, Normalization, Identity, and Export Safety

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Phase 1 traceability: `docs/review/phase1_requirements_traceability.md`
Phase 3 configuration: `docs/review/phase3_configuration_feature_flags.md`
Phase 4 database: `docs/review/phase4_database_migrations_transactions.md`
Phase 15 security: `docs/review/phase15_web_security_local_admin.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 5 reviews external CSV handling, mapped business-data normalization, ticker identity, raw-row
preservation, failed-upload behavior, and spreadsheet/export safety.

Overall status: not exit-ready. Happy-path ingestion and core export ordering are covered, but
hostile-file handling, duplicate identity policy, exact forensic preservation, and spreadsheet
formula neutralization need hardening.

## Evidence Log

Inspected surfaces:

- Upload route and service: `app/routers/upload_routes.py`, `app/services/upload_service.py`.
- CSV parsing and mapping: `app/services/csv_loader.py`, `app/services/column_mapper.py`,
  `app/services/validation_service.py`, `config/column_aliases.yaml`.
- Numeric/date/sector normalization: `app/services/numeric_parser.py`,
  `app/services/earnings_date_parser.py`, `app/services/sector_taxonomy.py`.
- Core and subsystem CSV exporters: `app/services/export_service.py`,
  `app/services/ranking_result_export.py`, `app/services/market_regime_export_service.py`,
  `app/services/sector_rotation_export_service.py`, `app/services/setup_lifecycle/export_service.py`,
  `app/services/winner_probability/exports.py`, `app/services/ceri/export_service.py`.
- Downstream duplicate-ticker behavior in combined, sector, ranking, setup lifecycle, and winner
  probability services.

Command evidence:

| Command | Result | Notes |
|---|---:|---|
| `uv run pytest tests/test_csv_upload_services.py tests/test_upload_service_v2.py tests/test_dashboard_upload.py tests/test_exports_history.py tests/test_earnings_date_parser.py tests/test_numeric_parser.py tests/test_sector_taxonomy.py tests/test_column_mapping_summary_service.py tests/test_ranking_result_export.py tests/test_market_regime_export_service.py tests/test_sector_rotation_exports.py tests/setup_lifecycle/test_export_service.py tests/winner_probability/test_exports.py tests/ceri/test_ceri_export_service.py tests/ceri/test_ceri_outcome_feature_export.py -q` | Passed | `81 passed in 8.32s` |
| Duplicate-header probe: `Symbol,Symbol,Description` with row `MSFT,AAPL,Microsoft` | Reproduced | Parsed row became `{"Symbol": "AAPL", "Description": "Microsoft"}`; ticker mapped to `AAPL` |
| Semicolon-delimiter probe: `Symbol;Description` | Reproduced | Parsed as one header and failed validation with no ticker |
| Over-wide-row probe: two headers, three fields | Reproduced | Parsed row included `None: ["EXTRA"]` |
| Filename probe: traversal and long names | Reproduced | Traversal reduced to `evil.csv`; 270-character base name remained 274 chars before timestamp prefix |
| Non-seekable upload probe | Reproduced | `_validate_upload_size` raised raw `OSError: not seekable` |
| Formula-export probe | Reproduced | Combined CSV emitted cells beginning with `=`, `+`, and `@` unchanged |

## Current Ingestion Model

- `create_upload_run` requires a `.csv` filename, validates size by seeking the upload object, saves
  the file, inserts an `UploadRun`, parses the saved file, maps columns, validates that at least one
  ticker exists, stores raw rows, then scores fundamentals.
- `load_csv_rows` tries `utf-8-sig`, `utf-8`, then `cp1252`, and returns `dict(row)` from
  `csv.DictReader`.
- `map_csv_rows` builds a union of row keys and maps aliases from `config/column_aliases.yaml`.
- Tickers are stripped and uppercased, but not otherwise identity-resolved during upload.
- Raw JSON stores the parsed `DictReader` row, not a byte-preserving row representation.

Positive coverage:

- BOM and cp1252-style fallback are supported by `load_csv_rows`.
- Happy-path TradingView-style files are mapped and tested.
- Column alias precedence is deterministic for configured aliases.
- Numeric parsing handles common formats, placeholders, NaN, and Infinity diagnostics.
- Earnings-date parsing covers ISO, month-name, US slash, and European dot formats.
- Sector normalization preserves raw sector text and records canonical/mapped/missing/unmapped status.
- Upload size validation resets seekable file pointers.
- Filename traversal is reduced by `Path(filename).name`, and saved filenames include a UUID suffix.
- Core export column order and several subsystem export shapes are tested.

## Findings Register

### PH5-001

Title: CSV exports do not neutralize spreadsheet formulas

Severity: S1 High

Confidence: Confirmed

Affected components: `app/services/export_service.py`, `app/services/ranking_result_export.py`,
`app/services/market_regime_export_service.py`, `app/services/sector_rotation_export_service.py`,
`app/services/setup_lifecycle/export_service.py`, `app/services/winner_probability/exports.py`,
`app/services/ceri/export_service.py`

Evidence: Export writers pass values directly to `csv.DictWriter` (`app/services/export_service.py:319`,
`:569`; `app/services/ranking_result_export.py:77`; `app/services/winner_probability/exports.py:112`;
`app/services/ceri/export_service.py:32`). A probe exported a combined row with ticker
`=HYPERLINK("http://x")`, company `+CMD`, and sector `@sector`; the generated CSV preserved all three
formula-leading cells unchanged.

Reproduction steps: Generate any CSV export containing a string cell beginning with `=`, `+`, `-`, or
`@`, then open it in spreadsheet software.

Expected behavior: Spreadsheet-openable exports escape or prefix formula-leading cells by default
while preserving the original value in JSON exports.

Observed behavior: CSV quoting occurs, but formula-leading values remain formula-like.

Impact: Malicious CSV/provider/debug content can become executable spreadsheet formulas when exported
and opened by a user.

Root cause or likely cause: Each export module has its own minimal `_csv_value`/writer logic and no
shared spreadsheet-safety function.

Recommended remediation: Introduce one shared export-cell sanitizer for CSV outputs. Prefix dangerous
cells with a single quote or another reviewed neutralization convention, including cells that start
with whitespace followed by formula characters.

Acceptance criteria: All CSV exporters use the shared sanitizer; JSON exports remain semantically
unchanged; tests cover `=`, `+`, `-`, `@`, leading whitespace, lists joined into text, and nested JSON
strings.

Regression tests required: Unit tests for every CSV export family plus an invariant test that scans
export modules for direct `csv.DictWriter` calls outside the shared helper.

Owner profile: Backend/data-export engineer

Dependencies: Product decision on exact spreadsheet neutralization convention.

### PH5-002

Title: Duplicate headers and over-wide rows break forensic raw-row preservation

Severity: S1 High

Confidence: Confirmed

Affected components: `app/services/csv_loader.py`, `app/services/column_mapper.py`,
`app/services/upload_service.py`

Evidence: `load_csv_rows` returns `dict(row)` from `csv.DictReader` (`app/services/csv_loader.py:10`,
`:14`). A duplicate-header probe with `Symbol,Symbol,Description` parsed `MSFT,AAPL,Microsoft` as
`{"Symbol": "AAPL", "Description": "Microsoft"}` and mapped ticker `AAPL`, silently losing the first
Symbol value. An over-wide-row probe parsed extra fields under a `None` key.

Reproduction steps: Upload or parse a CSV with repeated header names, or rows with more fields than
headers.

Expected behavior: Duplicate headers and malformed widths fail visibly or are preserved with explicit,
deterministic disambiguation such as `Symbol`, `Symbol.1`.

Observed behavior: Duplicate header values overwrite earlier values, and over-wide rows produce a
non-string raw key.

Impact: Raw evidence is not exact enough for forensic review, and ticker identity can be changed by a
duplicate header without a validation error.

Root cause or likely cause: `csv.DictReader` is used without header uniqueness checks, row-width
checks, `restkey` policy, or duplicate-header normalization.

Recommended remediation: Add a strict loader stage that validates unique normalized headers, rejects
or disambiguates duplicate headers, rejects over-wide/under-wide rows unless explicitly allowed, and
records parse diagnostics.

Acceptance criteria: Duplicate headers and malformed row widths produce deterministic validation
errors; raw preservation can reconstruct all input columns intended by the policy; tests cover BOM,
duplicate headers, extra fields, missing trailing fields, embedded newlines, and blank rows.

Regression tests required: Hostile CSV loader tests plus an upload-route test proving the user sees a
clear failed-run message.

Owner profile: Backend ingestion engineer

Dependencies: Decide whether duplicate headers should fail or be disambiguated.

### PH5-003

Title: Duplicate and ambiguous ticker identity policy is inconsistent downstream

Severity: S1 High

Confidence: Strong

Affected components: upload ingestion, combined decisions, sector rotation, ranking profiles, setup
lifecycle, winner probability

Evidence: Upload stores every ticker row after uppercasing. The run summary counts duplicates
(`app/routers/run_routes.py:1298`), combined and sector services keep the first raw row
(`app/services/combined_decision.py:310`; `app/services/sector_universe_service.py:626`), while
fundamental/technical lookup maps are last-value-wins (`app/services/combined_decision.py:80`, `:83`;
`app/services/ranking_profile_service.py:132`, `:136`; `app/services/setup_lifecycle/source_loader.py:227`).
The SDD and CERI plans discuss exchange/primary-exchange identity, but upload identity only normalizes
to uppercase ticker.

Reproduction steps: Upload two rows with the same ticker but different company/sector/fundamental
values, then refresh combined/ranking/sector/probability outputs.

Expected behavior: Duplicate and exchange-qualified identities have one documented policy that every
downstream subsystem follows.

Observed behavior: Duplicate detection is visible in one UI summary, but different services can select
different rows or scores.

Impact: Research output can blend one raw row with another row's score, or collapse exchange-distinct
symbols into a single uppercase ticker.

Root cause or likely cause: Ticker normalization is local and string-based; there is no canonical
upload identity model or duplicate resolution contract.

Recommended remediation: Define upload identity keys, including whether exchange/currency/security
type are required. Reject duplicates, quarantine them, or deterministically select one row with an
explicit warning that all services consume.

Acceptance criteria: Duplicate rows produce a clear status; all score/ranking/context/probability
services consume the same canonical identity set; tests prove first/last row drift cannot occur.

Regression tests required: Duplicate ticker, case-variant ticker, class/share ticker, exchange-qualified
ticker, and conflicting sector/company fixtures across upload, combined, ranking, sector, setup
lifecycle, winner probability, and exports.

Owner profile: Backend/domain modeling engineer

Dependencies: Product decision on upload ticker identity and exchange handling.

### PH5-004

Title: Upload file handling is not hardened for long names, unusual file objects, and cleanup policy

Severity: S2 Medium

Confidence: Confirmed

Affected components: `app/services/upload_service.py`, `app/settings.py`

Evidence: `_validate_upload_size` assumes `seek`/`tell` support (`app/services/upload_service.py:147`).
A non-seekable probe raised raw `OSError`. `_safe_filename` strips traversal but does not cap length
(`app/services/upload_service.py:170`); a 270-character base name remained 274 characters before the
timestamp/UUID prefix, likely exceeding common filesystem component limits. Files are saved before DB
processing (`app/services/upload_service.py:36`), and Phase 4 already recorded unexpected DB-failure
orphan risk as PH4-005.

Reproduction steps: Pass a non-seekable upload object, a very long filename, or force a DB failure
after `_save_upload`.

Expected behavior: Upload failures return controlled `UploadProcessingError` messages and either
retain artifacts under a documented failed-upload policy or clean them up.

Observed behavior: Unusual file objects and long names can escape the upload error path; cleanup and
retention semantics are not centralized.

Impact: Users can see 500-style failures, and disk inventory can drift from database inventory.

Root cause or likely cause: File IO is treated as a simple pre-DB step rather than a managed artifact
lifecycle.

Recommended remediation: Cap sanitized filename length, catch and wrap file IO errors, use a pending
artifact record or cleanup-on-failure block, and document retention for failed parse artifacts.

Acceptance criteria: Long filenames, reserved names, non-seekable objects, save failures, parse
failures, and DB failures are covered by tests and produce deterministic artifact state.

Regression tests required: Filename traversal, Windows reserved names, long names, collisions,
non-seekable streams, save permission error, parse failure, and DB failure after file save.

Owner profile: Backend/platform engineer

Dependencies: DR-PH4-005 upload artifact retention decision.

### PH5-005

Title: CSV dialect and hostile-file validation is too narrow

Severity: S2 Medium

Confidence: Confirmed

Affected components: `app/services/csv_loader.py`, `app/services/validation_service.py`

Evidence: `load_csv_rows` uses default `csv.DictReader` settings and does not sniff or validate
delimiter/dialect. A semicolon-delimited file parsed as one header and failed later as "ticker column"
missing. `validate_mapped_rows` only checks that at least one mapped row has a ticker
(`app/services/validation_service.py:8`); rows without tickers are silently dropped from persistence
if at least one ticker exists.

Reproduction steps: Upload semicolon-delimited files, tab-delimited files, mostly blank files, files
with many blank rows, or mixed-width rows.

Expected behavior: Common delimiter mistakes fail with targeted messages or are explicitly supported;
row counts and skipped-row counts are visible.

Observed behavior: Dialect anomalies are not distinguished from missing ticker columns, and partial
row drops are not surfaced as first-class diagnostics.

Impact: Users may believe a screener file was accepted while some rows were skipped, or may get vague
errors for correct-but-non-comma files.

Root cause or likely cause: Validation is minimal and occurs after lossy `DictReader` parsing.

Recommended remediation: Add a strict CSV-profile contract: supported delimiters, max columns, max
rows, blank-row policy, skipped-row diagnostics, and clear error messages.

Acceptance criteria: Hostile-file fixtures produce explicit pass/fail outcomes and upload run notes
record skipped/malformed row counts.

Regression tests required: BOM, cp1252, semicolon, tab, quoted embedded newline, duplicate headers,
blank rows, inconsistent width, empty file, header-only file, no ticker column, and mixed valid/invalid
rows.

Owner profile: Backend ingestion engineer

Dependencies: Product decision on whether to support non-comma delimiters.

### PH5-006

Title: Export schema versioning and round-trip guarantees are incomplete

Severity: S2 Medium

Confidence: Strong

Affected components: all CSV/Markdown exports

Evidence: Core exports have stable header constants and tests, but no shared export schema version,
schema registry, or round-trip tests were found. Some exports include model/config hashes, while
others only emit a view-specific column set.

Reproduction steps: Compare CSV headers across releases or attempt to ingest exported CSV back into a
known schema.

Expected behavior: Export consumers can identify schema version and compatible parser expectations.

Observed behavior: Export schemas are implicit in header lists and tests.

Impact: Downstream spreadsheets/scripts can silently break when columns change, and users cannot tell
which export schema produced a file.

Root cause or likely cause: Export writers evolved independently by subsystem.

Recommended remediation: Add export schema versions, a documented schema manifest, and compatibility
tests for stable column order and round-trip expectations.

Acceptance criteria: Every export has a schema id/version and tests that fail on accidental column
changes without an intentional schema update.

Regression tests required: Header snapshot tests, schema-version tests, and parser round-trip tests
for core CSV exports.

Owner profile: Backend/data-export engineer

Dependencies: Release/versioning policy from Phase 20.

## Action Backlog

Immediate:

- Add shared spreadsheet formula neutralization and apply it to every CSV exporter.
- Add strict duplicate-header and row-width validation before mapping.
- Define and enforce duplicate ticker handling for upload runs.

Near-term:

- Add hostile CSV fixtures for encoding, delimiter, quoting, blank rows, malformed numerics,
  NaN/Infinity, duplicate headers, over-wide rows, long filenames, and oversized uploads.
- Add upload artifact lifecycle tests for parse failure, DB failure, and cleanup/retention policy.
- Add skipped-row diagnostics to upload run notes or a structured diagnostics field.

Structural:

- Introduce a canonical upload identity model that can carry exchange/currency/security-type context.
- Centralize CSV export writing behind one schema-aware helper.
- Publish export schema versions and compatibility rules.

## Test Additions Proposal

- Loader unit tests for BOM, cp1252, semicolon/tab files, embedded newlines, duplicate headers,
  inconsistent row widths, header-only files, blank rows, malformed quoting, and very large row counts.
- Upload service tests for path traversal, reserved filenames, long filenames, collisions,
  non-seekable streams, save failures, parse failures, and DB failures after file save.
- Identity integration tests for duplicate ticker rows, case variants, exchange-qualified symbols,
  and conflicting company/sector data across combined, ranking, sector, setup lifecycle, winner
  probability, UI summaries, JSON, and CSV.
- Export tests for formula-leading values in every CSV family.
- Export schema snapshot tests and round-trip tests for core exports.

## Decision Record

- DR-PH5-001: Decide whether duplicate CSV headers fail ingestion or are preserved with generated
  suffixes.
- DR-PH5-002: Decide duplicate ticker policy: reject, quarantine, first row wins, last row wins, or
  exchange-qualified identity.
- DR-PH5-003: Decide spreadsheet formula neutralization convention.
- DR-PH5-004: Decide failed-upload artifact retention versus cleanup.
- DR-PH5-005: Decide supported CSV dialects beyond comma-separated UTF-8/CP1252.

## Scorecard

| Dimension | Rating | Rationale |
|---|---|---|
| Encoding and basic CSV parsing | Amber | BOM/UTF-8/CP1252 covered; dialect and malformed-row handling thin |
| Column mapping | Amber | Alias mapping works; duplicate headers and ambiguous fields are not safe |
| Numeric/date normalization | Green/Amber | Numeric diagnostics and date formats are good; malformed-file context still weak |
| Sector normalization | Green | Raw sector is preserved with canonical/mapped/missing/unmapped status |
| Raw preservation | Red | Duplicate headers and over-wide rows lose or distort original evidence |
| Identity handling | Red | Duplicate ticker and exchange identity policy is inconsistent |
| File handling | Amber/Red | Size/traversal basics exist; long names, non-seekable objects, and cleanup need work |
| Export safety | Red | Formula injection protection is missing across CSV exporters |
| Export schema stability | Amber | Stable headers are tested in places; no schema versioning/round-trip contract |

## Exit Report

Passed checks:

- Focused Phase 5 test suite passed.
- Happy-path TradingView-style upload mapping works.
- BOM and cp1252 fallback are implemented.
- Numeric parser rejects NaN/Infinity as non-values.
- Earnings date and sector normalization have targeted tests.
- Filename traversal is reduced to a basename.

Failed checks:

- Formula-leading CSV cells are not neutralized.
- Duplicate headers can silently alter ticker identity.
- Over-wide rows are not rejected before raw persistence.
- Duplicate ticker identity is not consistently resolved downstream.
- Long filenames and non-seekable file objects are not handled cleanly.

Deferred items:

- Full artifact cleanup policy depends on DR-PH4-005/DR-PH5-004.
- Export schema versioning should align with Phase 20 release governance.

Blockers:

- Phase 5 cannot exit until formula injection, duplicate-header/raw preservation, and duplicate
  identity policy have executable safeguards.

