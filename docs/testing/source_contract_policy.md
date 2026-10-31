# Source contracts and test review

The ten representative contracts live in the existing
`config/certification_registry.json` under `source_contracts`. This is a bounded
review map, not a complete test catalog. Domain maintainers are accountable roles;
these labels do not claim that a named person accepted a review. A02 checks the
map; A16 alone aggregates actual release evidence; A04 alone issues its pipeline
certificate. The approved core profile retains all nine lane kinds with curated selectors. Specialized obligations keep explicit affected/scheduled triggers and never become passing through deferral.

## Coverage words have different meanings

- `STATIC_INTENT`: reviewed source symbols, test selectors, assertions, tier and
  limits. Imports, AST matches and existing assert statements establish only this.
- `MEASURED_EXECUTION`: an identified execution reached a reviewed production
  source, with timestamp, source/test/environment/selection identities and
  hash-verifiable execution artifacts. A test PASS by itself is insufficient.
- `ASSERTED_CONTRACT`: that execution also reached the meaningful assertion.
- `FAULT_SENSITIVE`: a controlled violation reached and failed the intended
  assertion or certification gate. An unrelated import/setup failure is not proof.

Tracked mapping entries declare only static intent. Explicit execution
observations are reviewed separately with `assess_contract_observation`; the
function validates their identity, fidelity and evidence requirements and returns
no release verdict. It does not generate tracing data, authenticate a producer or
replace the original proof. Retained A07/A08/A11/A14 traces and gate records keep
their exact test IDs and limitations. Missing data means NOT_VERIFIED/UNKNOWN.
An editable observation or checksum is not independent attestation.

## Change impact, without executing tests

Use the existing A02 checker:

```text
python scripts/qa/check_certification_registry.py --check --changed-file app/services/ib_fetch_executor.py
python scripts/qa/check_certification_registry.py --check --diff-file changed-paths.txt
python scripts/qa/check_certification_registry.py --check --diff-base <commit-or-ref> --impact-output impact.json
```

The diff-file format is UTF-8, one repository path per line or a normal Git unified
diff with `--- a/` and `+++ b/` headers. The Git mode includes
working changes and untracked source; renamed/deleted paths are impact inputs,
not silently ignored missing files. Plans contain directly relevant fast tests,
native/browser selectors, accountable domains, all mandatory A16 lanes, original
conditional extended-scale lanes and separate manual live-provider obligations.
Patterns are conservative domain impact boundaries, not measured call graphs.
Unknown high-risk source gets UNKNOWN ownership, NOT_VERIFIED impact and
`review_required=true`; absence of a CI comparison base also requires review.
An unrecognized file is never reported covered.

CI emits the same plan from its GitHub event base and retains it beside the A16
plan. Exit zero means the static registry/plan producer worked; it does not mean
unknown impact was approved, tests executed or release evidence passed. Resolve
unknown high-risk impact in review by assigning its domain and required checks.
Unknown high-risk impact blocks core preparation. The original broad selections remain available as diagnostic policy; all original population variants remain discoverable. The approved core/specialized classification changes cadence, not assertions or historical outcomes.

## Evidence reuse and freshness

Use identity invalidation rather than a universal clock TTL:

- Any change covered by the original A04 whole-source fingerprint invalidates
  an older certificate for a current-source claim. Compute that original identity
  and obtain both supported variants after the final source freeze when changed.
- Changed test/assertion/helper bytes require review of the mapping and invalidate
  the former effectiveness claim. A mapping digest detects review drift; refreshing
  it records review intent and never creates execution proof.
- Changed oracle/configuration/expected artifacts require current A05B/A13
  provenance validation and applicable A04 refresh. Content hashes cannot supply
  authenticated update approval; the separate A13 requirement remains open.
- Different environment or selection-policy identities cannot reuse a measured
  observation as current. Missing timestamp/revision/fingerprint/test identity is
  NOT_VERIFIED. Historical observations remain historical even if code looks alike.
- Deterministic provider replay never supplies live-provider fidelity. Live checks
  remain separately authorized and obey their own actual provider freshness rules.
- Incident evidence never expires into deletion. Preserve its original outcome,
  source identity and hashes; A10 manual/archived tools cannot become active tests.

No existing policy supplies a universal evidence expiry duration, so none is
invented here. A16 retains source/run/attempt/environment/selection enforcement.

## Small review checklist for new or materially changed tests

1. Name the exact contract/domain and positive behavior; check for an existing
   equivalent historical regression before duplicating it.
2. Identify the production path actually reached, mocks/doubles and native parts.
   Mark static intent separately from observed execution and reached assertions.
3. Ensure a meaningful positive assertion and, for material risk, a matching
   negative/fault-sensitive case at the intended boundary.
4. Check independent source/configuration/temporal authority and financial
   expectations; a result generated by the production builder is not its oracle.
5. Declare the right tier, CI lane and PostgreSQL/browser/worker/provider fidelity;
   record remaining limits and unknown proof instead of promoting a unit result.
6. Refresh assertion/source/evidence identities and decide whether A04, native,
   extended or separately authorized live-provider evidence must be renewed.

Routine unit tests do not each need a new registry row. Extend the bounded map
only for a meaningful critical contract or ownership/selection change. Existing
T14 authority-census failures, A09 missing E2E timing and A13 authenticated review
are separate obligations; a green mapping check cannot close them.

## Approved finite core selection

The user-authorized 2026-10-30 handoff is registered in `test_selection`. Profiles are `core`, `affected`, `extended`, `diagnostic` and `live-authorized`. The existing A16 adapter constructs core from the curated P0 contracts plus A18 affected T0/T1 selectors. It retains migrations, populated restore, independent goldens, five cross-engine UI identities, Windows recovery, Node37, PromQL13 and the original A04 producer. A04 owns its two native cases; they are not selected again in Python/browser lanes.

CI prepares A18 impact first, then `--prepare-release --release-profile core --impact-plan <path>`. Both intended collection and execution read `--release-selectors <lane>` from the same sealed plan. All selected outcomes retain the original strict adapter, source, environment and finalizer checks. Missing, failed, errored, skipped, xfailed or incomplete evidence never certifies core. Unknown high-risk impact or a triggered specialized prerequisite blocks preparation.

The plan includes selected and deferred selectors with reasons, triggers and explicit NOT_RUN/ARCHIVAL_BLOCKED status. Retained Run40 history, absent dump, Run8, cohort, SLSE, observability and all 88/100 variants remain selectable for their specialized obligations. Deferred does not mean PASS. New incidents remain open. Original job/step IDs and legacy full-policy APIs are retained for compatibility.

Core aggregation uses `swinglens.core-release-certification.v1`, reports `CORE_RELEASE_CERTIFIED` or `CORE_RELEASE_FAILED`, and leaves FULL_RELEASE_REGRESSIONS and live-provider approval NOT_RUN with safe_for_normal_run=false. Preparation/prerequisite failures are CORE_RELEASE_BLOCKED. Diagnostic full selections, separate A13 external approval and separately authorized A15 live verification cannot inherit a core pass.
