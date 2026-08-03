# Changelog

SwingLens uses a Keep-a-Changelog style with SemVer-like release labels. Dates use ISO format.

## Unreleased

### Added

- Repository governance docs, ADR process, route/export inventory checks, and release checklists.

### Changed

- PRs that change routes, exports, scoring contracts, migrations, or model behavior must update the
  matching docs and versioning evidence.

## 0.1.0 - Initial Local MVP

### Added

- Local FastAPI research cockpit with CSV upload, raw row preservation, IB Gateway market-data
  fetches, technical/fundamental scoring, combined decisions, ranking profiles, market regime,
  sector rotation, setup lifecycle, winner probability, and CERI modules.
