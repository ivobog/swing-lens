# SwingLens Wave C Consolidated Software Review Report

**Review date:** 2026-08-02  
**Review target commit:** `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`  
**Repository:** `ivobog/swing-lens`  
**Overall status:** **Not exit-ready**  
**Recommended operating posture:** Advisory research mode only. Historical recalculation, persisted replay, model promotion, and licensed-data lifecycle operations require additional safeguards.

## 1. Purpose

This report consolidates the following Wave C phase reviews:

1. `phase10_market_regime_sector_rotation.md`
2. `phase11_setup_lifecycle_signal_change.md`
3. `phase12_winner_probability_quant_validation.md`
4. `phase13_catalyst_estimate_revision_intelligence.md`

The consolidation preserves every original finding ID while reorganizing overlapping findings into system-level release gates.

Wave C covers the contextual and intelligence layers that sit above core scoring:

```text
Market regime and candidate-universe breadth
  -> sector rotation context
  -> setup lifecycle snapshots and episodes
  -> winner-probability estimates and model governance
  -> catalyst and estimate-revision intelligence
```

These layers depend heavily on historical timestamps, immutable evidence, correction chains, replay controls, model approval, and licensed-data governance. Their formulas and focused tests are generally strong. The primary risks arise where stored evidence can be replaced, later context can enter an earlier decision, or a governance action can be executed without the policy guarantees implied by the UI or documentation.

## 2. Executive Summary

Wave C contains **18 original findings**:

| Severity | Count |
|---|---:|
| High | 9 |
| Medium | 7 |
| Low | 2 |
| **Total** | **18** |

No finding was formally classified as Critical in the source reports. Several High findings are nevertheless release-blocking because they affect historical truth, model activation, or licensed-data obligations.

The four reviewed subsystems have substantial strengths:

- market regime and sector rotation use explicit, versioned formulas and policy matrices;
- sector taxonomy preserves missing and unmapped states;
- setup lifecycle has deterministic canonicalization, stable event keys, episode uniqueness, alert deduplication, and terminal-state controls;
- winner probability excludes future and immature outcomes from cohort evidence, withholds cold-start probabilities, stores immutable evidence manifests, and uses walk-forward shadow validation;
- CERI has provider gating, source hashes, point-in-time estimate logic, normalization, conflict handling, recursive export redaction, and preview-first purge controls;
- all focused phase suites passed.

The release blockers cluster into five themes:

1. **Historical cutoff violations**  
   Sector rotation and setup lifecycle can attach a global market or sector snapshot later than the historical decision date. CERI accepts historical query modes but does not apply them in the public score-history path. Winner-probability feature capture has timestamp checks but lacks a per-feature availability audit.

2. **Mutable evidence and incomplete correction chains**  
   Market, sector, and setup snapshots are updated or replaced in place. CERI correction fields exist, but changed provider records cannot become a service-created append-only supersession chain.

3. **Governance actions do not fully enforce policy**  
   Setup replay can be persisted through a simple flag without the documented confirmation workflow. Setup purge helpers can delete evidence even while retention configuration disables purge. CERI purge marks an audit as executed without changing data availability.

4. **Probability model promotion is under-governed**  
   The registry can promote an arbitrary algorithm string. Promotion does not require superiority over baselines, confidence intervals, cohort stability, fresh calibration/drift evidence, or a formal human approval record.

5. **Scope and presentation can overstate confidence**  
   Breadth and sector leadership describe the uploaded candidate universe, not the full market. ETF confirmation is optional. Snapshot-level coverage confidence is weaker than row-level confidence. Probability displays omit visible calibration and model lifecycle status.

### Consolidated readiness decision

Wave C is **not exit-ready**. The application should not claim:

- point-in-time reproducibility for every historical context or score-history query;
- immutable historical evidence for market, sector, and setup snapshots;
- complete correction and supersession lineage for provider data;
- statistically approved model promotion;
- operationally effective licensed-data purge;
- full-market breadth when calculations use a screened uploaded universe.

## 3. Phase Evidence Baseline

| Phase | Focused test evidence | Source conclusion |
|---|---|---|
| Phase 10 | `121 passed, 1 warning` | Partially exit-ready |
| Phase 11 | `140 passed, 1 warning` | Partially exit-ready |
| Phase 12 | `119 passed, 1 warning` | Not exit-ready |
| Phase 13 | `135 passed, 1 warning` | Not exit-ready |

The phase suites target different packages, but the figures should still be treated as phase evidence rather than a single deduplicated test total.

## 4. Positive Controls and Strengths

### 4.1 Market regime and sector rotation

