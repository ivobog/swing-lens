"""Offline v2.1/v2.2 row comparison; no database writes or provider access.

Admission is a conditional policy simulation with the same valid-shaped provenance
on both sides. It does not claim retained production evidence or temporal certification.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from app.services.column_mapper import map_csv_rows
from app.services.fundamental_admission_policy import (
    FundamentalAdmissionInput,
    evaluate_fundamental_admission,
)
from app.services.fundamental_components_v2 import (
    COMPONENT_SCORE_KEYS,
    _forward_amount_pair,
    _forward_growth,
    _forward_number,
    forward_quality_subscores,
)
from app.services.fundamental_ranker_v2 import load_fundamentals_v2_config, score_rows_v2


def _growth(values, historical, estimated, *, magnitude=False):
    pair = _forward_amount_pair(values, historical, estimated)
    if pair is None:
        return None
    history, estimate = pair
    if magnitude:
        history, estimate = abs(history), abs(estimate)
    if history <= 0 or estimate < 0:
        return None
    return float(_forward_growth(history, estimate))


def _admission(score, fingerprint):
    value = FundamentalAdmissionInput(
        consumer_edge="FUNDAMENTAL_TO_COMBINED",
        source_identity_fingerprint=fingerprint,
        # A probe placeholder, deliberately not represented as a retained DB ID.
        source_evidence_id=1,
        decision_cutoff=datetime(2026, 11, 19, tzinfo=UTC),
        fundamental_score=score.fundamental_score,
        fundamental_label=score.fundamental_label,
        scoring_model_version=score.debug["model_version"],
        coverage=score.debug["coverage"],
        component_coverage=score.debug["component_coverage"],
        parse_diagnostics=score.debug["parse_diagnostics"],
        warning_codes=tuple(score.warning_flags),
    )
    return evaluate_fundamental_admission(value, authoritative=True)


def _read(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        raw = list(csv.DictReader(handle))
    return map_csv_rows(raw), hashlib.sha256(path.read_bytes()).hexdigest()


def _older_rows(directory, latest_tickers, latest_path):
    selected = []
    counts = Counter()
    seen = set(latest_tickers)
    scanned = 0
    for path in sorted(directory.glob("*.csv"), reverse=True):
        if path.name >= latest_path.name:
            continue
        scanned += 1
        rows, digest = _read(path)
        for row in rows:
            if not row.ticker or row.ticker in seen:
                continue
            history = _forward_number(row.canonical.get("net_income_ttm"))
            estimate = _forward_number(row.canonical.get("net_income_estimate_ntm"))
            if history is None or history >= 0 or estimate is None:
                continue
            cohort = "turnaround" if estimate > 0 else "loss_making"
            if counts[cohort] >= 8:
                continue
            selected.append((row, path.name, digest, cohort))
            seen.add(row.ticker)
            counts[cohort] += 1
        if counts["turnaround"] >= 8 and counts["loss_making"] >= 8:
            break
    return selected, {"files_scanned": scanned, "selected": dict(counts)}


def _summarize(records):
    def distribution(key):
        values = [record[key] for record in records]
        return {
            "mean": mean(values),
            "median": median(values),
            "min": min(values),
            "max": max(values),
        }

    changes = [
        r for r in records if r["old_conditional_admission"] != r["new_conditional_admission"]
    ]
    return {
        "companies": len(records),
        "sectors": len({r["sector"] for r in records}),
        "score_distributions": {
            key: distribution(key)
            for key in (
                "old_forward",
                "new_forward",
                "old_fundamental",
                "new_fundamental",
                "difference",
            )
        },
        "subcomponent_availability": {
            key: {
                "count": sum(r["subscores"][key] is not None for r in records),
                "rate": sum(r["subscores"][key] is not None for r in records) / len(records),
            }
            for key in records[0]["subscores"]
        },
        "classifications": {
            "old": dict(Counter(r["old_classification"] for r in records)),
            "new": dict(Counter(r["new_classification"] for r in records)),
            "changes": [
                r["ticker"] for r in records if r["old_classification"] != r["new_classification"]
            ],
        },
        "coverage": {
            key: distribution(key)
            for key in (
                "old_overall_coverage",
                "new_overall_coverage",
                "old_forward_coverage",
                "new_forward_coverage",
            )
        },
        "conditional_admission": {
            "old": dict(Counter(r["old_conditional_admission"] for r in records)),
            "new": dict(Counter(r["new_conditional_admission"] for r in records)),
            "changes": [
                {
                    "ticker": r["ticker"],
                    "old": r["old_conditional_admission"],
                    "new": r["new_conditional_admission"],
                    "old_reasons": r["old_admission_reasons"],
                    "new_reasons": r["new_admission_reasons"],
                }
                for r in changes
            ],
        },
        "largest_increases": [
            {k: r[k] for k in ("ticker", "difference", "old_forward", "new_forward")}
            for r in sorted(
                (r for r in records if r["difference"] > 0),
                key=lambda r: r["difference"],
                reverse=True,
            )[:5]
        ],
        "largest_decreases": [
            {k: r[k] for k in ("ticker", "difference", "old_forward", "new_forward")}
            for r in sorted(
                (r for r in records if r["difference"] < 0), key=lambda r: r["difference"]
            )[:5]
        ],
        "other_component_mismatches": sum(not r["other_components_identical"] for r in records),
        "penalty_mismatches": sum(r["old_penalty"] != r["new_penalty"] for r in records),
        "max_abs_raw_delta": max(abs(r["raw_difference"]) for r in records),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-file", type=Path, required=True)
    parser.add_argument("--older-upload-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows, digest = _read(args.csv_file)
    if (
        digest != "2247b49dc3f4e8fd88a1f96d50d095b1c17b8ba30a06ae6d2822bf199d532331"
        or len(rows) != 111
    ):
        raise ValueError("Expected the accepted 111-company input assessment source")
    selected = [(row, args.csv_file.name, digest, "latest") for row in rows]
    older, older_scan = _older_rows(args.older_upload_dir, {r.ticker for r in rows}, args.csv_file)
    selected.extend(older)
    old_config = load_fundamentals_v2_config(Path("tests/fixtures/fundamentals_v21.yaml"))
    new_config = load_fundamentals_v2_config()
    mapped = [item[0] for item in selected]
    started = perf_counter()
    old_scores = score_rows_v2(mapped, old_config)
    old_elapsed = perf_counter() - started
    started = perf_counter()
    new_scores = score_rows_v2(mapped, new_config)
    new_elapsed = perf_counter() - started
    records = []
    for (row, filename, file_hash, cohort), old, new in zip(
        selected, old_scores, new_scores, strict=True
    ):
        values = row.canonical
        old_admission, new_admission = (_admission(score, file_hash) for score in (old, new))
        other = [key for key in COMPONENT_SCORE_KEYS if key != "forward_quality_score"]
        raw_delta = sum(
            (getattr(new, key) - getattr(old, key)) * weight
            for key, weight in new_config["weights"].items()
        )
        if not all(getattr(old, k) == getattr(new, k) for k in other):
            raise AssertionError(f"Other component changed: {row.ticker}")
        if old.missing_data_penalty != new.missing_data_penalty or abs(raw_delta) > 0.800000001:
            raise AssertionError(f"Score isolation failed: {row.ticker}")
        records.append(
            {
                "ticker": row.ticker,
                "sector": row.sector,
                "cohort": cohort,
                "source_file": filename,
                "source_sha256": file_hash,
                "source_row": row.row_number,
                "expected_net_income_growth": _growth(
                    values, "net_income_ttm", "net_income_estimate_ntm"
                ),
                "annual_revenue_vs_ttm": _growth(
                    values, "total_revenue_ttm", "revenue_estimate_annual"
                ),
                "expected_margin_improvement": None,
                "annual_cash_comparison": _growth(
                    values,
                    "cash_short_term_investments_annual",
                    "cash_short_term_investments_estimate_annual",
                ),
                "expected_capex_growth": _growth(
                    values, "capex_ttm", "capex_estimate_ntm", magnitude=True
                ),
                "subscores": forward_quality_subscores(values),
                "old_forward": old.forward_quality_score,
                "new_forward": new.forward_quality_score,
                "old_fundamental": old.fundamental_score,
                "new_fundamental": new.fundamental_score,
                "difference": round(new.fundamental_score - old.fundamental_score, 4),
                "raw_difference": raw_delta,
                "other_components_identical": True,
                "old_components": {key: getattr(old, key) for key in other},
                "new_components": {key: getattr(new, key) for key in other},
                "old_penalty": old.missing_data_penalty,
                "new_penalty": new.missing_data_penalty,
                "old_overall_coverage": old.debug["coverage"]["coverage_ratio"],
                "new_overall_coverage": new.debug["coverage"]["coverage_ratio"],
                "old_forward_coverage": old.debug["component_coverage"]["forward_quality_score"],
                "new_forward_coverage": new.debug["component_coverage"]["forward_quality_score"],
                "old_classification": old.fundamental_label,
                "new_classification": new.fundamental_label,
                "old_warnings": old.warning_flags,
                "new_warnings": new.warning_flags,
                "old_conditional_admission": old_admission.eligibility.value,
                "new_conditional_admission": new_admission.eligibility.value,
                "old_admission_reasons": list(old_admission.reason_codes),
                "new_admission_reasons": list(new_admission.reason_codes),
            }
        )
    summary = {
        "admission_interpretation": (
            "Conditional policy simulation with valid-shaped shared provenance; "
            "not retained production admission evidence"
        ),
        "models": {c["model_version"]: c.config_hash for c in (old_config, new_config)},
        "latest": _summarize(records[:111]),
        "older": _summarize(records[111:]) if older else None,
        "older_scan": older_scan,
        "timing_seconds_single_pass": {"old": old_elapsed, "new": new_elapsed, "rows": len(mapped)},
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "comparison.json").write_text(
        json.dumps(records, indent=2, allow_nan=False) + "\n"
    )
    (args.output_dir / "comparison-summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )
    flat = []
    for record in records:
        result = {
            k: v
            for k, v in record.items()
            if k not in ("old_components", "new_components", "subscores")
        }
        result["available_forward_components"] = ";".join(
            k for k, v in record["subscores"].items() if v is not None
        )
        for version in ("old", "new"):
            result.update({f"{version}_{k}": v for k, v in record[f"{version}_components"].items()})
        result.update({f"subscore_{k}": v for k, v in record["subscores"].items()})
        for k, v in result.items():
            if isinstance(v, list):
                result[k] = ";".join(v)
        flat.append(result)
    with (args.output_dir / "company-comparison.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
