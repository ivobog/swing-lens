"""Run the research-only v4 + Extension/Maturity + Trigger State study."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.technical_artifact_cache import config_hash
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.services.technical_v51_overlay_research import (
    RESEARCH_VERSION,
    TRIGGER_DIAGNOSTIC_COLUMNS,
    action_transitions,
    add_extension_states,
    build_candidate_rankings,
    candidate_evaluation,
    coverage_report,
    determine_verdict,
    extension_forensics,
    interaction_analysis,
    load_research_config,
    stable_hash,
    trigger_forensics,
    validate_research_frame,
)
from app.settings import get_settings

BASELINES = ("V4_BASELINE", "V5_BASELINE")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("output/technical_v5/forensic_enriched_dataset.csv"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output/technical_v51"))
    parser.add_argument("--config", type=Path, default=Path("config/technical_v51_research.yaml"))
    parser.add_argument("--bootstrap-samples", type=int, default=300)
    args = parser.parse_args()
    if args.bootstrap_samples < 20:
        raise ValueError("bootstrap-samples must be at least 20")
    runtime = get_settings()
    if runtime.technical_v5_enabled:
        raise RuntimeError("v5.1 research runner refuses to run while v5 is the default")
    if not args.dataset.exists():
        raise FileNotFoundError(
            f"{args.dataset} is missing; run scripts/run_technical_v5_forensic_recalibration.py"
        )

    config = load_research_config(args.config)
    v4_config = load_technical_scoring_v4_config()
    v5_config = load_technical_scoring_v5_config()
    frame = validate_research_frame(pd.read_csv(args.dataset, low_memory=False))
    missing_diagnostics = sorted(set(TRIGGER_DIAGNOSTIC_COLUMNS) - set(frame.columns))
    if missing_diagnostics:
        raise ValueError(
            "refresh the forensic enrichment before v5.1 trigger analysis; missing: "
            + ", ".join(missing_diagnostics)
        )
    observed_v5_hashes = set(frame.v5_config_hash.dropna().astype(str))
    expected_v5_hash = config_hash(v5_config)
    if observed_v5_hashes != {expected_v5_hash}:
        raise RuntimeError(
            f"V5_BASELINE hash mismatch: dataset={sorted(observed_v5_hashes)}, "
            f"config={expected_v5_hash}"
        )

    head = _command("git", "rev-parse", "HEAD")
    migration_head = _command("alembic", "heads").split()[0]
    candidate_hash = stable_hash(config)
    dataset_signature = stable_hash(
        {
            "rows": len(frame),
            "keys": sorted(
                f"{row.run_id}:{row.ticker}:{row.decision_date}:{row.input_signature}"
                for row in frame.itertuples(index=False)
            ),
        }
    )
    frame = add_extension_states(frame, config)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = _baseline_manifest(
        frame,
        v4_config=v4_config,
        v5_config=v5_config,
        candidate_hash=candidate_hash,
        dataset_signature=dataset_signature,
        git_commit=head,
        migration_head=migration_head,
        research_config=config,
    )
    (output_dir / "baseline_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )

    extension = extension_forensics(
        frame, config, bootstrap_samples=args.bootstrap_samples
    )
    trigger = trigger_forensics(frame, bootstrap_samples=args.bootstrap_samples)
    rankings = build_candidate_rankings(
        frame,
        config,
        candidate_config_hash=candidate_hash,
        git_commit=head,
    )
    candidate_results, top_selection, walk_forward = candidate_evaluation(
        rankings, frame, bootstrap_samples=args.bootstrap_samples
    )
    interactions = interaction_analysis(frame, bootstrap_samples=args.bootstrap_samples)
    transitions = action_transitions(frame)
    coverage = coverage_report(frame)

    extension.to_csv(output_dir / "extension_forensics.csv", index=False)
    trigger.to_csv(output_dir / "trigger_forensics.csv", index=False)
    candidate_results.to_csv(output_dir / "candidate_results.csv", index=False)
    top_selection.to_csv(output_dir / "candidate_top_selection.csv", index=False)
    interactions.to_csv(output_dir / "interaction_analysis.csv", index=False)
    transitions.to_csv(output_dir / "action_transitions.csv", index=False)
    walk_forward.to_csv(output_dir / "walk_forward_results.csv", index=False)
    coverage.to_csv(output_dir / "coverage_report.csv", index=False)

    research = config["research"]
    verdict = determine_verdict(
        candidate_results,
        independent_dates=frame.decision_date.nunique(),
        minimum_dates=int(research["minimum_promotion_dates"]),
    )
    _write_reports(
        frame=frame,
        extension=extension,
        trigger=trigger,
        candidates=candidate_results,
        manifest=manifest,
        verdict=verdict,
    )
    print(
        json.dumps(
            {
                "verdict": verdict,
                "observations": len(frame),
                "independent_dates": frame.decision_date.nunique(),
                "candidate_config_hash": candidate_hash,
                "dataset_signature": dataset_signature,
                "git_commit": head,
            }
        )
    )
    return 0


def _baseline_manifest(
    frame: pd.DataFrame,
    *,
    v4_config: dict[str, Any],
    v5_config: dict[str, Any],
    candidate_hash: str,
    dataset_signature: str,
    git_commit: str,
    migration_head: str,
    research_config: dict[str, Any],
) -> dict[str, Any]:
    dates = sorted(str(value) for value in frame.decision_date.unique())
    common = {
        "git_commit": git_commit,
        "migration_head": migration_head,
        "dataset_version": research_config["research"]["dataset_version"],
        "dataset_signature": dataset_signature,
        "decision_date_coverage": {
            "count": len(dates),
            "first": dates[0],
            "last": dates[-1],
            "dates": dates,
        },
        "input_signature_count": int(frame.input_signature.nunique()),
    }
    return {
        "immutable_baselines": {
            "V4_BASELINE": {
                **common,
                "candidate_id": "V4_BASELINE",
                "candidate_version": str(v4_config["engine"]["version"]),
                "config_hash": config_hash(v4_config),
                "engine_version": str(v4_config["engine"]["version"]),
            },
            "V5_BASELINE": {
                **common,
                "candidate_id": "V5_BASELINE",
                "candidate_version": str(v5_config["engine"]["version"]),
                "config_hash": config_hash(v5_config),
                "engine_version": str(v5_config["engine"]["version"]),
            },
        },
        "research_candidates": {
            "candidate_version": RESEARCH_VERSION,
            "config_hash": candidate_hash,
            "git_commit": git_commit,
            "production_enabled": False,
            "active_extension_threshold_set": research_config["research"][
                "active_extension_threshold_set"
            ],
        },
    }


def _write_reports(
    *,
    frame: pd.DataFrame,
    extension: pd.DataFrame,
    trigger: pd.DataFrame,
    candidates: pd.DataFrame,
    manifest: dict[str, Any],
    verdict: str,
) -> None:
    docs = Path("docs")
    active_set = str(frame.extension_threshold_set_id.iloc[0])
    extension_summary = extension[
        (extension.record_type == "STATE_SUMMARY")
        & (extension.threshold_set_id == active_set)
    ][
        [
            "extension_state",
            "N",
            "N_5d",
            "N_10d",
            "independent_dates",
            "mean_return_1d",
            "mean_return_3d",
            "mean_return_5d",
            "mean_return_10d",
            "mean_MFE_5d",
            "mean_MAE_5d",
            "hit_rate_5d",
        ]
    ]
    ts_extension = extension[
        (
            extension.record_type.isin(
                {"TS_EXTENSION_COHORT", "TS_EXTENSION_PERCENTILE_REPLICATION"}
            )
        )
        & extension.threshold_set_id.isin(
            {active_set, "PERSISTED_EXTENSION_PERCENTILE"}
        )
    ][
        [
            "threshold_set_id",
            "ts_threshold",
            "extension_group",
            "N",
            "N_5d",
            "N_10d",
            "independent_dates",
            "mean_return_1d",
            "mean_return_3d",
            "mean_return_5d",
            "mean_return_10d",
            "mean_MFE_5d",
            "mean_MAE_5d",
            "hit_rate_5d",
            "mean_return_5d_ci_low",
            "mean_return_5d_ci_high",
        ]
    ]
    trigger_summary = trigger[trigger.record_type == "STATE_SUMMARY"]
    trigger_summary = trigger_summary[
        [
            "trigger_state",
            "N",
            "N_5d",
            "N_10d",
            "independent_dates",
            "mean_return_1d",
            "mean_return_3d",
            "mean_return_5d",
            "mean_return_10d",
            "mean_MFE_5d",
            "mean_MAE_5d",
            "hit_rate_5d",
        ]
    ]
    at_fresh = trigger_summary[
        trigger_summary.trigger_state.isin({"AT_TRIGGER", "FRESHLY_TRIGGERED"})
    ]
    legacy_ts8 = ts_extension[
        (ts_extension.threshold_set_id == "PERSISTED_EXTENSION_PERCENTILE")
        & (ts_extension.ts_threshold == 8.0)
    ]
    state_ts8 = ts_extension[
        (ts_extension.threshold_set_id == active_set) & (ts_extension.ts_threshold == 8.0)
    ]
    legacy_extension_finding = _cohort_comparison(
        legacy_ts8, "LOW_EXTENSION_LT_P50", "HIGH_EXTENSION_GE_P80"
    )
    state_extension_finding = _cohort_comparison(
        state_ts8, "LOW_EXTENSION", "HIGH_EXTENSION"
    )
    trigger_finding = _cohort_comparison(
        at_fresh, "AT_TRIGGER", "FRESHLY_TRIGGERED", label_column="trigger_state"
    )
    holdout = candidates[
        (candidates.evaluation_scope == "holdout")
        & (candidates.selection == "TOP_20_PCT")
        & (candidates.danger_variant == "NO_DANGER_EXCLUSION")
    ][
        [
            "candidate_id",
            "coverage",
            "candidate_count",
            "independent_dates",
            "mean_return_5d",
            "median_return_5d",
            "mean_return_10d",
            "median_return_10d",
            "mean_MFE_5d",
            "mean_MAE_5d",
            "hit_rate_5d",
            "paired_mean_delta_5d",
            "candidate_turnover_vs_v4",
        ]
    ]
    dates = frame.decision_date.nunique()
    observations = len(frame)
    v4 = manifest["immutable_baselines"]["V4_BASELINE"]
    v5 = manifest["immutable_baselines"]["V5_BASELINE"]

    (docs / "technical_v51_extension_forensics.md").write_text(
        f"""# Technical v5.1 extension forensics

