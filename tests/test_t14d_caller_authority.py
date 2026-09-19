"""Caller rejection must precede any business work or external acquisition."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.routers import market_regime_routes, run_routes, sector_rotation_routes
from app.services.configuration_delivery import anchored_job_configuration
from app.services.entrypoint_authority import EntryPointAuthorityError
from app.services.market_calculation_context_service import (
    PipelineCalculationContextError,
    market_context_for_pipeline,
)


@pytest.mark.parametrize(
    "operation,kwargs",
    [
        (run_routes.recalculate_fundamentals_action, {}),
        (run_routes.refresh_technicals_action, {}),
        (run_routes.refresh_combined_results_action, {}),
        (run_routes.refresh_all_ranking_profiles_action, {}),
        (run_routes.refresh_ranking_profile_action, {"profile_name": "balanced"}),
        (market_regime_routes.recalculate_run_market_regime_api, {}),
        (sector_rotation_routes.recalculate_run_sector_rotation_api, {}),
    ],
)
def test_unbound_standalone_routes_are_formally_retired(monkeypatch, operation, kwargs):
    monkeypatch.setattr(operation.__module__ + "._require_run", lambda *_: None)
    with pytest.raises(HTTPException) as error:
        operation(run_id=7, db=object(), **kwargs)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "STANDALONE_MUTATION_RETIRED"
    assert "new Full Pipeline" in error.value.detail["message"]


def test_reduced_pipeline_rejects_before_external_probe_or_mutation(monkeypatch):
    monkeypatch.setattr(
        run_routes, "get_settings", lambda: SimpleNamespace(use_durable_pipeline=False)
    )
    monkeypatch.setattr(run_routes, "check_status", lambda **_: pytest.fail("gateway probe ran"))
    monkeypatch.setattr(run_routes, "start_pipeline", lambda *_a, **_k: pytest.fail("enqueue ran"))
    with pytest.raises(HTTPException) as error:
        run_routes.run_full_pipeline_action(run_id=7, db=object())
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "REDUCED_PIPELINE_RETIRED"


@pytest.mark.parametrize("kind", ["regime", "sector"])
def test_build_on_miss_is_read_only(monkeypatch, kind):
    if kind == "regime":
        monkeypatch.setattr(
            market_regime_routes.MarketRegimeRepository, "latest_for_run", lambda *_: None
        )
        read = market_regime_routes._run_snapshot_or_calculate
    else:
        monkeypatch.setattr(
            sector_rotation_routes.SectorRotationRepository, "latest_for_run", lambda *_: None
        )
        read = sector_rotation_routes._run_payload_or_calculate
    with pytest.raises(HTTPException) as error:
        read(object(), 7)
    assert error.value.status_code == 404


def test_existing_pipeline_cannot_freeze_replacement_context_on_execution(monkeypatch):
    with Session() as db:
        monkeypatch.setattr(db, "scalar", lambda *_: None)
        with pytest.raises(
            PipelineCalculationContextError, match="PIPELINE_FROZEN_CONTEXT_REQUIRED"
        ):
            market_context_for_pipeline(db, SimpleNamespace(id=9))
        assert not db.new


def test_direct_durable_handler_rejects_missing_anchor():
    @anchored_job_configuration
    def operation(db, job):
        pytest.fail("unanchored durable handler ran")

    with Session() as db, pytest.raises(ValueError, match="MISSING_CONFIGURATION_ANCHOR"):
        operation(db, SimpleNamespace(payload_json={}))


@pytest.mark.parametrize(
    "file,symbol",
    [
        ("scripts/winner_candidate_estimates.py", "write"),
        ("scripts/winner_clean_reconstruction.py", "build_candidate"),
        ("app/services/winner_probability/pre11_activation_service.py", "activate"),
        (
            "app/services/winner_probability/pre11_compatibility_service.py",
            "persist_decisions_and_replays",
        ),
        (
            "app/services/winner_probability/target_stop_scope_repair_service.py",
            "apply_target_stop_scope_repair",
        ),
        ("app/services/ceri/controlled_replay_service.py", "replay"),
    ],
)
def test_retired_legacy_write_is_unconditional_before_any_database_work(file, symbol):
    import ast

    tree = ast.parse(Path(file).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == symbol)
    statements = node.body
    if isinstance(statements[0], ast.If):
        assert ast.unparse(statements[0].test) == "not approve_write"
        statements = statements[1:]
    assert isinstance(statements[0], ast.Expr)
    call = statements[0].value
    assert isinstance(call, ast.Call) and ast.unparse(call.func) == "reject_legacy_mutation"


def test_incident_canary_rejects_before_opening_database():
    from datetime import UTC, datetime
    from unittest.mock import patch

    from app.services.winner_probability.maturation_canary_service import (
        execute_reviewed_maturation_canary,
    )

    with patch("app.services.winner_probability.maturation_canary_service.verify_canary_approval"):
        with pytest.raises(EntryPointAuthorityError, match="LEGACY_MUTATION_RETIRED"):
            execute_reviewed_maturation_canary(
                lambda: pytest.fail("database opened"),
                {},
                reviewed_manifest_hash="reviewed",
                approve_write=True,
                actor="test",
                request_key="test",
                now=datetime.now(UTC),
            )


def test_retirement_is_a_failure_not_success():
    from app.services.entrypoint_authority import reject_legacy_mutation

    with pytest.raises(EntryPointAuthorityError, match="LEGACY_MUTATION_RETIRED"):
        reject_legacy_mutation("legacy SQL")


@pytest.mark.parametrize("script", ["profile_sec_performance", "certify_sec_incremental_ingestion"])
def test_callable_qa_helper_rejects_non_disposable_target_before_provider_work(monkeypatch, script):
    import importlib

    module = importlib.import_module("scripts." + script)
    monkeypatch.setenv("SWINGLENS_DATABASE_SAFETY_CONTEXT", "DISPOSABLE_TEST")
    monkeypatch.setattr(
        module,
        "engine",
        SimpleNamespace(url="postgresql+psycopg://local@127.0.0.1/swinglens_runtime"),
    )
    with pytest.raises(EntryPointAuthorityError, match="LEGACY_TOOL_DISPOSABLE_DATABASE_REQUIRED"):
        if script == "profile_sec_performance":
            module.run_scenario("test", ("ACME",), "test")
        else:
            module.run_scenario(
                name="test",
                mode=None,
                runtime=None,
                config=None,
                stamp="test",
                tickers=("ACME",),
                requests_per_second=1,
            )