- Market-regime scoring is explicit and deterministic.
- Missing SPY produces an unknown, low-confidence, risk-off result.
- QQQ can lower confidence without blocking the SPY-led calculation.
- Stale benchmark data creates warnings and can force a configured risk state.
- Sector scoring is decomposed into technical, profile, candidate-share, setup-density, and risk-control components.
- Sector taxonomy preserves canonical, mapped, missing, and unmapped status.
- Sector confidence exposes insufficient, low, normal, and high states.
- The market-bucket by rotation-state permission matrix is explicit and tested.
- Sorting and tie-breaking are deterministic.
- Dashboard, API, drill-down, CSV, JSON, and Markdown paths share repository/export payloads.

### 4.2 Setup lifecycle and signal change

- The state set, terminal states, and transition precedence are config-validated.
- Canonicalization and primary-episode selection are deterministic.
- Signal-change event keys are stable.
- Active-episode, lifecycle-event, signal-change, alert-rule, and alert-event uniqueness is protected by database constraints.
- Terminal lifecycle states are locked.
- READY/TRIGGERED hysteresis and confirmation persistence reduce flapping.
- Dry-run replay is isolated.
- Persisted replay currently creates a separate replay evaluation version rather than mutating live episodes.
- Alert rules include cooldown and event-key deduplication.
- Acknowledge and dismiss operations change user state rather than lifecycle evidence.

### 4.3 Winner probability

- Production cohort estimates use evidence available before the decision cutoff.
- Current predictions and future evidence are excluded.
- Outcomes must mature before inclusion.
- Reconstructed history is excluded from production evidence.
- Dependent repeated episodes are collapsed.
- Cold-start probabilities and intervals are withheld when evidence is insufficient.
- Estimates persist exact evidence manifests and hashes.
- Shadow training uses chronological walk-forward folds.
- Episode groups are disjoint between train and test.
- Preprocessing is fitted inside each fold.
- Training restricts approved algorithms.
- Log loss, Brier score, ECE, reliability bins, baseline comparisons, and drift metrics are available in services.
- Model lifecycle events and retirement of replaced active models are supported.

### 4.4 CERI

- CERI is disabled by default.
- Manual provider fixtures are safe and local.
- Primary provider credentials come from the environment.
- Live primary-provider fetching is gated until a licensed implementation exists.
- Source records contain provider, terms, content hash, idempotency, retention, quarantine, and correction fields.
- Normalization covers fiscal periods, effective sessions, currencies, scales, estimates, earnings, guidance, surprises, and catalysts.
- Low-level estimate queries distinguish `AS_KNOWN` and `LATEST_CORRECTED`.
- Conflicting provider evidence and manual review flows exist.
- Export redaction recursively masks restricted fields, tokens, paths, SQL-like details, URLs, and raw payloads.
- Purge preview requires explicit scope and confirmation evidence.

These controls make the systems promising. They do not replace the missing cross-layer guarantees below.

# 5. Consolidated Release Gates

## 5.1 Gate A: Enforce One Point-in-Time Cutoff Contract

**Severity:** High  
**Source findings:** `PH10-001`, `PH11-002`, `PH12-004`, `PH13-002`

### Problem

Several subsystems implement timestamps and warnings, but do not consistently enforce the same rule:

> Every source contributing to a decision must have been available at or before that decision’s cutoff.

Specific gaps:

- Sector rotation falls back from a missing run-specific market snapshot to the latest global market snapshot without an `as_of_date` cutoff.
- Setup lifecycle source loading can attach latest global market and sector context before checking whether it is future-dated.
- Winner-probability extraction checks source-row timestamps as a group but does not invoke the feature-schema availability rule for each feature.
- CERI public score-history validates `AS_KNOWN` and `LATEST_CORRECTED`, but returns the same stored-snapshot behavior for both modes.

### Impact

A historical calculation can use context that did not exist at the original decision time. A public API can promise one historical mode while returning another. Model-card evidence cannot prove each individual feature’s availability.

### Required actions

1. Define one cross-subsystem cutoff vocabulary:
   - `data_as_of_date`;
   - `source_available_at`;
   - `decision_cutoff_at`;
   - `training_cutoff_at`;
   - `effective_session`;
   - `correction_visible_at`.
2. Require every context lookup to use:
   ```text
   source_available_at <= decision_cutoff_at
   ```
3. Replace global `latest()` fallbacks with `latest_as_of_or_before(cutoff)`.
4. When no eligible context exists:
   - attach no future context;
   - return explicit missing-context warnings;
   - lower confidence or mark the artifact incomplete.
5. Add first-class cutoff fields to setup snapshots and lifecycle events.
6. Invoke winner feature-schema cutoff validation for every captured feature.
7. Persist a per-feature availability audit or compact audit hash.
8. Implement distinct CERI score-history semantics for `AS_KNOWN` and `LATEST_CORRECTED`, or rename the endpoint to “stored score snapshots” and remove the unsupported mode promise.
9. Add cross-engine historical fixtures where only future context exists.