## Scope

The active deterministic threshold set is `{active_set}`. The fixed
`conservative_v1` set is retained only as a sensitivity check. Both require agreement
from multiple decision-time inputs, except documented Stage/climax overrides. Raw
inputs, state, reasons, threshold-set ID, input signature, and aggregate outcome rows
are persisted in `output/technical_v51/extension_forensics.csv`.

## Extension-state outcomes

{_markdown_table(extension_summary)}

## TS x Extension replication

{_markdown_table(ts_extension)}

The sample has only {dates} independent dates. These cohorts diagnose whether TS
inversion is concentrated in mature entries; they do not authorize a production
weight change or a claim of stable causality.

The original percentile definition gives {legacy_extension_finding}. The broader
multivariate state gives {state_extension_finding}. Therefore extension plausibly
explains part of TS inversion, but the finding is definition-sensitive and is not yet
a validated primary explanation.
""",
        encoding="utf-8",
    )
    (docs / "technical_v51_trigger_forensics.md").write_text(
        f"""# Technical v5.1 trigger forensics

## Trigger-state outcomes

{_markdown_table(trigger_summary)}

## AT_TRIGGER versus FRESHLY_TRIGGERED

{_markdown_table(at_fresh)}

The observation and diagnostic rows in `output/technical_v51/trigger_forensics.csv`
compare distance ATR, volume confirmation, strong-close ratio, breakout volume, gap
behavior, next-day follow-through, same-day EMA20/SMA50 extension, setup type, Stage,
regime, v4, TS, SQ, and EQ. Numeric diagnostics use fixed within-sample quartile slices
for description only; trigger bands remain frozen.

