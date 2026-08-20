from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.ceri_tables import CeriCompany, CeriSecProcessorRelease, CeriSecSyncState
from app.services.ceri.config import CeriConfigError
from app.services.ceri.sec.pipeline_preflight import validate_sec_pipeline_preflight
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.pipeline_prerequisites import (
    CeriBootstrapRequiredError,
    CeriProviderConfigurationError,
)


class Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


class SequentialDb:
    def __init__(self, *responses):
        self.responses = [Rows(values) for values in responses]

    def scalars(self, _statement):
        return self.responses.pop(0)


def _release():
    signature = sec_guidance_processor_signature()
    return CeriSecProcessorRelease(processor_signature=signature, status="ACTIVE")


def _company(ticker: str, cik: int):
    return CeriCompany(ticker=ticker, cik=str(cik).zfill(10))


def _sync(cik: int, signature: str | None = None):
    return CeriSecSyncState(
        cik=str(cik).zfill(10),
        dataset="guidance",
        processor_signature=signature or sec_guidance_processor_signature(),
    )


def test_fully_ready_universe_passes_preflight() -> None:
    report = validate_sec_pipeline_preflight(
        SequentialDb([_release()], [_company("AAA", 1), _company("BBB", 2)], [_sync(1), _sync(2)]),
        tickers=["AAA", "BBB"],
    )

    assert report["readiness"]["complete"] is True
    assert report["readiness"]["ready_tickers"] == 2


def test_disabled_sec_guidance_provider_configuration_blocks(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.ceri.sec.pipeline_preflight.load_ceri_config",
        lambda: SimpleNamespace(
            config_hash="bad-config",
            datasets={},
            providers=SimpleNamespace(capabilities={}),
        ),
    )

    with pytest.raises(CeriProviderConfigurationError) as raised:
        validate_sec_pipeline_preflight(SequentialDb(), tickers=["AAA"])

    assert raised.value.reason_code == "CERI_PROVIDER_CONFIGURATION_INVALID"


def test_unreadable_provider_configuration_is_nonretryable_block(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.ceri.sec.pipeline_preflight.load_ceri_config",
        lambda: (_ for _ in ()).throw(CeriConfigError("missing providers")),
    )

    with pytest.raises(CeriProviderConfigurationError) as raised:
        validate_sec_pipeline_preflight(SequentialDb(), tickers=["AAA"])

    assert raised.value.diagnostics == {"config_error": "missing providers"}


def test_partially_ready_universe_blocks_with_precise_counts() -> None:
    with pytest.raises(CeriBootstrapRequiredError) as raised:
        validate_sec_pipeline_preflight(
            SequentialDb(
                [_release()],
                [_company("AAA", 1), _company("BBB", 2)],
                [_sync(1)],
            ),
            tickers=["AAA", "BBB"],
        )

    readiness = raised.value.diagnostics["readiness"]
    assert readiness["ready_tickers"] == 1
    assert readiness["counts"]["SYNC_STATE_MISSING"] == 1


def test_zero_ready_universe_blocks() -> None:
    with pytest.raises(CeriBootstrapRequiredError) as raised:
        validate_sec_pipeline_preflight(
            SequentialDb(
                [_release()],
                [_company("AAA", 1), _company("BBB", 2)],
                [],
            ),
            tickers=["AAA", "BBB"],
        )

    assert raised.value.diagnostics["readiness"]["ready_tickers"] == 0


def test_unresolved_required_ticker_blocks() -> None:
    with pytest.raises(CeriBootstrapRequiredError) as raised:
        validate_sec_pipeline_preflight(
            SequentialDb([_release()], [], []),
            tickers=["MISSING"],
        )

    assert raised.value.diagnostics["readiness"]["counts"]["UNRESOLVED_MAPPING"] == 1


def test_explicit_not_applicable_ticker_is_accepted() -> None:
    company = CeriCompany(
        ticker="FOREIGN",
        cik=None,
        sec_applicability="NOT_APPLICABLE",
        sec_applicability_reason="reviewed non-SEC security",
    )

    report = validate_sec_pipeline_preflight(
        SequentialDb([_release()], [company]),
        tickers=["FOREIGN"],
    )

    assert report["readiness"]["complete"] is True
    assert report["readiness"]["counts"]["SEC_NOT_APPLICABLE"] == 1


def test_signature_mismatch_is_reported_precisely() -> None:
    with pytest.raises(CeriBootstrapRequiredError) as raised:
        validate_sec_pipeline_preflight(
            SequentialDb([_release()], [_company("AAA", 1)], [_sync(1, "old")]),
            tickers=["AAA"],
        )

    ticker = raised.value.diagnostics["readiness"]["tickers"][0]
    assert ticker["category"] == "SIGNATURE_MISMATCH"
    assert ticker["available_signatures"] == ["old"]


def test_run118_regression_universe_moves_from_136_to_180_ready() -> None:
    tickers = [f"T{index:03d}" for index in range(180)]
    companies = [_company(ticker, index + 1) for index, ticker in enumerate(tickers)]

    with pytest.raises(CeriBootstrapRequiredError) as raised:
        validate_sec_pipeline_preflight(
            SequentialDb(
                [_release()],
                companies,
                [_sync(index + 1) for index in range(136)],
            ),
            tickers=tickers,
        )

    assert raised.value.diagnostics["readiness"]["ready_tickers"] == 136
    assert len(raised.value.diagnostics["readiness"]["blocking_tickers"]) == 44

    report = validate_sec_pipeline_preflight(
        SequentialDb(
            [_release()],
            companies,
            [_sync(index + 1) for index in range(180)],
        ),
        tickers=tickers,
    )
    assert report["readiness"]["ready_tickers"] == 180
