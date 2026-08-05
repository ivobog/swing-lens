from app.services.dual_stream_parity_service import (
    REQUIRED_PARITY_CATEGORIES,
    DualStreamObservation,
    DualStreamParityEvaluator,
    ParityCorpusCase,
)


def _observation(case: ParityCorpusCase) -> DualStreamObservation:
    return DualStreamObservation(
        source_ohlcv={"ticker": case.input_payload["ticker"], "rows": 252},
        adjusted_price_behavior={"split_adjusted": True},
        volume_indicators={"relative_volume": 1.2},
        technical_scores={"technical_score": 7.5},
        technical_flags=["trend_positive"],
        setup_latest_bar={"price_bar_id": 42, "source": "TRADES"},
        missing_data_behavior={"status": "ready"},
        revision_lineage={"series_version": 3},
    )


def _corpus() -> list[ParityCorpusCase]:
    return [
        ParityCorpusCase(category, category, {"ticker": "MSFT"})
        for category in sorted(REQUIRED_PARITY_CATEGORIES)
    ]


def test_parity_evaluator_requires_full_corpus_and_product_approval() -> None:
    evaluator = DualStreamParityEvaluator()

    report = evaluator.evaluate(
        _corpus(),
        dual_stream_runner=_observation,
        candidate_stream_runner=_observation,
    )

    assert report.passed is True
    assert report.production_reduction_eligible is False
    assert report.product_approval is False
    assert report.missing_categories == ()
    assert all(case.passed for case in report.cases)

    approved = evaluator.evaluate(
        _corpus(),
        dual_stream_runner=_observation,
        candidate_stream_runner=_observation,
        product_approval=True,
    )
    assert approved.production_reduction_eligible is True


def test_parity_evaluator_reports_nested_output_drift() -> None:
    evaluator = DualStreamParityEvaluator()

    def candidate(case: ParityCorpusCase) -> DualStreamObservation:
        observation = _observation(case)
        return DualStreamObservation(
            **{
                **observation.__dict__,
                "volume_indicators": {"relative_volume": 1.3},
            }
        )

    report = evaluator.evaluate(
        _corpus(),
        dual_stream_runner=_observation,
        candidate_stream_runner=candidate,
        product_approval=True,
    )

    assert report.passed is False
    assert report.production_reduction_eligible is False
    assert report.cases[0].passed is False
    assert "$.volume_indicators.relative_volume" in report.cases[0].differences


def test_parity_evaluator_fails_closed_for_missing_categories_and_runner_errors() -> None:
    evaluator = DualStreamParityEvaluator()
    corpus = [ParityCorpusCase("only-case", "frozen_run_78", {"ticker": "MSFT"})]

    def failing_runner(_case: ParityCorpusCase) -> DualStreamObservation:
        raise RuntimeError("candidate unavailable")

    report = evaluator.evaluate(
        corpus,
        dual_stream_runner=_observation,
        candidate_stream_runner=failing_runner,
    )

    assert report.passed is False
    assert report.production_reduction_eligible is False
    assert set(report.missing_categories) == REQUIRED_PARITY_CATEGORIES - {"frozen_run_78"}
    assert report.cases[0].error == "candidate unavailable"
