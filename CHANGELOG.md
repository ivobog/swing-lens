# Changelog

SwingLens uses a Keep-a-Changelog style with SemVer-like release labels. Dates use ISO format.

## Unreleased

### Added

- Repository governance docs, ADR process, route/export inventory checks, and release checklists.

### Changed

- Fundamental model v2.2 replaces forward-estimate positivity rewards with bounded
  earnings/revenue/cash comparisons, CAPEX sustainability, and weighted textual
  analyst ratings. The 8% outer weight and all other component formulas remain
  unchanged. v2.1 replay and version-specific 79/76-field admission are preserved;
  forecast profitability remains unavailable without compatible reporting periods.
- PRs that change routes, exports, scoring contracts, migrations, or model behavior must update the
  matching docs and versioning evidence.

## 0.1.0 - Initial Local MVP

### Added

- Local FastAPI research cockpit with CSV upload, raw row preservation, IB Gateway market-data
  fetches, technical/fundamental scoring, combined decisions, ranking profiles, market regime,
  sector rotation, setup lifecycle, winner probability, and CERI modules.
