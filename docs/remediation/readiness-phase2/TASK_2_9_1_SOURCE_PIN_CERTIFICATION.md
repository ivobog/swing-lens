# Task 2.9.1 — T14 Source-Pin Certification

## 1. VERDICT

**PASS**

The Phase 2.9 source-pin artifact is current, the new CERI context writer has an
explicit reviewed authority assignment, all focused T14A tests pass, and repeated
generation is byte-deterministic.

This verdict applies only to Task 2.9.1. It does not certify T14D or the complete
Phase 2.9 release.

## 2. ELAPSED TIME

- Started: 2026-10-14 00:45:04 CEST
- Verification completed: 2026-10-14 00:48:08 CEST
- Engineering and verification time: 3 minutes 4 seconds
- Total including this report: under 4 minutes
- Budget: 25 minutes

## 3. ROOT CAUSE

The source-pin artifact predated the final CERI context-authority changes in:

- `app/services/market_calculation_context_service.py`
- `app/services/ceri/feature_certification_workflow.py`

Validation reported exactly those two paths as stale. Semantic inspection also found
that the new `create_pipeline_ceri_certification_context` function performs a real
context insert and retains authority on the pipeline. Its behavior belongs to the
existing `WF_MARKET_CONTEXT_AUTHORITY` family, but that exact source address had not yet
been added to the Phase 2.9 reviewed overlay.

## 4. FILES CHANGED

- `scripts/qa/t14a_phase2_9_semantic_review.py`
  - Added the exact new writer address to `EXISTING_FAMILY_SINKS` as
    `WF_MARKET_CONTEXT_AUTHORITY / DOMAIN_SUPPORTING_STATE`.
  - Added the same address to `ENTRY_ASSIGNMENTS` for
    `WF_MARKET_CONTEXT_AUTHORITY`.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json`
  - Regenerated with the official repository generator.
- `docs/remediation/readiness-phase2/TASK_2_9_1_T14A.xml`
  - Focused pytest JUnit evidence.
- `docs/remediation/readiness-phase2/TASK_2_9_1_SOURCE_PIN_CERTIFICATION.md`
  - This report.

No production application logic was modified by this task. The two affected CERI
production files were pre-existing working-tree changes and were inspected but not
edited. Existing user work was preserved.

## 5. SOURCE-PIN CHANGES

Official generator:

```text
.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins --write
```

Reviewed hash changes:

- `app/services/ceri/feature_certification_workflow.py`
  - old: `264afe044eaf3ec9e71fe718033f17e8411ac7746d9855da74b473e47f44a63f`
  - new: `1788139fdf75ddc3b6f439ce108cf0fc30efc35e78b47403bebd0ddc8e67ea7c`
- `app/services/market_calculation_context_service.py`
  - old: `b2a7e6ebdcf3d6cb05613cabf798ecee8b4cd48a33db1fe0e7290273650e10c3`
  - new: `2860709f19e719dceb004e9e20580019664f38c8c618766d0c227d76199f8b58`
- `scripts/qa/t14a_phase2_9_semantic_review.py`
  - regenerated because the exact new writer mapping was reviewed and added;
  - current: `efb65b1ad71e9127ffa77522dc35147f1b6997df33dc681e77f8a80318b86162`.

Final generated artifact SHA-256:
`E0533A80871FB7E9F60F426384A580B3D894EA14F2D2DBE9EA0ECEF4AA3551C1`.

The historical T14D pin artifact was not changed.

## 6. TEST COMMANDS AND RESULTS

Validation before regeneration:

```text
.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins
```

Result: stale paths were exactly the two expected CERI files.

Focused T14A certification:

```text
.venv\Scripts\python.exe -m pytest tests/test_t14a_mutation_inventory.py -q \
  --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_1_T14A.xml
```

Result: **20 passed, 1 warning in 115.01 seconds**.

Final validation:

```json
{
  "stale": []
}
```

Deterministic regeneration:

- SHA-256 before second generation:
  `E0533A80871FB7E9F60F426384A580B3D894EA14F2D2DBE9EA0ECEF4AA3551C1`
- SHA-256 after second generation:
  `E0533A80871FB7E9F60F426384A580B3D894EA14F2D2DBE9EA0ECEF4AA3551C1`
- Byte-deterministic: **true**

## 7. AUTHORITY/SAFETY VERIFICATION

- The new function creates a distinct CERI certification calculation context and
  retains that identity on its owning pipeline.
- It is explicitly assigned to the existing market-context authority family; no new
  authority family or production behavior was invented.
- Its role is `DOMAIN_SUPPORTING_STATE`, not a canonical financial-result writer.
- The admission workflow remains mapped to `WF_CERI_FEATURE_CERTIFICATION` and the
  feature-source-manifest authority.
- No mutation-authority check, negative enforcement test, ownership requirement, or
  source-identity requirement was weakened.
- No tests were changed.
- No database, migration, provider, pipeline, browser, or E2E process was invoked.
- The dirty working tree and pre-existing changes were preserved.

## 8. REMAINING BLOCKERS

- Six T14D CERI authority-scaling failures remain separate and unresolved.
- The worker heartbeat/fence failure remains separate and unresolved.
- Broader Winner, Phase 4, recovery, browser, and full-regression failures remain as
  recorded in `PHASE2_9_RELEASE_CERTIFICATION_REPORT.md`.

Task 2.9.1 closes only the stale T14A source-pin blocker.

## 9. RECOMMENDED NEXT SINGLE TASK

**Task 2.9.2 — reconcile the six T14D CERI authority-scaling failures.**

Keep that task bounded to the capture/pipeline/material population cases, determine
whether the two additional source-authority reads are required, and repair missing
semantic membership without increasing limits merely to make the tests pass.