### Acceptance gate

- Sector and setup calculations never select a context snapshot after their cutoff.
- CERI historical modes produce demonstrably different responses when a later correction exists.
- Every required winner feature has an auditable availability assertion.
- Missing eligible context produces explicit insufficiency rather than silent fallback.
- A repository-wide temporal lineage test verifies all source timestamps are at or before the decision cutoff.

### Owner profile

Data/quant backend engineer.

---

## 5.2 Gate B: Make Analytical Snapshots Append-Only and Revisioned

**Severity:** High  
**Source findings:** `PH10-002`, `PH11-001`

### Problem

Market-regime, sector-rotation, and setup-signal snapshots are updated or replaced in place for the same logical key.

Current behavior provides convenient retry idempotency, but revised source data, code behavior, or upstream rows can overwrite the only stored evidence for a historical calculation.

### Impact

Historical replay and audit can no longer prove what evidence existed at a prior point. A recalculated snapshot can retain the same business identity while its source ids, hashes, warnings, scores, or decisions change.

### Required actions

1. Introduce append-only revision identity using fields such as:
   - logical snapshot key;
   - revision number;
   - input/source hash;
   - source cutoff;
   - calculation version;
   - config hash;
   - supersedes/superseded-by id;
   - recalculation reason;
   - evaluation run id.
2. Keep a separate canonical/latest materialized pointer for efficient UI access.
3. Define retry idempotency by complete source hash and request identity, not only run/ticker/date/config.
4. Preserve prior market and sector rows when recalculation changes results.
5. Preserve prior setup snapshot payloads when source evidence changes.
6. Add immutable export and reconstruction tests.
7. Expose revision lineage in debug and administrative views.

### Acceptance gate

- Reprocessing identical evidence returns the existing revision.
- Reprocessing changed evidence creates a new revision.
- Prior revisions remain queryable and reconstructable.
- No historical analytical snapshot is silently overwritten.
- Exports can identify logical snapshot id, revision id, source hash, and supersession state.

### Owner profile

Backend/database engineer.

---

## 5.3 Gate C: Complete Provider Correction and Supersession Lineage

**Severity:** High  
**Source finding:** `PH13-001`

### Problem

CERI source records contain `supersedes_id` and `correction_type`, but ingestion does not create correction chains. A changed payload for the same provider record receives a new content hash and idempotency key, then conflicts with the uniqueness rule on provider, dataset, and provider record id.

### Impact

Late corrections cannot become append-only auditable provider revisions through the normal service. This weakens:

- `LATEST_CORRECTED`;
- conflict resolution;
- manual review;
- score reconstruction;
- purge scoping;
- provider-quality statistics;
- export lineage.

### Required actions

1. Look up the current provider record by provider, dataset, and provider record identity.
2. If the content hash is identical, deduplicate.
3. If content differs:
   - create a distinct source revision;
   - link `supersedes_id`;
   - set `correction_type`;
   - increment correction counts;
   - preserve both original and corrected payload lineage.
4. Replace the current uniqueness model with either:
   - provider record identity plus revision number; or
   - append-only source revision rows plus a current-pointer table.
5. Define visibility rules by correction timestamp for `AS_KNOWN` and `LATEST_CORRECTED`.
6. Add database-backed tests covering:
   - exact duplicate;
   - changed same-id record;
   - multi-step correction chain;
   - corrected normalized estimate;
   - historical query behavior;
   - conflict review;
   - export lineage;
   - purge scope.

### Acceptance gate

- Changed provider content can be ingested without overwriting or uniqueness failure.
- Original and corrected records remain auditable.
- Historical modes select the correct revision by visibility time.
- Derived features and scores identify the source revision chain.

### Owner profile

Backend/data-lineage engineer.

---

## 5.4 Gate D: Enforce Replay, Retention, and Purge Policy at the Service Boundary

**Severity:** High/Medium  
**Source findings:** `PH11-003`, `PH11-004`, `PH13-003`

### Problem

Three governance mismatches exist:

1. Setup replay can be persisted with `persist=true` without the documented preview/confirmation/reason workflow.
2. Setup repository purge methods can delete evidence even while configuration explicitly disables purge and retains evidence indefinitely.
3. CERI purge execution marks the audit as `EXECUTED` but does not delete, tombstone, redact, quarantine, or invalidate affected data.

### Impact

The software can persist replay state, delete lifecycle evidence, or claim purge completion without consistently enforcing the declared policy. This undermines audit trust and may violate licensed-data obligations.

### Required actions

#### Replay

- Keep dry-run replay as the default and simplest route.
- Require preview id/token, requester, reason, and explicit confirmation for persisted replay.
- Separate replay persistence from promotion to live-authoritative state.
- Add a formal promotion endpoint and audit event if promotion is supported.
- Validate `promotion_requires_confirmation` in configuration.

