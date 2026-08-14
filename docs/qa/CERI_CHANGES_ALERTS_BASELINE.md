# CERI Changes and Alerts Baseline

Captured 2026-08-14 before semantic remediation. All database queries were executed in a read-only transaction.

## Repository and schema

- Branch: `codex/ceri-run101-remediation`
- HEAD: `3704430933cee6c3106bfdceb4cc2409af8a6989` (`Remediate CERI Run 104 GUI semantics`)
- Worktree: dirty before this remediation. Existing Run 104 controlled-replay work and verification archives were preserved.
- Alembic head/current: `0044_ceri_controlled_replays`
- Baseline test command: `pytest -q tests/ceri`
- Baseline result: **328 passed**, one warning, 26.80 seconds.

## Stored distributions

There were **8,632** `ceri_change_events`.

| Change type | Count |
|---|---:|
| NEW_CATALYST | 5,665 |
| OPPORTUNITY_UPGRADED | 1,407 |
| DATA_REFRESHED | 411 |
| CATALYST_UPDATED | 309 |
| BECAME_RATED | 179 |
| RISK_DEESCALATED | 176 |
| RISK_ESCALATED | 167 |
| BECAME_UNRATED | 138 |
| GUIDANCE_RAISED | 101 |
| GUIDANCE_LOWERED | 56 |
| CATALYST_CONFIRMED | 23 |

Severity was `NOTABLE` for 7,992 rows and `RISK` for 640. This was the first evidence that importance and signal class were conflated.

There were **723** alerts: 722 `OPPORTUNITY_UPGRADED` and one `RISK_ESCALATED`; every alert was `UNREAD`. There were zero missing/orphan change references, zero missing rules, zero duplicate change dedup keys, and zero duplicate alert event keys. All 722 opportunity alerts stored `cooldown_scope=event_revision` even though their actual identity was a score-snapshot transition.

## Reproduced defects

- `BECAME_RATED` appeared under Other because UI grouping duplicated a partial mapping and omitted both `BECAME_RATED` and `BECAME_UNRATED`.
- AEIS displayed `From 2362 to 2970`; those values are score-snapshot primary keys. The business transition is `Unrated -> 9.42 Positive`, coverage `0% -> 70%`, confidence `Insufficient -> Normal`.
- Catalyst changes displayed `N/A -> N/A` because event changes have no score-snapshot pair and the template rendered foreign keys unconditionally.
- AIZ revisions 5518/5519 and STX revisions 5539-5542 each generated a `NEW_CATALYST` change despite having no trader-meaningful materiality.
- Opportunity alerts used the misleading evidence label `cooldown_scope=event_revision`.

## Baseline interpretation

The screen defects were symptoms. Stored changes lacked an explicit comparison-eligibility state, semantic previous/current DTOs, a single exhaustive group mapping, separate importance/signal dimensions, and a historical validity classification. The raw baseline evidence is retained in `.qa_work/ceri_changes_alerts_baseline.json`.
