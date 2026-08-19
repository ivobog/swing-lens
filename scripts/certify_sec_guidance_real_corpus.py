from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService
from app.services.ceri.sec.processor_signature import (
    DOCUMENT_PARSER_VERSION,
    EVIDENCE_LOCATOR_VERSION,
    FILING_SELECTION_POLICY_VERSION,
    GUIDANCE_EXTRACTOR_VERSION,
    sec_guidance_processor_signature,
)

ROW_FIELDS = (
    "metric",
    "period_label",
    "low_value",
    "high_value",
    "point_value",
    "unit",
    "currency",
    "management_claim",
)
NUMERIC_FIELDS = ("low_value", "high_value", "point_value")
INITIAL_GATES = {
    "reviewed_cases": (100, ">="),
    "positive_cases": (30, ">="),
    "negative_cases": (50, ">="),
    "precision": (0.98, ">="),
    "false_positives": (1, "<="),
    "recall": (0.85, ">="),
    "numeric_values": (0.98, ">="),
    "metric": (0.98, ">="),
    "period_label": (0.95, ">="),
    "unit": (0.98, ">="),
    "currency": (0.98, ">="),
}


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0

    @property
    def rate(self) -> float:
        return self.correct / self.total if self.total else 0.0


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
    return rows


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unavailable"


def _decimal_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _normalize(field: str, value: Any) -> Any:
    if field in NUMERIC_FIELDS:
        return _decimal_string(value)
    if value is None:
        return None
    return str(value).upper() if field != "evidence_locator" else str(value)


def _expected_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    if case["label"] != "POSITIVE":
        return []
    if "expected_rows" in case:
        rows = case["expected_rows"]
    else:
        rows = [case.get("expected")]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Positive case {case['case_id']} has no valid expected row")
    return rows


def _actual_row(row: Any) -> dict[str, Any]:
    return {
        "metric": row.metric,
        "period_label": row.period_label,
        "low_value": _decimal_string(row.low_value),
        "high_value": _decimal_string(row.high_value),
        "point_value": _decimal_string(row.point_value),
        "unit": row.unit,
        "currency": row.currency,
        "management_claim": row.management_claim,
        "action": row.action,
        "confidence": row.confidence,
        "evidence_locator": row.evidence_locator,
        "matched_text": row.matched_text,
        "matched_text_sha256": _sha256_text(row.matched_text),
        "warnings": list(row.warnings),
    }


def _accepted(row: dict[str, Any]) -> bool:
    return bool(
        row["confidence"] == "HIGH"
        and row["metric"] is not None
        and row["period_label"] is not None
        and (
            row["low_value"] is not None
            or row["point_value"] is not None
            or row["management_claim"] == "WITHDRAWN"
        )
    )


def _mismatches(expected: dict[str, Any], actual: dict[str, Any] | None) -> list[str]:
    if actual is None:
        return list(ROW_FIELDS)
    return [
        field
        for field in ROW_FIELDS
        if _normalize(field, expected.get(field)) != _normalize(field, actual.get(field))
    ]


def _match_rows(
    expected_rows: list[dict[str, Any]], actual_rows: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None, list[str]]], list[dict[str, Any]]]:
    remaining = list(range(len(actual_rows)))
    matches: list[tuple[dict[str, Any], dict[str, Any] | None, list[str]]] = []
    for expected in expected_rows:
        if not remaining:
            matches.append((expected, None, list(ROW_FIELDS)))
            continue
        chosen = min(
            remaining,
            key=lambda index: (
                len(_mismatches(expected, actual_rows[index])),
                json.dumps(actual_rows[index], sort_keys=True),
            ),
        )
        remaining.remove(chosen)
        actual = actual_rows[chosen]
        matches.append((expected, actual, _mismatches(expected, actual)))
    return matches, [actual_rows[index] for index in remaining]


