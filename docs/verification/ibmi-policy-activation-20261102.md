# IBMI policy activation verification — 2026-11-02

The approved liquidity and volatility paths were activated locally after native
isolated verification. This document records the verified policy scope and
observed results; it does not enable features on another deployment.

## Policy scope

`technical-to-combined-v2` and `technical-to-ranking-v2` permit certified
Technical evidence only when its sole degradation consists of catalogued,
non-integrity `STRATEGY_RESTRICTION` warnings. Existing identity, temporal,
freshness, history, confidence and quality requirements remain mandatory.
Unknown warnings and mixed degradation remain excluded. New consumer evidence
freezes the exact permission, source identity, catalog and justification.

Producer status remains DEGRADED and every warning is retained. Combined and
Ranking retain bearish restrictions and No new entry. Setup and Winner continue
to enforce their direct Technical v1 policies. Fundamental policy is unchanged.
Existing frozen jobs and v1 decisions retain their original configuration.

Fresh Combined configurations use `preserve-stricter-v1` to preserve stricter
decisions, including Incomplete data, when applying a research-only ceiling.
Older frozen configurations retain their original ceiling behavior.

`native-advisory-only-partial-v1` is a read-only activation assessment. It requires
all 13 pipeline stages to complete, complete admitted Combined/Ranking rows,
certified compatible required IBMI inputs, reconciled arithmetic and exact
allowed advisory/exclusion reasons. Unknown or genuine execution, integrity,
temporal, acquisition, publication or completeness failures block acceptance.
The actual business status remains PARTIAL; `_final_pipeline_status` is unchanged.

Reviewed fixes also cover observation freshness while retaining long volatility
history, frozen IBMI configuration delivery, six-decimal score publication,
CERI configuration comparison and stage handoff, and lifecycle inspection.
IBMI configuration is version 1.0.2; budgets, thresholds and lookbacks are unchanged.

## Verification observed before publication

313 distinct targeted automated cases passed, including PostgreSQL regressions,
negative admission/assessment cases, frozen-policy retention, bearish gates,
Setup/Winner restrictions, source identity and arithmetic. Publication validation
must additionally run against the checkout integrated with current main.

Final isolated native run 53 used real `ib_insync.ib`, fresh liquidity and
volatility collection, normal workflow execution and no certification adapter.
Both collections completed without failures. All 13 stages completed; three
Combined and 15 Ranking rows admitted Fundamental and Technical together.
The actual status was PARTIAL and the scoped assessment accepted it without
blockers. Isolated examples are excluded from production counts below.

Production runs 47–50 validated ten distinct tickers in bounded batches:
AAPL, MSFT, NVDA; COST, ADI, TXN; VRTX, TMO, CACI; and V. Required features were
published before their consumer cutoffs. All required stages completed, with
10 complete Combined rows, 50 complete Ranking rows, 30 liquidity profile
contributions and 10 volatility inputs. All four runs remained PARTIAL and
passed the advisory-only assessment. Winner recorded ten policy exclusions and
zero predictions.

These validations used the completed session **2026-10-30**. Liquidity penalties
were genuinely zero for all ten examples. Volatility premiums were positive for
AAPL (0.25), NVDA (1.5), ADI (0.25), COST (0.25) and VRTX (1.5), and eligible-zero
for the other five. Independent replay reconciled exact retained source edges,
Ranking penalties and CERI's secondary cap of 2 and total cap of 10. Nonzero
liquidity branches were exercised by regressions rather than invented samples.

All 46,676 pre-activation financial envelopes through evidence ID 49989 retained
their full-body and identity digest. Runtime checkout identity, process creation,
ancestry, worker heartbeat and loaded configuration were verified through the
supported lifecycle. Historical reused process IDs were never signalled.

## Deployment and operational limits

Only `IB_MARKET_INTELLIGENCE_ENABLED`, `IB_LIQUIDITY_ENABLED` and
`IB_VOLATILITY_INTELLIGENCE_ENABLED` were enabled in the local deployment.
Short pressure, options activity, scanner, histogram and Flex remain disabled.
Local credentials, environment files, databases and raw operational evidence
are not part of this publication.

Collection remains explicit and must precede consumers for the required
session. The expected session advanced to 2026-11-02 during final verification;
another consumer requires collection/publication for that session. This does
not alter frozen October 30 evidence. The assessment grants no entry authority.

Rollback requires a verified idle boundary, disabling the three collection
flags, withdrawing `tradeability_overlay.enabled` for momentum_swing,
early_rocket and defensive_quality, admitting fresh configuration and using
the supported restart. Master-off alone does not withdraw retained Ranking
inputs. Preserve historical evidence and already-frozen operations.
