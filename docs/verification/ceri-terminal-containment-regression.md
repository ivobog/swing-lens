# Safe PARTIAL CERI terminal containment regression

CERI terminal reconciliation consults the existing shared pipeline outcome policy
for PARTIAL owners without requiring a truthy `partial_scope`. A certified safe
full-scope owner must retain its evidence when the scope is NULL or absent.
The existing CERI-failure guard and integrity protections remain authoritative.

The focused regressions exercise NULL, absent and explicit partial scopes;
integrity hazards; missing, malformed and wrong-version outcome summaries;
CERI failures; FAILED, CANCELLED, BLOCKED and COMPLETED owners; independent
exclusions; and repeated reconciliation.

Synthetic PostgreSQL cases use the real terminal transition and verify unchanged snapshot
identities, timestamps and seals, retained independent exclusions, preserved
projections and idempotent reconciliation. Existing ownership regressions remain
in place, including unresolved and competing pipeline bindings.

Validation selects these modules:

```text
tests/ceri/test_pipeline_terminal_containment.py
tests/ceri/test_ceri_evidence_quarantine.py
tests/test_pipeline_partial_results.py
tests/integration/test_ceri_run_isolation_remediation.py
```

Validation on 2026-11-17: **40 passed**. Ruff and Git whitespace checks passed.

No production policy, contract, schema, configuration or ownership behavior is
changed by this regression-only publication. Historical production restoration
and deployment remain separate operations requiring explicit authorization.