def _validate_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = (
        "case_id",
        "company",
        "ticker",
        "cik",
        "accession",
        "filing_date",
        "form",
        "document_name",
        "source_url",
        "source_document_sha256",
        "visible_text_locator",
        "passage_sha256",
        "passage_text",
        "label",
        "review_state",
    )
    errors.extend(f"missing:{field}" for field in required if field not in case)
    passage = str(case.get("passage_text", ""))
    if case.get("passage_sha256") != _sha256_text(passage):
        errors.append("passage_sha256_mismatch")
    if case.get("label") not in {"POSITIVE", "NEGATIVE", "REVIEW_REQUIRED"}:
        errors.append("invalid_label")
    if case.get("label") == "REVIEW_REQUIRED":
        if case.get("review_state") != "UNRESOLVED":
            errors.append("review_required_not_unresolved")
    elif case.get("review_state") != "REVIEWED":
        errors.append("scored_case_not_reviewed")
    return errors


def _severity(case: dict[str, Any], mismatch_fields: list[str], unexpected: bool) -> str:
    text = f"{case.get('passage_text', '')} {case.get('notes', '')}".lower()
    critical_tokens = (
        "historical",
        "forward-looking",
        "actual results may differ",
        "undue reliance",
        "analyst",
        "third-party",
        "industry outlook",
        "xbrl",
        "pension",
        "date range",
    )
    if case.get("label") == "NEGATIVE" and any(token in text for token in critical_tokens):
        return "CRITICAL"
    if mismatch_fields == ["evidence_locator"]:
        return "LOW"
    if mismatch_fields == ["management_claim"] or unexpected:
        return "MEDIUM"
    return "HIGH"


def _root_cause(case: dict[str, Any], failure_type: str, fields: list[str]) -> str:
    text = str(case.get("passage_text", "")).lower()
    if failure_type == "FALSE_POSITIVE":
        if "forward-looking" in text or "actual results may differ" in text:
            return "legal disclaimer mistaken for guidance"
        if "historical" in text or "previous guidance" in text or "prior guidance" in text:
            return "historical or superseded guidance treated as current"
        if "analyst" in text or "industry" in text or "market forecast" in text:
            return "non-company outlook mistaken for issuer guidance"
        return "hard-negative coverage insufficient"
    if failure_type == "FALSE_NEGATIVE":
        if any(token in text for token in ("reaffirm", "provided", "projects", "target")):
            return "unsupported keyword or action synonym"
        if "between" in text or "bps" in text or "+/-" in text or "–" in text or "—" in text:
            return "number format unsupported"
        if "table" in str(case.get("notes", "")).lower():
            return "table-format guidance unsupported"
        return "metric, period, or numeric wording unsupported"
    if any(field in fields for field in NUMERIC_FIELDS):
        return "numeric extraction mismatch"
    if "period_label" in fields:
        return "period wording mismatch"
    if "metric" in fields:
        return "metric wording mismatch"
    if "management_claim" in fields:
        return "management action mismatch"
    return "structured extraction mismatch"


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _gate_result(name: str, value: float | int) -> dict[str, Any]:
    threshold, operator = INITIAL_GATES[name]
    passed = value >= threshold if operator == ">=" else value <= threshold
    return {
        "value": value,
        "operator": operator,
        "threshold": threshold,
        "passed": passed,
    }


