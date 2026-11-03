# PR 78 native certification fixture correction

The retained A05/A05B corpus remains `a05-v3/a05b-r3`, with all 45 historical seals
and original derivations intact. The proposed `a05-v4/a05b-r4` expectations retain
402 financial rows, 541 relationships and the original tolerances. Production
calculation rules are unchanged by the fixture correction described here.

## Observed failure

The older PR-associated [run 41546333951](https://github.com/ivobog/swing-lens/actions/runs/41546333951)
completed its source-impact prerequisites with four passing Chromium 88/100
population cases. Four Firefox cases could not launch the missing engine; the
separate reviewed CI provisioning correction addresses that prerequisite.

The native Run 40 two-ticker recovery case passed. The 88-ticker case completed
its publication/interruption/replay checks, then excluded all 88 Winner inputs
instead of inserting the required 88 predictions. Its original insertion
assertion correctly failed. A small native reproduction isolated the mandatory
Technical source: confidence was high, coverage complete and freshness valid,
but the producer was `DEGRADED` with `blowoff_top` and `climax_reversal_risk`.
Winner's `technical-to-winner-v1` decision remained `POLICY_UNDECIDED` with
`DEGRADED_POLICY_UNDECIDED`. Combined and Ranking v2 research admission did not
grant permission to Winner.

## Correct positive input and retain the negative boundary

The helper retains earlier price bars. Applying its old current-run multiplier
therefore creates a large new-session gap, rather than rewriting the prior
history. The positive Winner fixture now uses this explicit synthetic input:

| Current-run input when Winner is enabled | Previous | READY-positive case | Reason |
| --- | ---: | ---: | --- |
| OHLC multiplier | 1.2 | 1.02 | Preserve a changed price path without the deliberately excessive gap. |
| Volume multiplier | 2 | 1.1 | Preserve a volume change without the climax pattern. |

The new `large_current_gap=True` native control retains the old 1.2/2 input. It
requires the producer to remain DEGRADED, Winner v1 to remain undecided, and both
rows to be excluded with no predictions. It additionally confirms that Combined
and Ranking research permission cannot supply Winner permission. The READY case
requires actual certified Technical eligibility and two committed predictions.
Both PostgreSQL controls passed; the initial warned-input reproduction failed
the same positive expectation before the fixture correction.

The 88-ticker scenario keeps its population, 321 historical observations,
interruption checkpoints, cross-session visibility, rollback, replay/idempotency
checks and exact `winner.inserted == population` assertion. The ordinary
Winner-disabled fixture keeps its existing price/volume path. Historical input
artifacts and execution records are not rewritten. No production policy,
threshold, lookback, risk budget, golden financial expectation or tolerance is
changed. The complete current-source nine-lane release plan and all ten triggered
native prerequisites still have to pass on the final PR head.

The completed local deterministic diagnostic at preceding source `9b4f7fc`
certified both original ten-ticker handoff variants. Its receipts are archived
privately with byte hashes and CRC verification; they do not replace final-head
GitHub certification or authenticated sole-owner acknowledgement.
