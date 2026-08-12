# Fiscal periods and PIT

Feature rebuild now iterates EPS and revenue across CURRENT_QUARTER, NEXT_QUARTER, CURRENT_FISCAL_YEAR, and NEXT_FISCAL_YEAR for 7/30/90-day windows. PIT selection uses the full metric/period identity and company/metric-scoped SQL. Retrospective provider baselines carry separate reference and known timestamps and cannot cross a historical cutoff. See [nwe_revision_pairs.json](data/nwe_revision_pairs.json).