def certify(corpus_path: Path) -> dict[str, Any]:
    cases = _load_jsonl(corpus_path)
    validation_errors = {
        case.get("case_id", f"row-{index + 1}"): errors
        for index, case in enumerate(cases)
        if (errors := _validate_case(case))
    }
    if validation_errors:
        raise ValueError(f"Corpus validation failed: {json.dumps(validation_errors, indent=2)}")
    extractor = GuidanceExtractionService()
    scored = [case for case in cases if case["label"] != "REVIEW_REQUIRED"]
    classification = Counter({"TP": 0, "FP": 0, "FN": 0, "TN": 0})
    field_scores = {field: FieldScore() for field in ROW_FIELDS}
    numeric_score = FieldScore()
    structured_score = FieldScore()
    evidence_score = FieldScore()
    results: list[dict[str, Any]] = []
    fingerprint_rows: list[dict[str, Any]] = []
    failure_severity = Counter({"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})
    for case in scored:
        base_locator = (
            f"{case['accession']}/{case['document_name']}/{case['visible_text_locator']}"
        )
        all_actual = [
            _actual_row(row)
            for row in extractor.extract(str(case["passage_text"]), locator=base_locator)
        ]
        actual = [row for row in all_actual if _accepted(row)]
        expected = _expected_rows(case)
        expected_positive = case["label"] == "POSITIVE"
        actual_positive = bool(actual)
        classification_key = (
            "TP"
            if expected_positive and actual_positive
            else "FN"
            if expected_positive
            else "FP"
            if actual_positive
            else "TN"
        )
        classification[classification_key] += 1
        matches, unexpected_rows = _match_rows(expected, actual)
        match_details: list[dict[str, Any]] = []
        all_fields_correct = bool(expected)
        for expected_row, actual_row, mismatched in matches:
            for field in ROW_FIELDS:
                field_scores[field].total += 1
                if field not in mismatched:
                    field_scores[field].correct += 1
            numeric_score.total += 1
            if not any(field in mismatched for field in NUMERIC_FIELDS):
                numeric_score.correct += 1
            evidence_verified = bool(
                actual_row
                and actual_row["matched_text_sha256"] == case["passage_sha256"]
                and actual_row["evidence_locator"].startswith(base_locator + "#paragraph-")
            )
            evidence_score.total += 1
            evidence_score.correct += int(evidence_verified)
            row_exact = not mismatched and evidence_verified
            all_fields_correct = all_fields_correct and row_exact
            match_details.append(
                {
                    "expected": expected_row,
                    "actual": actual_row,
                    "mismatched_fields": mismatched,
                    "evidence_verified": evidence_verified,
                    "exact_match": row_exact,
                }
            )
        if expected:
            structured_score.total += 1
            if all_fields_correct and not unexpected_rows and len(matches) == len(expected):
                structured_score.correct += 1
        failure_type = None
        mismatch_fields = sorted(
            {field for match in match_details for field in match["mismatched_fields"]}
        )
        evidence_failed = any(not match["evidence_verified"] for match in match_details)
        if classification_key == "FP":
            failure_type = "FALSE_POSITIVE"
        elif classification_key == "FN":
            failure_type = "FALSE_NEGATIVE"
        elif expected_positive and (mismatch_fields or unexpected_rows or evidence_failed):
            failure_type = "FIELD_MISMATCH"
        severity = None
        root_cause = None
        if failure_type:
            effective_fields = [*mismatch_fields]
            if evidence_failed:
                effective_fields.append("evidence_locator")
            severity = _severity(case, effective_fields, bool(unexpected_rows))
            failure_severity[severity] += 1
            root_cause = _root_cause(case, failure_type, effective_fields)
        case_passed = classification_key in {"TP", "TN"} and not (
            expected_positive and (mismatch_fields or unexpected_rows or evidence_failed)
        )
        result = {
            "case_id": case["case_id"],
            "company": case["company"],
            "ticker": case["ticker"],
            "cik": case["cik"],
            "accession": case["accession"],
            "filing_date": case["filing_date"],
            "form": case["form"],
            "document_name": case["document_name"],
            "visible_text_locator": case["visible_text_locator"],
            "passage_text": case["passage_text"],
            "label": case["label"],
            "classification": classification_key,
            "expected_rows": expected,
            "actual_rows": actual,
            "all_actual_rows": all_actual,
            "matches": match_details,
            "unexpected_actual_rows": unexpected_rows,
            "passed": case_passed,
            "failure_type": failure_type,
            "severity": severity,
            "probable_root_cause": root_cause,
        }
        results.append(result)
        fingerprint_rows.append(
            {"case_id": case["case_id"], "accepted_actual_rows": actual}
        )
    tp, fp, fn, tn = (
        classification["TP"],
        classification["FP"],
        classification["FN"],
        classification["TN"],
    )
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    f1 = _ratio(2 * precision * recall, precision + recall)
    positive_count = sum(case["label"] == "POSITIVE" for case in scored)
    negative_count = sum(case["label"] == "NEGATIVE" for case in scored)
    metrics = {
        "classification": {
            "TP": tp,
            "FP": fp,
            "FN": fn,
            "TN": tn,
            "precision": precision,
            "recall": recall,
            "specificity": specificity,
            "f1": f1,
        },
        "field_exact_match": {
            field: {"correct": score.correct, "total": score.total, "rate": score.rate}
            for field, score in field_scores.items()
        },
        "numeric_values": asdict(numeric_score) | {"rate": numeric_score.rate},
        "evidence_locator": asdict(evidence_score) | {"rate": evidence_score.rate},
        "structured_exact_match": asdict(structured_score) | {"rate": structured_score.rate},
    }
    gate_values = {
        "reviewed_cases": len(scored),
        "positive_cases": positive_count,
        "negative_cases": negative_count,
        "precision": precision,
        "false_positives": fp,
        "recall": recall,
        "numeric_values": numeric_score.rate,
        "metric": field_scores["metric"].rate,
        "period_label": field_scores["period_label"].rate,
        "unit": field_scores["unit"].rate,
        "currency": field_scores["currency"].rate,
    }
    gates = {name: _gate_result(name, value) for name, value in gate_values.items()}
    no_critical_fp = not any(
        row["classification"] == "FP" and row["severity"] == "CRITICAL" for row in results
    )
    gates["no_critical_false_positive"] = {
        "value": no_critical_fp,
        "operator": "==",
        "threshold": True,
        "passed": no_critical_fp,
    }
    fingerprint = _sha256_text(
        json.dumps(fingerprint_rows, sort_keys=True, separators=(",", ":"))
    )
    return {
        "artifact_type": "sec_guidance_real_corpus_certification",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "corpus_path": corpus_path.as_posix(),
        "corpus_sha256": _sha256_text(corpus_path.read_text(encoding="utf-8")),
        "corpus": {
            "issuers": len({case["cik"] for case in cases}),
            "filings": len({case["accession"] for case in cases}),
            "passages": len(cases),
            "positive": positive_count,
            "negative": negative_count,
            "review_required": len(cases) - len(scored),
        },
        "versions": {
            "git_sha": _git_sha(),
            "processor_signature": sec_guidance_processor_signature(),
            "DOCUMENT_PARSER_VERSION": DOCUMENT_PARSER_VERSION,
            "GUIDANCE_EXTRACTOR_VERSION": GUIDANCE_EXTRACTOR_VERSION,
            "EVIDENCE_LOCATOR_VERSION": EVIDENCE_LOCATOR_VERSION,
            "FILING_SELECTION_POLICY_VERSION": FILING_SELECTION_POLICY_VERSION,
        },
        "output_fingerprint_sha256": fingerprint,
        "metrics": metrics,
        "gates": gates,
        "failure_severity": dict(failure_severity),
        "certified": all(gate["passed"] for gate in gates.values()),
        "results": results,
    }


def _format_row(row: dict[str, Any] | None) -> str:
    if not row:
        return "<none>"
    return ", ".join(f"{field}={row.get(field)}" for field in ROW_FIELDS)


def _markdown(report: dict[str, Any], *, baseline_report: dict[str, Any] | None) -> str:
    corpus = report["corpus"]
    classification = report["metrics"]["classification"]
    fields = report["metrics"]["field_exact_match"]
    lines = [
        "# Real SEC Guidance Corpus Certification",
        "",
        "## Corpus summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Issuers | {corpus['issuers']} |",
        f"| Filings | {corpus['filings']} |",
        f"| Passages | {corpus['passages']} |",
        f"| Positive | {corpus['positive']} |",
        f"| Negative | {corpus['negative']} |",
        f"| Review required | {corpus['review_required']} |",
        "",
        "## Classification",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for key in ("TP", "FP", "FN", "TN", "precision", "recall", "specificity", "f1"):
        value = classification[key]
        formatted = f"{value:.6f}" if isinstance(value, float) else str(value)
        lines.append(f"| {key} | {formatted} |")
    lines.extend(
        [
            "",
            "## Structured extraction",
            "",
            "| Field | Exact-match |",
            "|---|---:|",
        ]
    )
    display_fields = {
        "Metric": fields["metric"]["rate"],
        "Period": fields["period_label"]["rate"],
        "Numeric values": report["metrics"]["numeric_values"]["rate"],
        "Unit": fields["unit"]["rate"],
        "Currency": fields["currency"]["rate"],
        "Management claim": fields["management_claim"]["rate"],
        "Evidence locator": report["metrics"]["evidence_locator"]["rate"],
        "All fields": report["metrics"]["structured_exact_match"]["rate"],
    }
    lines.extend(f"| {name} | {rate:.6f} |" for name, rate in display_fields.items())
    lines.extend(
        [
            "",
            "## Failure severity",
            "",
            "| Severity | Count |",
            "|---|---:|",
        ]
    )
    for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        lines.append(f"| {severity} | {report['failure_severity'].get(severity, 0)} |")
    lines.extend(
        [
            "",
            "## Version",
            "",
            "| Item | Value |",
            "|---|---|",
            f"| Git SHA | {report['versions']['git_sha']} |",
            f"| Processor signature | {report['versions']['processor_signature']} |",
            f"| Parser version | {report['versions']['DOCUMENT_PARSER_VERSION']} |",
            f"| Extractor version | {report['versions']['GUIDANCE_EXTRACTOR_VERSION']} |",
            f"| Locator version | {report['versions']['EVIDENCE_LOCATOR_VERSION']} |",
            "| Filing-selection version | "
            f"{report['versions']['FILING_SELECTION_POLICY_VERSION']} |",
            "",
            "## Acceptance gates",
            "",
            "| Gate | Actual | Requirement | Result |",
            "|---|---:|---:|---|",
        ]
    )
    for name, gate in report["gates"].items():
        lines.append(
            f"| {name} | {gate['value']} | {gate['operator']} {gate['threshold']} | "
            f"{'PASS' if gate['passed'] else 'FAIL'} |"
        )
    if baseline_report:
        lines.extend(
            [
                "",
                "## Baseline versus candidate",
                "",
                "| Metric | Baseline | Candidate | Delta |",
                "|---|---:|---:|---:|",
            ]
        )
        baseline_metrics = baseline_report["metrics"]
        comparisons = (
            (
                "Precision",
                baseline_metrics["classification"]["precision"],
                classification["precision"],
            ),
            ("Recall", baseline_metrics["classification"]["recall"], classification["recall"]),
            ("False positives", baseline_metrics["classification"]["FP"], classification["FP"]),
            ("False negatives", baseline_metrics["classification"]["FN"], classification["FN"]),
            (
                "Structured exact match",
                baseline_metrics["structured_exact_match"]["rate"],
                report["metrics"]["structured_exact_match"]["rate"],
            ),
            (
                "Metric accuracy",
                baseline_metrics["field_exact_match"]["metric"]["rate"],
                fields["metric"]["rate"],
            ),
            (
                "Period accuracy",
                baseline_metrics["field_exact_match"]["period_label"]["rate"],
                fields["period_label"]["rate"],
            ),
            (
                "Numeric accuracy",
                baseline_metrics["numeric_values"]["rate"],
                report["metrics"]["numeric_values"]["rate"],
            ),
            (
                "Unit accuracy",
                baseline_metrics["field_exact_match"]["unit"]["rate"],
                fields["unit"]["rate"],
            ),
            (
                "Currency accuracy",
                baseline_metrics["field_exact_match"]["currency"]["rate"],
                fields["currency"]["rate"],
            ),
            (
                "Management-claim accuracy",
                baseline_metrics["field_exact_match"]["management_claim"]["rate"],
                fields["management_claim"]["rate"],
            ),
        )
        for name, old, new in comparisons:
            if isinstance(old, int) and isinstance(new, int):
                lines.append(f"| {name} | {old} | {new} | {new - old:+d} |")
            else:
                lines.append(f"| {name} | {old:.6f} | {new:.6f} | {new - old:+.6f} |")
    failures = [row for row in report["results"] if not row["passed"]]
    lines.extend(["", "## Failures", ""])
    if not failures:
        lines.append("No scored failures.")
    for row in failures:
        lines.extend(
            [
                f"### {row['case_id']} — {row['failure_type']} ({row['severity']})",
                "",
                f"Ticker: {row['ticker']}",
                f"CIK: {row['cik']}",
                f"Accession: {row['accession']}",
                f"Form: {row['form']}",
                f"Document: {row['document_name']}",
                f"Locator: {row['visible_text_locator']}",
                f"Probable root cause: {row['probable_root_cause']}",
                "",
                "INPUT:",
                "",
                row["passage_text"],
                "",
                "EXPECTED:",
                "",
                "; ".join(_format_row(item) for item in row["expected_rows"]) or "<negative>",
                "",
                "ACTUAL:",
                "",
                "; ".join(_format_row(item) for item in row["actual_rows"]) or "<none>",
                "",
                "RESULT: FAIL",
                "",
            ]
        )
    lines.extend(["## Human-readable proof samples", ""])
    samples = report["results"][:20]
    for row in samples:
        lines.extend(
            [
                f"### {row['case_id']}",
                "",
                f"Ticker: {row['ticker']}",
                f"CIK: {row['cik']}",
                f"Accession: {row['accession']}",
                f"Form: {row['form']}",
                f"Document: {row['document_name']}",
                f"Locator: {row['visible_text_locator']}",
                "",
                "INPUT:",
                "",
                row["passage_text"],
                "",
                "EXPECTED:",
                "",
                "; ".join(_format_row(item) for item in row["expected_rows"]) or "<negative>",
                "",
                "ACTUAL:",
                "",
                "; ".join(_format_row(item) for item in row["actual_rows"]) or "<none>",
                "",
                f"RESULT: {'PASS' if row['passed'] else 'FAIL'}",
                "",
            ]
        )
    decision = (
        "REAL SEC GUIDANCE EXTRACTOR CERTIFIED"
        if report["certified"]
        else "REAL SEC GUIDANCE EXTRACTOR NOT CERTIFIED"
    )
    lines.extend(["## Final decision", "", f"**{decision}**", ""])
    return "\n".join(lines)


def _manifest_markdown(report: dict[str, Any]) -> str:
    corpus = report["corpus"]
    rows = report["results"]
    forms = sorted({row["form"] for row in rows})
    filing_dates = sorted(row["filing_date"] for row in rows)
    issuers = sorted({(row["ticker"], row["cik"], row["company"]) for row in rows})
    lines = [
        "# Real SEC Guidance Corpus Manifest",
        "",
        "## Provenance",
        "",
        f"- Corpus: `{report['corpus_path']}`",
        f"- Corpus SHA-256: `{report['corpus_sha256']}`",
        "- Source policy: real SEC filing and exhibit passages with accession, CIK, form, "
        "document name, source URL, source-document hash, and visible-text locator retained "
        "per case.",
        "- Review policy: independent Q1-Q9 semantic annotation; extractor output was not "
        "used as gold.",
        "- Scoring policy: `REVIEW_REQUIRED` cases are preserved but excluded from scored "
        "metrics.",
        "",
        "## Coverage",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Issuers | {corpus['issuers']} |",
        f"| Filings | {corpus['filings']} |",
        f"| Passages | {corpus['passages']} |",
        f"| Positive | {corpus['positive']} |",
        f"| Negative | {corpus['negative']} |",
        f"| Review required | {corpus['review_required']} |",
        f"| Forms represented | {', '.join(forms)} |",
        f"| Filing-date range | {filing_dates[0]} to {filing_dates[-1]} |",
        "",
        "## Versioned certification boundary",
        "",
        f"- Git SHA: `{report['versions']['git_sha']}`",
        f"- Processor signature: `{report['versions']['processor_signature']}`",
        f"- Parser: `{report['versions']['DOCUMENT_PARSER_VERSION']}`",
        f"- Extractor: `{report['versions']['GUIDANCE_EXTRACTOR_VERSION']}`",
        f"- Evidence locator: `{report['versions']['EVIDENCE_LOCATOR_VERSION']}`",
        "- Filing selection: "
        f"`{report['versions']['FILING_SELECTION_POLICY_VERSION']}`",
        f"- Output fingerprint: `{report['output_fingerprint_sha256']}`",
        "",
        "## Issuers",
        "",
        "| Ticker | CIK | Company |",
        "|---|---|---|",
    ]
    lines.extend(f"| {ticker} | {cik} | {company} |" for ticker, cik, company in issuers)
    lines.append("")
    return "\n".join(lines)


def write_reports(
    report: dict[str, Any],
    *,
    output_dir: Path,
    timestamp: str,
    baseline_report: dict[str, Any] | None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"sec_guidance_real_corpus_certification_{timestamp}.json"
    md_path = output_dir / f"sec_guidance_real_corpus_certification_{timestamp}.md"
    csv_path = output_dir / f"sec_guidance_real_corpus_failures_{timestamp}.csv"
    manifest_path = output_dir / f"sec_guidance_real_corpus_manifest_{timestamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report, baseline_report=baseline_report), encoding="utf-8")
    manifest_path.write_text(_manifest_markdown(report), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "case_id",
            "ticker",
            "cik",
            "accession",
            "form",
            "document_name",
            "visible_text_locator",
            "failure_type",
            "severity",
            "probable_root_cause",
            "passage_text",
            "expected_rows",
            "actual_rows",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in report["results"]:
            if row["passed"]:
                continue
            writer.writerow(
                {
                    key: json.dumps(row[key], ensure_ascii=False)
                    if key in {"expected_rows", "actual_rows"}
                    else row.get(key)
                    for key in fieldnames
                }
            )
    return {
        "json": json_path,
        "markdown": md_path,
        "failures_csv": csv_path,
        "manifest": manifest_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Certify the frozen real-SEC corpus.")
    parser.add_argument(
        "--corpus", default="tests/ceri/fixtures/sec_guidance_real_corpus_v1.jsonl"
    )
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--timestamp")
    parser.add_argument("--baseline-report")
    parser.add_argument("--reference-certification")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = certify(Path(args.corpus))
    if args.reference_certification:
        reference = json.loads(Path(args.reference_certification).read_text(encoding="utf-8"))
        if (
            reference["versions"]["processor_signature"]
            == report["versions"]["processor_signature"]
            and reference["output_fingerprint_sha256"] != report["output_fingerprint_sha256"]
        ):
            raise SystemExit(
                "Certification outputs changed while the processor signature remained unchanged."
            )
    baseline = (
        json.loads(Path(args.baseline_report).read_text(encoding="utf-8"))
        if args.baseline_report
        else None
    )
    timestamp = args.timestamp or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = write_reports(
        report,
        output_dir=Path(args.output_dir),
        timestamp=timestamp,
        baseline_report=baseline,
    )
    print(json.dumps({key: path.as_posix() for key, path in paths.items()}, indent=2))
    print(
        "REAL SEC GUIDANCE EXTRACTOR CERTIFIED"
        if report["certified"]
        else "REAL SEC GUIDANCE EXTRACTOR NOT CERTIFIED"
    )
