from types import SimpleNamespace

from app.services.ceri.evidence_population import evidence_population_summary


class PopulationDb:
    def __init__(self) -> None:
        self.values = iter((209, 0))

    def scalar(self, _statement):
        return next(self.values)

    def execute(self, _statement):
        return SimpleNamespace(all=lambda: [("PARENT_PIPELINE_UNSUCCESSFUL", 209)])


def test_population_reports_captured_excluded_without_promoting_it() -> None:
    summary = evidence_population_summary(PopulationDb(), run_id=30)  # type: ignore[arg-type]

    assert summary == {
        "state": "CAPTURED_EXCLUDED",
        "captured_count": 209,
        "eligible_count": 0,
        "excluded_count": 209,
        "quarantined_count": 0,
        "exclusion_reasons": [
            {"reason": "PARENT_PIPELINE_UNSUCCESSFUL", "count": 209}
        ],
    }
