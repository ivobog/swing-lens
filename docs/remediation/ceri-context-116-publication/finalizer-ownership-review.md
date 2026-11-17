# Sealed capture ownership in A04 finalization

Push run `44020837669`, job `133829839535`, executed all eight native 100/88
browser/handoff cases successfully in 6,532.1 seconds. Both original ten-ticker
pytest cases also passed in 292.3 seconds. Their final certificates nevertheless
failed one of 620 gates: `immutable_lineage` contained an unconditional historical
CERI `UNKNOWN` expectation. Every other gate, including all financial values,
402 rows and 541 relationships, passed. The selected proposed v5/r5 oracle already
declared the ten newly generated captures' ownership as KNOWN.

The finalizer now obtains each CERI capture's expected ownership state from that
capture's independently sealed manifest row, addressed by its exact semantic
member. KNOWN still requires the exact native parent ID; UNKNOWN requires the
original UNKNOWN representation. Other states are refused. The existing complete
membership, run, context, cutoff, configuration hash and source-edge comparisons
remain. Non-CERI ownership checks remain exact KNOWN-parent comparisons. The
separate oracle prerequisite still validates the entire sealed selected manifest;
editing an expected ownership state cannot create an authorized alternative.

Historical v3/v4 files are unchanged. The v3 finalizer specimen remains valid
with UNKNOWN ownership and rejects a substituted KNOWN parent. The proposed v5
specimen requires KNOWN ownership and rejects UNKNOWN or a foreign parent. Both
handoff modes retain all 620 required gates. No numerical value, tolerance,
relationship, mandatory check or producer fidelity is relaxed.

All **128** existing and new finalizer specimen cases passed, including twenty
new candidate/historical controls. The new controls use explicitly isolated JSON
unit doubles. They exercise the file finalizer, both ownership contracts and
unknown/foreign owner, foreign run/context/cutoff, changed configuration/source
and missing-member refusals. Their source-contract classification is
FAST/UNIT_DOUBLES; they claim no actual database or owner authorization.

For diagnosis, the original published archive was authenticated with SHA-256
`f7544b9e3d4c3a1eda66ac78c53f189a154d721a4c810629883f9ec2a57e1275`.
Separate copies of both native evidence bundles were re-evaluated with the
corrected finalizer. Each copy satisfied all 620 gates, 402 rows and 541
relationships, with no remaining nonpassing gate. The published archive and its
original FAILED certificates were not rewritten. These local diagnostic results
retain the original `ed5ea18d7b0651c0a8bf83021735734fece24129` producer identity;
they are not a current-source CI certificate.

Current-source native CI and its original finalizer/aggregation protocol remain
required after this correction. Native dependency and reuse boundaries, source
freshness, the trusted CI workflow, original scale limits and authenticated owner
acknowledgement are unchanged. The independent proposal digest and all candidate
bytes remain unchanged; authorization and activation are not granted by this fix.

All 216 existing source-governance, evidence-reuse and release-selection controls
also passed after registering the twenty new cases. Registry inspection retains
zero unknown impact, all fifteen declared native obligations, the existing
fourteen-case fresh PR selection, and all twenty new unit controls in the Python
lane. Ruff passes. No reuse dependency, source-freshness or scheduling boundary
was changed to avoid fresh native verification.