The aggregate comparison gives {trigger_finding}. Gap-exhaustion behavior is available
for every row; the persisted debug payload did not contain numeric gap-up magnitude, so
that field is retained as explicitly missing instead of being reconstructed from a
later price revision.
""",
        encoding="utf-8",
    )
    (docs / "technical_v51_candidate_comparison.md").write_text(
        f"""# Technical v5.1 candidate comparison

## Frozen candidate architecture

- `M0_V4`: unchanged v4 control.
- `M1A/M1B`: filter EXTREME or EXTENDED+EXTREME; `M1C` uses extension only inside
  fixed 0.5-point v4 bands.
- `M2`: v4 bands, trigger eligibility, then fixed trigger preference.
- `M3`: v4 bands, EXTREME exclusion, then extension and trigger preference.
- `M4`: fixed TS 6.0/6.5/7.0 eligibility gates followed by v4.
- `V5_BASELINE`: immutable frozen comparison control.

No additive overlay score, danger cap tuning, sector RS ranking input, or production
activation is present. Each candidate also has a label-only danger-exclusion
sensitivity, except the frozen v5 control.

## Holdout top 20%

{_markdown_table(holdout)}

The time split is chronological and the policies above are config-hashed before any
evaluation. With only {dates} independent dates, holdout estimates are descriptive.