#### Setup retention and purge

- Move deletion behind a policy-aware service.
- Check:
  - `purge_enabled`;
  - preview requirement;
  - confirmation requirement;
  - audit requirement;
  - requester authorization.
- Make repository deletion primitives private.
- Reject purge while retention policy disables it.

#### CERI licensed-data purge

- Decide the authoritative lifecycle action:
  - physical delete;
  - tombstone;
  - redaction;
  - quarantine;
  - or explicitly named audit-only attestation.
- If the operation is named purge, execute the chosen action transactionally.
- Record all invalidated derivative ids.
- Block queries and exports from serving invalidated derivatives.
- Enqueue required rebuild jobs and link them to the purge audit.
- Preserve a non-sensitive immutable audit record.

### Acceptance gate

- Persisted replay cannot occur through one unconfirmed flag.
- Setup purge is impossible while configuration disables it.
- CERI purge execution materially changes data availability according to the documented policy.
- Rebuild obligations are tracked to completion.
- Retry and rollback tests prove the lifecycle operation is safe and idempotent.

### Owner profile

Backend/data-governance engineer.

---

## 5.5 Gate E: Strengthen Winner-Model Registration and Promotion Governance

**Severity:** High  
**Source findings:** `PH12-001`, `PH12-002`

### Problem

Training code blocks unapproved algorithms, but model registration and promotion accept arbitrary algorithm strings. Existing promotion gates validate artifact shape, sample count, ECE, coverage, and existing critical drift breaches, but do not require:

- model improvement over global and cohort baselines;
- Brier and log-loss margins;
- confidence intervals;
- class-balance constraints;
- independent episode counts;
- cohort stability;
- persisted calibration bins used for approval;
- fresh drift evidence;
- formal human approval;
- rollback readiness.

### Impact

A manually inserted or imported candidate can become active without passing the quantitative standards implied by the model-development code. A model can be promoted despite no demonstrated improvement over simpler estimates.

### Required actions

1. Create one approved serving-algorithm registry.
2. Enforce the allow-list in:
   - training;
   - registration;
   - import/backfill;
   - promotion;
   - activation.
3. Persist a promotion gate report containing:
   - algorithm and artifact identity;
   - artifact hash recomputation;
   - feature schema and order;
   - dependency versions;
   - training cutoff;
   - independent episode count;
   - effective sample size;
   - class balance;
   - coverage;
   - log loss;
   - Brier score;
   - ECE;
   - calibration bins;
   - global and cohort baseline metrics;
   - confidence intervals;
   - segment/cohort stability;
   - drift freshness;
   - all pass/fail reasons.
4. Require model improvement over approved baselines by configured margins.
5. Treat missing or stale calibration/drift evidence as a blocker.
6. Add embargo sessions when overlapping ticker/setup outcomes could leak across folds.
7. Require a formal approval record with:
   - reviewer identity;
   - timestamp;
   - model-card hash;
   - gate-report hash;
   - reason;
   - rollback/fallback plan.
8. Keep non-passing candidates in shadow or rejected status.

### Acceptance gate

- An unapproved algorithm cannot be registered or promoted.
- Promotion requires complete, fresh, persisted evidence.
- A candidate cannot become active without baseline superiority or an explicit documented policy allowing cohort-only serving.
- Human approval and rollback evidence are mandatory.
- Promotion is fully reproducible from stored training and validation artifacts.

### Owner profile

Quant/model-governance engineer.

---

## 5.6 Gate F: Expose Probability Provenance, Calibration, and Lifecycle Status

**Severity:** Medium  
**Source finding:** `PH12-003`

### Problem

Probability UI and API output include probability, interval, evidence grade, sample size, cutoff, and manifest information, but do not visibly show calibration state or model lifecycle status/version next to the probability.

### Impact

A user cannot immediately distinguish:

- cohort baseline;
- active calibrated model;
- stale calibration;
- insufficient calibration;
- shadow-only model;
- retired model;
- unapproved candidate.

The number appears more authoritative than its operational status warrants.

### Required actions

1. Add the following to estimate payloads:
   - `model_key`;
   - `model_version_label`;
   - `model_status`;
   - `algorithm`;
   - `calibration_status`;
   - `calibration_calculated_at`;
   - `calibration_freshness`;
   - `drift_status`;
   - `source_kind`.
2. Display these beside probability and interval on run and ticker pages.
3. Include them in CSV and JSON exports.
4. Use explicit labels such as:
   - `Cohort baseline`;
   - `Active calibrated model`;
   - `Calibration stale`;
   - `Insufficient calibration`;
   - `Shadow model, not serving`.
5. Link each estimate to its model card, evidence manifest, and reproduction result.
6. Add reliability-bin and confidence-interval context where enough data exists.

