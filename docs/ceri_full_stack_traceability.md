# CERI Full Stack implementation traceability

This checklist is maintained against `SwingLens_CERI_Full_Stack_SRS.md` and
`SwingLens_CERI_Full_Stack_SDD.md` supplied for the 7 August 2026 release.

| SRS requirements | SDD component / existing code | Required change | Verification |
|---|---|---|---|
| FR-001–005, NFR-001/002 | settings, provider registry, primary placeholder | Add explicit EODHD/SEC registration, independent settings and secret-safe health metadata | provider/config/security tests |
| FR-006–012, FR-021–027 | provider protocol, source records, normalizers, IBKR services | EODHD trends/calendar/news adapter; preserve immutable evidence; keep IBKR price source | offline provider contracts + ingestion tests |
| FR-013–020 | taxonomy, guidance normalizer, manual review | deterministic news classification and conservative SEC guidance extraction primitives | taxonomy/guidance tests |
| FR-028 | normalization, revision/surprise/catalyst services | durable feature rebuild service and real `CERI_REBUILD_FEATURES` handler | feature rebuild integration tests |
| FR-029–033 | opportunity/risk/confidence/snapshot/capture services | rebuild canonical inputs and preserve reproducibility lineage | golden scoring/snapshot tests |
| FR-034–037 | change detector, alert service, processing runs | standalone persisted change rebuild and persisted-change alert rebuild | change/alert idempotency tests |
| FR-038–041 | query service, routes, existing templates | preserve current UI/API and make current-state filters/ops reflect persisted data | route/query tests |
| FR-042–048 | backfill and purge services | retain checkpointed backfill and provider-specific purge policy; add missing execution coverage | restart/purge tests |
| FR-049–052, NFR-008–015 | export policy, observability, config | restricted EODHD exports, deterministic hashes, validation workflow documentation | export/security/validation tests |

Known controlled external dependency: live EODHD and SEC smoke validation requires
operator configuration and must not run in ordinary offline CI.
