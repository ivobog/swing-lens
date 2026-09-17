"""Source-qualified adoption call review, separate from T14A history."""

NON_EDGE_REVIEWS = {
    (
        "app/services/core_mutation_authority.py:_validate_permission",
        "native.evaluate",
    ): "T14B: native is an explicitly named TechnicalConsumerPolicy or ContextualConsumerPolicy "
    "constant from the two Phase-3 policy modules; evaluate accepts a readiness envelope "
    "and has no Session or persistence. Same-name Setup/Lifecycle evaluate methods are "
    "unrelated receivers, and are not mutation graph edges.",
    (
        "app/services/core_mutation_authority.py:validate_sector_permission",
        "native.evaluate",
    ): "T14B: artifact_kind selects only TECHNICAL/COMBINED/RANKING_TO_SECTOR constants; "
    "their TechnicalConsumerPolicy/ContextualConsumerPolicy evaluate methods validate "
    "immutable readiness without persistence. Homonymous decision writers cannot be "
    "selected by this source-qualified lookup.",
}