### Acceptance gate

Every displayed probability communicates its source, serving status, model version, sample context, and calibration freshness without requiring users to inspect a secondary debug block.

### Owner profile

Backend/UI and quant engineer.

---

## 5.7 Gate G: Label Candidate-Universe Metrics and Define Confirmation Requirements

**Severity:** Medium/Low  
**Source findings:** `PH10-003`, `PH10-004`, `PH10-005`

### Problem

Market participation and sector rotation use the uploaded run universe. This is reproducible, but it is not a fixed historical market universe. ETF corroboration is optional and disabled by default. Row-level confidence warnings do not fully roll up into one snapshot-level confidence decision.

### Impact

A screened candidate list can make participation and sector leadership look stronger than the total market. Missing failed or delisted names introduces selection and survivorship bias. A “leading sector” can appear without ETF confirmation or with weak aggregate coverage.

### Required actions

1. Rename relevant outputs:
   - `candidate_universe_breadth`;
   - `candidate_universe_sector_rotation`;
   - `universe_only`.
2. State clearly that the calculation does not represent:
   - S&P 500 breadth;
   - Nasdaq 100 breadth;
   - Russell breadth;
   - full-market constituent history.
3. Add optional fixed-universe and historical-constituent inputs if true market breadth is a product goal.
4. Decide whether ETF confirmation is:
   - mandatory for production-grade rotation;
   - optional contextual evidence;
   - or a separate confidence tier.
5. If mandatory, make missing ETF evidence produce insufficient status.
6. Add snapshot-level coverage fields:
   - total tickers;
   - known-sector share;
   - technical coverage;
   - ranking-profile coverage;
   - ETF coverage and freshness;
   - unknown/unmapped share;
   - overall confidence.
7. Suppress or qualify “leading/weakest” summary labels when aggregate confidence is low.

### Acceptance gate

- No candidate-universe measure is presented as full-market breadth.
- ETF confirmation policy is explicit.
- Snapshot-level confidence incorporates aggregate coverage.
- Exports and dashboards expose universe definition and confidence inputs.

### Owner profile

Quant/product engineer.

---

## 5.8 Gate H: Close CERI Redaction Outside Formal Exports

**Severity:** Medium  
**Source finding:** `PH13-004`

### Problem

Formal CERI exports use robust redaction. The job-status endpoint returns raw job payload, result, and error fields directly.

Potential values include:

- provider paths;
- source URLs;
- raw payload fragments;
- bearer tokens;
- SQL-like text;
- purge confirmation tokens;
- operational metadata.

### Impact

Restricted values can leave approved service boundaries through an operational API even though exports are safe.

### Required actions

1. Pass job payload, result, and error text through the same redaction policy used by exports.
2. Avoid storing raw confirmation tokens in job payloads.
3. Store token hashes or server-side one-time references.
4. Classify operational fields as:
   - public;
   - local-admin;
   - restricted;
   - never-return.
5. Add route tests for nested sensitive keys and sensitive text fragments.
6. Ensure logs and operations UI use the same redaction rules.

### Acceptance gate

No CERI route, job response, export, log, or operations view returns restricted content outside the approved policy.

### Owner profile

Backend/security engineer.

---

## 5.9 Gate I: Add Property-Based Lifecycle Invariant Testing

**Severity:** Low  
**Source finding:** `PH11-005`

### Problem

Setup lifecycle has broad example-based coverage but no generated transition-sequence tests.

### Impact

Edge cases may remain around:

- repeated dates;
- out-of-order evidence;
- missing observations;
- duplicate evidence;
- terminal-state reopen attempts;
- family switches;
- observation gaps;
- replay versus live state;
- revised source evidence.

### Required actions

Add property-based tests generating snapshots and gaps while enforcing:

- one active episode per ticker/timeframe/family;
- terminal episodes never reopen;
- current as-of date never moves backward;
- state age never decreases except on state transition/reset;
- duplicate source evidence creates no duplicate event;
- idempotent replay creates no live mutation;
- revised evidence creates a new snapshot revision;
- alert deduplication survives retry and concurrency;
- canonical choice is deterministic.

### Acceptance gate

Generated state sequences cannot violate lifecycle invariants, and discovered counterexamples become permanent regression fixtures.

### Owner profile

Backend/test engineer.

# 6. Cross-Phase Risk Register