**Research verdict: {verdict}**
""",
        encoding="utf-8",
    )
    (docs / "technical_v51_research_report.md").write_text(
        f"""# Technical Scoring v5.1 overlay research report

## Executive verdict

**{verdict}**

V4 remains the production ranking baseline. V5 stays in shadow and disabled as the
default. This campaign evaluates only extension/maturity and trigger-state gates or
secondary sorts around v4.

## Frozen provenance

- Observations: {observations:,}
- Independent dates: {dates} ({frame.decision_date.min()} through {frame.decision_date.max()})
- V4 baseline: `{v4['candidate_version']}` / `{v4['config_hash']}`
- V5 baseline: `{v5['candidate_version']}` / `{v5['config_hash']}`
- Candidate version: `{RESEARCH_VERSION}`
- Candidate config hash: `{manifest['research_candidates']['config_hash']}`
- Git commit at execution: `{v4['git_commit']}`
- Migration head: `{v4['migration_head']}`
- Dataset version: `{v4['dataset_version']}`
- Dataset signature: `{v4['dataset_signature']}`

`output/technical_v51/baseline_manifest.json` contains the immutable baseline records,
decision-date list, and input-signature coverage.

## Findings

The extension and trigger reports retain all 1d/3d/5d/10d outcome metrics, MFE, MAE,
hit rate, and decision-date-cluster bootstrap intervals. Candidate results additionally
include coverage, top 10%/20%, within-run rank correlation, paired date deltas, and
turnover versus v4. All mandatory V4/TS/setup/regime interactions and canonical action
transitions are exported.

At TS >= 8, the narrow persisted-percentile replication gives
{legacy_extension_finding}; the broader multivariate state gives
{state_extension_finding}. This supports an extension/maturity interaction but does not
show that TS inversion is primarily or stably caused by extension. Trigger state
reproduces the sharper clue: {trigger_finding}.

The promotion sample target is 50 independent dates (80+ preferred); current coverage
is {dates}. Walk-forward plumbing is complete, but the present calibration/validation/
holdout slices are too small for promotion. Historical rows are accepted only with the
two certified reconstruction statuses, and state/rank assignment never reads forward
outcomes.

## Scope boundaries

- TS, SQ, EQ, Leadership, Residual Momentum, Stage, Danger State, and Sector RS remain
  available diagnostics.
- Sector RS is not used by M1/M2/M3.
- Danger labels remain visible; numeric danger caps do not affect primary candidates.
- G8 remains failed. No Winner, lifecycle, alert, SLSE, or ranking-profile migration was
  performed because no new correctness or contract issue was discovered.
- No production setting or scorer configuration was changed.
""",
        encoding="utf-8",
    )


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "No defensible rows."
    display = frame.copy()
    for column in display.select_dtypes(include="number"):
        display[column] = display[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    headers = [str(column) for column in display.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in display.itertuples(index=False, name=None)
    )
    return "\n".join(lines)


def _cohort_comparison(
    frame: pd.DataFrame,
    first: str,
    second: str,
    *,
    label_column: str = "extension_group",
) -> str:
    left = frame[frame[label_column] == first]
    right = frame[frame[label_column] == second]
    if left.empty or right.empty:
        return "insufficient defensible rows"
    left_row = left.iloc[0]
    right_row = right.iloc[0]
    return (
        f"{first} {left_row.mean_return_5d:.3f}%/{left_row.mean_return_10d:.3f}% "
        f"at 5d/10d (N={int(left_row.N_5d)}/{int(left_row.N_10d)}), versus "
        f"{second} {right_row.mean_return_5d:.3f}%/{right_row.mean_return_10d:.3f}% "
        f"(N={int(right_row.N_5d)}/{int(right_row.N_10d)})"
    )


def _command(*parts: str) -> str:
    result = subprocess.run(parts, check=True, capture_output=True, text=True)
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
