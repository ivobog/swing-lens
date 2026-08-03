## Summary

## Risk Area

- [ ] Scoring/model behavior
- [ ] Database migration or data retention
- [ ] Background job or concurrency
- [ ] Provider/API integration
- [ ] Admin/destructive operation
- [ ] Security/redaction/local-only boundary
- [ ] UI/export/accessibility
- [ ] Documentation only

## Verification

- [ ] `uv run ruff check app tests scripts`
- [ ] `uv run python scripts/docs/check_route_inventory.py`
- [ ] Focused tests:
- [ ] `uv run pytest -q`
- [ ] Alembic check:
- [ ] Manual smoke:

## Versioning and Governance

- [ ] App/version change considered
- [ ] Migration version added or not applicable
- [ ] Engine/model/config/export version bump considered
- [ ] Golden fixtures updated or not applicable
- [ ] ADR updated or not applicable
- [ ] Changelog/release notes updated

## Safety

- [ ] No broker order path added
- [ ] Secrets and local paths redacted
- [ ] Admin/destructive actions protected and audited
- [ ] Rollback/restore path documented for high-risk change
