# Final release source preparation

Baseline: accepted implementation `a2f134e` on `codex/forward-quality-v22`.

The user explicitly approved the minimal frozen-evidence extension before its
implementation. v2.2 policy v5 requires a literal true producer-sealed
`forward_financial_evidence_available` fact. Missing, invalid, or false evidence
fails closed. The accepted formula, neutral fallback, and 65%/50% coverage thresholds
are unchanged. v2.1 retains policy v3 and its original serialized input shape.

The 127-company repeat is in `correction-comparison/`. Comparing every numerical
score, component, penalty, coverage value, and classification to `a2f134e` gives
exact equality. Only COKE changes from INCLUDED to UNDECIDED. No ticker exception
exists. `admission-correction-verification.json` records the exact result.

The protected old configuration is restored byte-identically at
`config/fundamentals_v2.yaml` (SHA-256
`2f228e931f0ce2bdc545a06badfe07854c2f1dfdf038881473b745739eb22db3`).
New computation/GUI defaults select `config/fundamentals_v2_2.yaml`; its logical
scoring config hash remains the accepted v2.2 hash. Frozen model selection remains
authoritative. The registry/provenance prerequisite now passes without changing an
old seal or weakening verification. New config/review bytes use scoped -text
attributes so Windows checkout conversion cannot invalidate their hashes.

Current main is protected with administrator enforcement, strict up-to-date checks,
no force pushes/deletions, and the four required GitHub Actions checks. Those settings
were read only and will not be changed. No second-person review is required by the
existing sole-owner policy; authenticated owner acknowledgement is still required.

The independent v6 authoring program calculates new expectations from approved
literal equations, frozen raw inputs, and prior independent vectors. It imports no
production scoring functions and reads no production/real-company comparison output.
It first reproduces all 60 old Combined/Ranking rows exactly, then propagates the
new fundamental score through the unchanged downstream equations. The financial
literal result is forward 7.3646 and fundamental 8.9800.

The retained ten-ticker fixture has no currency headers. The proposed new v6
controlled specimen therefore declares USD in eight already-mapped currency columns
(80 metadata cells). These are authored synthetic test facts for the positive
comparison case, not recovered vendor provenance or changes to real uploaded rows.
No financial amount or production mapping is invented. This input change and all
resulting expectation differences require exact owner review before activation.

The new candidate will use new v6 paths. v3 protected inputs/oracles and v4/v5
candidate artifacts remain untouched. The existing pending CERI proposal is archived
as metadata rather than adopted; no CERI financial expectation or implementation
change is included. Candidate validation and owner authorization do not activate
a golden. All mandatory native CI/certification must still pass before merge.
