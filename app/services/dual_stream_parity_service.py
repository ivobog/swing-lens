from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from app.services.pipeline_baseline import normalize_parity_payload

PARITY_SCHEMA_VERSION = "dual-stream-parity-v1"

REQUIRED_PARITY_CATEGORIES = frozenset(
    {
        "frozen_run_78",
        "splits_dividends",
        "missing_zero_volume",
        "stale_partial_series",
        "revised_historical_bars",
        "insufficient_history",
        "benchmark_sector_proxy",
    }
)


@dataclass(frozen=True)
class DualStreamObservation:
    """Comparable output from one dual-stream or candidate-stream execution."""

    source_ohlcv: Any
    adjusted_price_behavior: Any
    volume_indicators: Any
    technical_scores: Any
    technical_flags: Any
    setup_latest_bar: Any
    missing_data_behavior: Any
    revision_lineage: Any


@dataclass(frozen=True)
class ParityCorpusCase:
    case_id: str
    category: str
    input_payload: Any


@dataclass(frozen=True)
class ParityCaseResult:
    case_id: str
    category: str
    passed: bool
    differences: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class DualStreamParityReport:
    schema_version: str
    passed: bool
    production_reduction_eligible: bool
    product_approval: bool
    required_categories: tuple[str, ...]
    covered_categories: tuple[str, ...]
    missing_categories: tuple[str, ...]
    cases: tuple[ParityCaseResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DualStreamRunner = Callable[[ParityCorpusCase], DualStreamObservation]


class DualStreamParityEvaluator:
    """Run parity checks without changing the production IB request path."""

    def __init__(
        self,
        *,
        required_categories: Iterable[str] = REQUIRED_PARITY_CATEGORIES,
    ) -> None:
        categories = tuple(sorted({str(category) for category in required_categories}))
        if not categories:
            raise ValueError("At least one parity corpus category is required.")
        self.required_categories = categories

    def evaluate(
        self,
        corpus: Iterable[ParityCorpusCase],
        *,
        dual_stream_runner: DualStreamRunner,
        candidate_stream_runner: DualStreamRunner,
        product_approval: bool = False,
    ) -> DualStreamParityReport:
        results: list[ParityCaseResult] = []
        covered_categories: set[str] = set()

        for case in corpus:
            covered_categories.add(case.category)
            results.append(
                self._evaluate_case(
                    case,
                    dual_stream_runner=dual_stream_runner,
                    candidate_stream_runner=candidate_stream_runner,
                )
            )

        missing_categories = tuple(
            category for category in self.required_categories if category not in covered_categories
        )
        all_cases_passed = bool(results) and all(result.passed for result in results)
        passed = all_cases_passed and not missing_categories
        return DualStreamParityReport(
            schema_version=PARITY_SCHEMA_VERSION,
            passed=passed,
            production_reduction_eligible=passed and product_approval,
            product_approval=product_approval,
            required_categories=self.required_categories,
            covered_categories=tuple(sorted(covered_categories)),
            missing_categories=missing_categories,
            cases=tuple(results),
        )

    def _evaluate_case(
        self,
        case: ParityCorpusCase,
        *,
        dual_stream_runner: DualStreamRunner,
        candidate_stream_runner: DualStreamRunner,
    ) -> ParityCaseResult:
        try:
            dual = dual_stream_runner(case)
            candidate = candidate_stream_runner(case)
        except Exception as exc:
            return ParityCaseResult(
                case_id=case.case_id,
                category=case.category,
                passed=False,
                error=str(exc).replace("\n", " ")[:500],
            )

        differences = tuple(
            _diff_paths(
                normalize_parity_payload(asdict(dual)),
                normalize_parity_payload(asdict(candidate)),
            )
        )
        return ParityCaseResult(
            case_id=case.case_id,
            category=case.category,
            passed=not differences,
            differences=differences,
        )


def _diff_paths(left: Any, right: Any, path: str = "$") -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, Mapping):
        differences: list[str] = []
        keys = sorted(set(left) | set(right), key=str)
        for key in keys:
            child_path = f"{path}.{key}"
            if key not in left or key not in right:
                differences.append(child_path)
            else:
                differences.extend(_diff_paths(left[key], right[key], child_path))
        return differences
    if isinstance(left, list):
        differences = []
        if len(left) != len(right):
            differences.append(path)
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=False)):
            differences.extend(_diff_paths(left_item, right_item, f"{path}[{index}]"))
        return differences
    return [] if left == right else [path]
