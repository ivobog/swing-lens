# Migration Checklist

- [ ] Migration has the correct `down_revision`
- [ ] Clean PostgreSQL `upgrade head` passes
- [ ] Existing populated database upgrade passes
- [ ] Downgrade or restore plan is documented
- [ ] Data backfill is idempotent and bounded
- [ ] New indexes and constraints have query/performance rationale
- [ ] Backup exists before production-like migration
- [ ] Evidence hashes, purge audits, and immutable source rows remain queryable after migration
