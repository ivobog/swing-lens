# v2.2 forward financial evidence admission correction: proposed contract

Implementation baseline: `a2f134e203f185edba8c28d9e83f172906c842e1`.
Status: approved in chat before implementation. The user explicitly replied
"Approve this minimal evidence extension". This is contract-scope authorization,
not authenticated approval of a protected golden proposal.

The existing admission input retains aggregate field coverage, missing-field lists,
parse diagnostics, score/classification, warnings, and opaque source identity. It
does not retain monetary currencies or actual mathematical subcomponent availability.

A read-only distinguishability probe used unchanged expected/historical income and
Hold rating, with the other financial forecasts missing. Matching USD currencies
give an available earnings subscore of 5; conflicting USD/EUR currencies make that
subscore unavailable. Both complete scorer outputs and current admission decisions
are identical: forward 5, INCLUDED. No existing admission predicate can distinguish
these cases reliably. Merely checking estimate presence cannot establish usability.

The smallest proposed extension is one producer-calculated, hash-sealed boolean in
the existing debug payload:

```text
debug_json.forward_financial_evidence_available: boolean
```

- Calculate it from the accepted `forward_quality_subscores` helper on the mapped
  row: any financial subscore other than analyst rating is not None. A valid score
  of zero counts as usable evidence; no positivity or investment gate is introduced.
- Forward this exact frozen fact into the admission input. Do not infer it from a
  neutral score, analyst opinion, historical presence, or mutable current data.
- For v2.2 only, require literal true. False and missing evidence remain UNDECIDED
  with explicit reasons. Retain 65% overall and 50% component coverage requirements.
- Version the revised admission policy and frozen registration; keep v2.1 decisions
  and registrations exactly unchanged. Do not rewrite historical evidence.
- No database column/table, REST/GUI payload, provider, or formula change is needed.
  This nevertheless expands the frozen evidence/admission contract, and was implemented only after the user's requested prior approval.

The read-only 127-company proposal simulation uses the existing row-level subscores:
only COKE has zero usable financial forecasts. It is the only proposed decision
change, INCLUDED to UNDECIDED; every mathematical score and penalty stays unchanged.
See `admission-correction-proposal.json`. The implemented repeat reproduces only that COKE correction; mathematical scores
and all 126 other admission outcomes match the accepted implementation.