| Consolidated risk | Original findings | Release effect |
|---|---|---|
| Later context can enter historical decisions | PH10-001, PH11-002 | Blocks point-in-time certification |
| Per-feature cutoff evidence is incomplete | PH12-004 | Blocks model-card leakage proof |
| Public historical mode is semantically inert | PH13-002 | Blocks CERI historical API certification |
| Snapshots are mutable rather than revisioned | PH10-002, PH11-001 | Blocks historical reconstruction |
| Provider corrections lack supersession chain | PH13-001 | Blocks latest-corrected lineage |
| Persisted replay lacks confirmation workflow | PH11-003 | Blocks replay governance exit |
| Retention-disabled purge can still execute internally | PH11-004 | Blocks lifecycle retention guarantee |
| CERI purge is audit-only | PH13-003 | Blocks licensed-data lifecycle exit |
| Registry accepts unapproved algorithms | PH12-001 | Blocks model activation |
| Promotion gates do not establish quantitative superiority | PH12-002 | Blocks model promotion |
| Probability provenance/calibration is not visible | PH12-003 | Blocks safe presentation exit |
| Candidate-universe metrics may be overstated | PH10-003 | Requires labeling and model-card limitation |
| ETF confirmation is optional | PH10-004 | Requires production policy decision |
| Aggregate sector confidence is weak | PH10-005 | Requires snapshot-level confidence |
| CERI operational endpoint bypasses redaction | PH13-004 | Blocks restricted-boundary closure |
| Lifecycle lacks property-based invariant tests | PH11-005 | Quality improvement, not primary blocker |

# 7. Recommended Remediation Program

## Stage 0: Immediate Operating Restrictions

Until the release gates are closed:

- do not recalculate historical sector or setup artifacts using unrestricted global-latest context;
- treat historical market, sector, and setup rows as latest materializations, not immutable audit records;
- keep persisted replay disabled except for controlled development testing;
- keep setup purge inaccessible;
- do not represent CERI purge as completed data removal;
- do not promote new probability models;
- serve cohort probabilities only with explicit candidate-universe and calibration caveats;
- identify sector outputs as candidate-universe metrics;
- keep CERI operational endpoints local-admin only and avoid placing secrets or raw tokens in job payloads.

## Stage 1: Historical Truth and Lineage

1. Implement as-of-safe context lookups.
2. Add decision/source cutoff fields.
3. Convert market, sector, and setup snapshots to append-only revisions.
4. Implement CERI source correction chains.
5. Apply historical modes in the public CERI score-history API.
6. Add cross-engine point-in-time fixtures.

## Stage 2: Governance Operations

1. Add persisted replay preview and confirmation.
2. Put setup purge behind retention policy.
3. Implement real CERI licensed-data lifecycle semantics.
4. Track derivative invalidation and rebuild.
5. Add transactional rollback and idempotency tests.

## Stage 3: Winner-Probability Approval

1. Enforce algorithm allow-list in registry and promotion.
2. Create a persisted quantitative gate report.
3. Require baseline superiority and confidence intervals.
4. Add cohort stability and embargo rules.
5. Require fresh calibration and drift.
6. Add formal human approval and rollback plan.
7. Expose model/calibration status with every probability.

## Stage 4: Scope, Confidence, and Security

1. Rename breadth and rotation as candidate-universe metrics.
2. Decide ETF confirmation requirements.
3. Add snapshot-level sector confidence.
4. Redact all CERI job-status fields.
5. Add lifecycle property-based tests.
6. Add model cards and operational runbooks.

# 8. Master Verification Plan

## 8.1 Point-in-time context tests

- Historical sector run with only a later global market snapshot.
- Historical setup snapshot with later market and sector snapshots.
- Missing eligible context produces missing warning, not future fallback.
- Same-day timestamps around market close.
- Timezone-aware source availability.
- CERI correction arriving after the requested as-of timestamp.
- Distinct `AS_KNOWN` and `LATEST_CORRECTED` score-history output.
- Winner feature with source timestamp one microsecond after cutoff.
- Optional contextual feature after cutoff becomes null/warning.
- Required feature after cutoff fails capture.

## 8.2 Snapshot immutability tests

For market, sector, and setup:

- same logical request plus identical source hash returns same revision;
- same logical request plus changed source hash creates new revision;
- prior payload remains queryable;
- latest pointer moves atomically;
- exports identify revision lineage;
- concurrent recalculation cannot overwrite a prior revision;
- rollback leaves prior canonical revision intact.

## 8.3 CERI correction tests

- exact duplicate source record;
- changed content with same provider record id;
- multi-step correction chain;
- corrected normalized estimate;
- corrected catalyst revision;
- original visibility under `AS_KNOWN`;
- correction visibility under `LATEST_CORRECTED`;
- conflict/manual-review lineage;
- correction-aware export;
- correction-aware purge scope.

## 8.4 Replay and purge tests

### Setup replay

- dry run creates no persisted evaluation;
- persist without preview rejected;
- persist without confirmation rejected;
- persist without reason/requester rejected;
- confirmed replay creates parallel replay evidence only;
- promotion requires a separate audited action;
- replay never mutates live episodes or alerts.

### Setup purge

