# CI snapshot comparison baseline

PR execution compares the event's base SHA; push execution compares `before`.
Those existing comparisons remain unchanged.

Scheduled and manual snapshots are supported only for `ivobog/swing-lens` on
`refs/heads/main`, the event repository's default branch. The checked-out commit
must equal `GITHUB_SHA`. With complete Git history, the comparison is that exact
commit's verified first parent. The parent must exist, differ from the tested
commit, and be its ancestor. A root commit, shallow history, missing parent,
invalid event reference, foreign repository, or mismatched source leaves the
baseline unknown and the plan review-required. No empty self comparison is used.

This is a snapshot policy: the diff selects additional obligations for the most
recent first-parent change. It does not certify changes since a prior successful
run. Every invocation still requires the full curated core, all P0 contracts,
protected native A04 cases, and the existing required lanes. Unknown financial
paths still require owner review. Deferred tests are never reported as passed.

The source-impact evidence records event, tested source, resolved baseline,
policy, and any rejection reason. The release planner re-resolves that evidence
against the actual source and event before accepting it. All participating CI
checkouts fetch complete history so planning and lane verification use the same
Git comparison. The source-bound release plan seals the comparison along with
the selected obligations. Planning remains separate from certification.
