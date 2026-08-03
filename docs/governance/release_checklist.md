# Release Checklist

- [ ] `uv sync --frozen --extra dev`
- [ ] `uv run ruff check app tests scripts`
- [ ] `uv run python scripts/docs/check_route_inventory.py`
- [ ] `uv run pytest -q`
- [ ] Focused subsystem tests for touched areas
- [ ] `uv run alembic upgrade head` on clean PostgreSQL
- [ ] Backup created and restore validation passed for production-like environments
- [ ] `/health` and `/ready` pass
- [ ] Route/export inventory reviewed
- [ ] Changelog/release notes updated
- [ ] ADRs updated for major design changes
- [ ] Incident and rollback procedures confirmed for high-risk changes
- [ ] XLSX remains deferred unless a tested workbook implementation is included