- purge disabled by config rejects all execution paths;
- repository helper cannot bypass policy;
- preview and confirmation are mandatory when enabled;
- transaction rollback preserves all evidence.

### CERI purge

- preview count and token hash;
- incorrect token rejection;
- chosen lifecycle action changes source availability;
- derivatives become invalidated;
- stale query/export blocked;
- rebuild job linked;
- completed rebuild excludes purged evidence;
- audit remains non-sensitive and immutable.

## 8.5 Probability promotion tests

- unapproved algorithm registration rejected;
- unapproved algorithm promotion rejected;
- missing Brier/log-loss rejected;
- no baseline improvement remains shadow;
- stale calibration rejected;
- absent drift rejected;
- breached segment rejected;
- inadequate independent episodes rejected;
- class imbalance outside policy rejected;
- missing model card rejected;
- missing human approval rejected;
- missing rollback plan rejected;
- passing candidate activation retires prior active model;
- gate report and artifact hashes reproduce.

## 8.6 Probability presentation tests

Every estimate surface must show:

- source kind;
- model key/version;
- model lifecycle status;
- algorithm;
- calibration status and timestamp;
- drift status;
- raw sample size;
- effective sample size;
- interval;
- training cutoff;
- candidate-universe limitation;
- evidence manifest link.

## 8.7 Candidate-universe and sector confidence tests

- screened universe versus unscreened universe labeling;
- unknown/unmapped sector share;
- all technical scores missing;
- ranking-profile coverage missing;
- ETF disabled;
- ETF enabled but stale;
- ETF enabled but absent;
- low aggregate coverage suppresses “leading” summary;
- fixed-universe mode, if implemented;
- historical constituent changes, if implemented.

## 8.8 Redaction tests

For CERI job status, logs, and operations UI:

- nested `authorization`;
- `api_key`;
- `provider_secret`;
- bearer token in error text;
- Windows, macOS, and Linux paths;
- SQL fragments;
- source URL;
- raw payload;
- purge confirmation token;
- nested lists and objects.

## 8.9 Lifecycle property tests

Generate sequences containing:

- repeated dates;
- decreasing dates;
- duplicate snapshots;
- revised source hashes;
- missing sessions;
- long observation gaps;
- state regressions;
- terminal state plus new strong evidence;
- family switch;
- replay alongside live processing;
- duplicate alert retries.

# 9. Required Decision Records

| Decision ID | Required decision |
|---|---|
| DR-C-001 | What timestamp is the authoritative decision cutoff for each Wave C artifact? |
| DR-C-002 | When is market, sector, provider, and feature evidence considered available? |
| DR-C-003 | What happens when no context exists at or before cutoff? |
| DR-C-004 | Are market, sector, and setup snapshots immutable evidence or latest materializations? |
| DR-C-005 | What revision and supersession model is used for analytical snapshots? |
| DR-C-006 | What correction identity is used for provider records? |
| DR-C-007 | What exact semantics distinguish CERI `AS_KNOWN` and `LATEST_CORRECTED` score history? |
| DR-C-008 | What confirmation workflow is required for persisted replay? |
| DR-C-009 | Can replay output ever become live-authoritative, and how is promotion audited? |
| DR-C-010 | Is setup lifecycle evidence purgeable? |
| DR-C-011 | What does CERI “purge” mean: delete, tombstone, redact, quarantine, or audit-only? |
| DR-C-012 | How are invalidated derivatives blocked and rebuilt after purge? |
| DR-C-013 | Which winner-probability algorithms are approved for serving? |
| DR-C-014 | What baseline-improvement margins are required for promotion? |
| DR-C-015 | What sample, class-balance, confidence-interval, and cohort-stability gates apply? |
| DR-C-016 | What calibration and drift freshness windows apply? |
| DR-C-017 | What constitutes formal human model approval? |
| DR-C-018 | What fallback or rollback model must exist before promotion? |
| DR-C-019 | Is probability a candidate-universe probability or intended to generalize further? |
| DR-C-020 | Are sector breadth and rotation candidate-universe or fixed-universe products? |
| DR-C-021 | Is ETF confirmation mandatory for production-grade sector rotation? |
| DR-C-022 | What aggregate sector coverage is required before showing leadership labels? |
| DR-C-023 | Which CERI operational fields may be returned through job APIs? |
| DR-C-024 | Which lifecycle invariants must be enforced by code, database constraints, and property tests? |

# 10. Wave C Exit Criteria

Wave C can close only when all of the following are satisfied.

## Historical semantics

- Every source is selected as of or before the decision cutoff.
- No future global market or sector context is attached.
- Winner features have per-feature availability audit evidence.
- CERI public historical modes are implemented and tested.
- Historical query metadata includes mode, cutoff, correction policy, and evidence hash.

## Immutable evidence and corrections

- Market, sector, and setup snapshots preserve append-only revisions.
- Recalculation with revised evidence cannot overwrite the previous artifact.
- CERI corrections create auditable supersession chains.
- Original and corrected evidence remain queryable according to historical mode.

## Replay, retention, and purge

- Persisted replay requires preview, confirmation, requester, reason, and audit metadata.
- Replay promotion, if supported, is a separate explicit operation.
- Setup purge cannot bypass retention configuration.
- CERI purge performs the declared lifecycle action.
- Invalidated derivatives cannot be served before rebuild.
- Purge and rebuild are transactionally and operationally traceable.

## Winner-probability governance

- Algorithm allow-list is enforced in every registry path.
- Promotion requires a complete persisted gate report.
- Baseline superiority, calibration, drift, sample sufficiency, class balance, and cohort stability are required.
- Formal human approval and rollback plan are recorded.
- Probability output visibly includes source, model version/status, calibration, drift, sample context, and cutoff.

## Universe and confidence

- Candidate-universe metrics are labeled accurately.
- ETF confirmation policy is explicit.
- Snapshot-level coverage confidence is calculated and displayed.
- Low aggregate confidence constrains leadership summaries.
- Model cards disclose selection and survivorship limitations.

## Restricted-data boundaries

- CERI job APIs, logs, and operations views apply the approved redaction policy.
- Raw confirmation tokens are not persisted or returned.

## Lifecycle robustness

- Property-based tests cover state and episode invariants.
- Revised evidence creates a new immutable revision.
- Replay and duplicate processing cannot mutate live state unexpectedly.

# 11. Original Finding Traceability

| Original ID | Severity | Consolidated section |
|---|---|---|
| PH10-001 | High | 5.1 Point-in-Time Cutoff |
| PH10-002 | High | 5.2 Immutable Snapshot Revisions |
| PH10-003 | Medium | 5.7 Candidate-Universe Scope |
| PH10-004 | Medium | 5.7 ETF Confirmation |
| PH10-005 | Low | 5.7 Snapshot-Level Confidence |
| PH11-001 | High | 5.2 Immutable Setup Evidence |
| PH11-002 | High | 5.1 Point-in-Time Context |
| PH11-003 | Medium | 5.4 Replay Governance |
| PH11-004 | Medium | 5.4 Retention and Purge Policy |
| PH11-005 | Low | 5.9 Property-Based Lifecycle Tests |
| PH12-001 | High | 5.5 Algorithm Governance |
| PH12-002 | High | 5.5 Promotion Gates |
| PH12-003 | Medium | 5.6 Probability Presentation |
| PH12-004 | Medium | 5.1 Per-Feature Cutoff Audit |
| PH13-001 | High | 5.3 Provider Correction Lineage |
| PH13-002 | High | 5.1 CERI Historical Semantics |
| PH13-003 | High | 5.4 Licensed-Data Purge |
| PH13-004 | Medium | 5.8 CERI Redaction Boundaries |

# 12. Phase-Level Status Summary

| Phase | Status | Principal conclusion |
|---|---|---|
| Phase 10 | Partially exit-ready | Deterministic formulas and policy matrix are strong, but future global context, mutable snapshots, universe scope, and aggregate confidence remain open |
| Phase 11 | Partially exit-ready | Lifecycle architecture and idempotency controls are strong, but setup evidence is mutable, context can be future-dated, and replay/purge policy needs enforcement |
| Phase 12 | Not exit-ready | Leakage controls and shadow validation are promising, but model registration, promotion gates, per-feature audits, and presentation are incomplete |
| Phase 13 | Not exit-ready | Provider controls, normalization, PIT primitives, and redaction are strong, but correction chains, public history semantics, purge execution, and job-status redaction are incomplete |

# 13. Final Assessment

Wave C reveals a consistent architectural pattern.

The individual engines are not flimsy. They have explicit formulas, configuration validation, rich schemas, stable keys, focused tests, and thoughtful conservative behavior. The remaining risk lies in the transformation from a live calculation into a historical claim.

A market regime becomes a historical context.  
A setup snapshot becomes evidence for a lifecycle event.  
A cohort probability becomes a model-governed prediction.  
A provider correction becomes a revised historical fact.  
A purge button becomes a claim about data no longer being available.

Those transitions require a stronger contract than “the latest row is correct.”

The highest-value remediation sequence is:

1. enforce one point-in-time cutoff contract;
2. make analytical evidence append-only;
3. implement provider correction chains;
4. enforce replay, retention, and purge policy at service boundaries;
5. harden model registration and promotion;
6. expose model and calibration state with every probability;
7. label universe scope and aggregate confidence accurately;
8. extend redaction and lifecycle invariant testing.

Closing those gates will turn Wave C from a collection of sophisticated analytical features into an auditable intelligence layer whose historical and governance claims are backed by durable evidence.
