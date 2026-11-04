"""Control projections and retained raw evidence for native sector interaction QA."""

import json
from copy import deepcopy
from datetime import UTC
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models import ceri_tables as ceri
from app.models import tables as models
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.scope_refresh_adoption import require_semantic_authority


def _mapped_value(row, name):
    """Column names in retained SQL can differ from mapped attribute names."""
    column = type(row).__table__.columns.get(name)
    attribute = (
        inspect(type(row)).get_property_by_column(column).key if column is not None else name
    )
    return getattr(row, attribute)


def _comparable_ceri(value, cutoff):
    """Keep financial values exact; validate each execution's native identity.

    CERI deliberately freezes a separate context after provider normalization.
    Its actual instant and derived provenance digests differ across executions.
    Full originals remain in raw SQL; feature business values are compared too.
    """
    cutoff = cutoff.astimezone(UTC)
    if isinstance(value, list):
        return [_comparable_ceri(item, cutoff) for item in value]
    if not isinstance(value, dict):
        if isinstance(value, str) and value in (
            cutoff.isoformat(),
            cutoff.isoformat().replace("+00:00", "Z"),
        ):
            return "<validated-native-ceri-cutoff>"
        return value
    value = deepcopy(value)
    if "calculation_identity" in value:
        identity = value["calculation_identity"]
        assert Canonical.fingerprint(identity) == value["calculation_identity_fingerprint"]
        identity["calculation_context"]["context_fingerprint"]["value"]["digest"] = (
            "<validated-native-context>"
        )
        lineage = identity["source_lineage"]["value"]
        for item in lineage["references"]:
            assert item["artifact_type"] == "ceri-context-input-envelope"
            assert item["artifact_id"] == "sha256:" + item["fingerprint"]["value"]["digest"]
            item["artifact_id"] = "<validated-native-input-envelope>"
        for reference in [
            lineage["aggregate_fingerprint"],
            *[item["fingerprint"] for item in lineage["references"]],
        ]:
            reference["value"]["digest"] = "<native-feature-identity; values-compared>"
        value["calculation_identity_fingerprint"] = "<validated-native-identity>"
    return {key: _comparable_ceri(item, cutoff) for key, item in value.items()}


def capture_refresh_effects(db, pipeline, fetch_run_id, frozen_at):
    """Verify actual admitted recent-window writes, including all 26 series."""
    from datetime import UTC, datetime

    from app.services.price_bar_repository import load_price_bars_frame

    acquisition = db.get(models.IBFetchRun, fetch_run_id)
    assert acquisition.status == "COMPLETED" and acquisition.success_count == 26
    cutoff = cutoff_from_row(pipeline.market_calculation_context)
    items = list(
        db.scalars(
            select(models.IBFetchItem).where(
                models.IBFetchItem.fetch_run_id == fetch_run_id,
            )
        )
    )
    symbols = {
        "SPY",
        "QQQ",
        "XLB",
        "XLC",
        "XLE",
        "XLF",
        "XLI",
        "XLK",
        "XLP",
        "XLRE",
        "XLU",
        "XLV",
        "XLY",
    }
    expected = {(symbol, basis) for symbol in symbols for basis in ("ADJUSTED_LAST", "TRADES")}
    assert {(item.ticker, item.what_to_show) for item in items} == expected
    revisions = list(
        db.scalars(
            select(models.PriceBarRevision).where(
                models.PriceBarRevision.fetch_run_id == fetch_run_id,
                models.PriceBarRevision.observed_at > frozen_at,
            )
        )
    )
    prices = {}
    for item in items:
        owned = [row for row in revisions if row.fetch_item_id == item.id]
        assert len(owned) == item.revised and item.revised > 0
        assert all(
            row.ticker == item.ticker and row.what_to_show == item.what_to_show for row in owned
        )
        assert any(row.bar_date == cutoff.latest_completed_session for row in owned)
        assert any(pipeline.started_at < row.observed_at < pipeline.completed_at for row in owned)
        frozen, current = [
            load_price_bars_frame(
                db,
                item.ticker,
                item.what_to_show,
                max_session=cutoff.latest_completed_session,
                as_of=instant,
            )
            for instant in (frozen_at, datetime.now(UTC))
        ]
        before, after = float(frozen.iloc[-1]["close"]), float(current.iloc[-1]["close"])
        assert abs(after - before - 7) < 1e-8
        prices[item.ticker + "/" + item.what_to_show] = {
            "frozen_close": before,
            "fresh_close": after,
            "revised": item.revised,
            "duration": item.duration,
            "admitted_window": item.decision_metadata_json,
            "revision_ids": [row.id for row in owned],
        }
    return {
        "refresh_effects": prices,
        "refresh_revision_count": len(revisions),
        "shared_revisions": [
            {
                column.name: _mapped_value(row, column.name)
                for column in models.PriceBarRevision.__table__.columns
            }
            for row in revisions
            if row.ticker in ("SPY", "QQQ")
        ],
    }


def capture_result(engine, run_id, pipeline_id, destination, *, winner_config=None):
    """Compare approved financial fields; retain complete SQL rows separately."""
    source = Path(__file__).parent / "e2e/single_run_certification/golden/v3/oracle-completion"
    fields = json.loads((source / "core-expected.json").read_text())
    raw, financial, links = {}, {}, []
    with Session(engine) as db:
        pipeline = db.get(models.PipelineRun, pipeline_id)
        context = pipeline.market_calculation_context
        cutoff = cutoff_from_row(context)
        shared = {}
        for symbol in ("SPY", "QQQ"):
            frames = load_preferred_ohlcv_frames(
                db,
                symbol,
                max_session=cutoff.latest_completed_session,
                as_of=cutoff.cutoff_at,
                calculation_context_id=context.id,
            )
            shared[symbol] = [
                {"data": frame.to_dict("records"), "attrs": frame.attrs} for frame in frames
            ]
        aliases = {
            "combined": {"warning_flags": "warning_flags_json"},
            "ranking": {
                "profile": "ranking_profile",
                "gates": "gates_json",
                "penalties": "penalties_json",
                "warning_flags": "warning_flags_json",
            },
        }
        for name, model in (
            ("technical", models.TechnicalScore),
            ("combined", models.CombinedResult),
            ("ranking", models.RankingResult),
            ("fundamental", models.FundamentalScore),
        ):
            rows = list(db.scalars(select(model).where(model.run_id == run_id)))
            assert len(rows) == (50 if name == "ranking" else 10), name
            raw[name] = [
                {col.name: _mapped_value(row, col.name) for col in model.__table__.columns}
                for row in rows
            ]
            projected = []
            for row in rows:
                if name == "fundamental":
                    keys = [
                        col.name
                        for col in model.__table__.columns
                        if not col.foreign_keys
                        and col.name
                        not in (
                            "id",
                            "created_at",
                            "updated_at",
                            "acquisition_plan_id",
                            "scope_id",
                            "refresh_cycle_id",
                        )
                    ]
                else:
                    keys = fields[name][0]
                values = {}
                for key in keys:
                    if key == "base_dual_score":
                        value = row.v4_debug_json["debug"]["base_dual_score"]
                    else:
                        value = _mapped_value(row, aliases.get(name, {}).get(key, key))
                    values[key] = value
                projected.append(values)
            financial[name] = sorted(
                projected, key=lambda item: (item.get("ticker", ""), item.get("profile", ""))
            )
        technical_manifests = list(
            db.scalars(
                select(models.TechnicalSourceManifest).where(
                    models.TechnicalSourceManifest.run_id == run_id,
                    models.TechnicalSourceManifest.pipeline_run_id == pipeline_id,
                )
            )
        )
        assert technical_manifests
        assert all(row.calculation_context_id == context.id for row in technical_manifests)
        raw["technical_source_manifests"] = [row.manifest_json for row in technical_manifests]
        financial["frozen_market_source_manifests"] = sorted(
            [
                source
                for row in technical_manifests
                for source in row.manifest_json["sources"]
                if source["manifest"]["ticker"] in ("SPY", "QQQ")
            ],
            key=str,
        )
        assert financial["frozen_market_source_manifests"]
        evidence = list(
            db.scalars(
                select(models.CoreCalculationEvidence).where(
                    models.CoreCalculationEvidence.run_id == run_id
                )
            )
        )
        assert evidence
        ceri_context_id = pipeline.result_json["ceri_calculation_context_id"]
        ceri_context = db.get(models.MarketCalculationContext, ceri_context_id)
        raw["ceri_context"] = {
            col.name: _mapped_value(ceri_context, col.name)
            for col in models.MarketCalculationContext.__table__.columns
        }
        ceri_captures = list(
            db.scalars(
                select(ceri.CeriScoreSnapshot).where(ceri.CeriScoreSnapshot.run_id == run_id)
            )
        )
        assert len(ceri_captures) == 10
        ceri_evidence = {row.evidence_id: row for row in ceri_captures}
        assert all(row.calculation_context_id == ceri_context_id for row in ceri_captures)
        raw["core_evidence"] = [
            {
                col.name: _mapped_value(row, col.name)
                for col in models.CoreCalculationEvidence.__table__.columns
            }
            for row in evidence
        ]
        for row in evidence:
            identity = row.calculation_identity_json
            assert identity["ownership"]["run_id"]["value"] == run_id
            if row.artifact_kind == "CERI":
                # The retained native CERI contract declares a separate context
                # and UNKNOWN pipeline dimension; its capture row is the issuer.
                assert row.id in ceri_evidence
                assert identity["ownership"]["pipeline_id"] == {"state": "UNKNOWN"}
                expected_context = ceri_context_id
            else:
                assert identity["ownership"]["pipeline_id"]["value"] == pipeline_id
                expected_context = context.id
            assert (
                identity["calculation_context"]["market_calculation_context_id"]["value"]
                == expected_context
            )
            sources = list(
                db.scalars(
                    select(models.CoreCalculationEvidenceSource).where(
                        models.CoreCalculationEvidenceSource.evidence_id == row.id
                    )
                )
            )
            assert row.source_evidence_ids_json == {
                item.source_role: item.source_evidence_id for item in sources
            }
            source_roles = []
            for item in sources:
                parent = db.get(models.CoreCalculationEvidence, item.source_evidence_id)
                assert parent is not None
                source_roles.append((item.source_role, parent.artifact_kind, parent.ticker))
            links.append((row.artifact_kind, row.ticker, sorted(source_roles)))
        for name, model, rows in (
            ("ceri_captures", ceri.CeriScoreSnapshot, ceri_captures),
            *[
                (
                    name,
                    model,
                    list(
                        db.scalars(
                            select(model).where(
                                model.calculation_context_id == ceri_context_id,
                                model.ownership_mode == "PIPELINE",
                            )
                        )
                    ),
                )
                for name, model in (
                    ("ceri_revision_features", ceri.CeriRevisionFeature),
                    ("ceri_derived_features", ceri.CeriDerivedFeature),
                )
            ],
            (
                "ceri_price_response",
                ceri.CeriPriceResponseFeature,
                list(
                    db.scalars(
                        select(ceri.CeriPriceResponseFeature).where(
                            ceri.CeriPriceResponseFeature.calculation_context_id == ceri_context_id,
                            ceri.CeriPriceResponseFeature.ownership_mode == "PIPELINE",
                        )
                    )
                ),
            ),
        ):
            assert rows, name
            raw[name] = [
                {col.name: _mapped_value(row, col.name) for col in model.__table__.columns}
                for row in rows
            ]
            # Receipt/context identifiers and operational instants are retained
            # above. Compare every business value, including reaction metrics.
            financial[name] = sorted(
                [
                    {
                        col.name: _mapped_value(row, col.name)
                        for col in model.__table__.columns
                        if col.name != "id"
                        and not col.foreign_keys
                        and not col.name.endswith(("_id", "_at"))
                        and (not col.name.endswith("_hash") or col.name == "config_hash")
                        and col.name
                        not in ("scope_id", "refresh_cycle_id", "acquisition_plan_id", "event_key")
                    }
                    for row in rows
                ],
                key=str,
            )
            financial[name] = _comparable_ceri(financial[name], ceri_context.cutoff_at)
        predictions = list(
            db.scalars(
                select(models.WinnerPredictionSnapshot).where(
                    models.WinnerPredictionSnapshot.run_id == run_id
                )
            )
        )
        assert len(predictions) == 10
        financial["winner"] = [
            {
                key: getattr(row, key)
                for key in (
                    "ticker",
                    "eligibility_status",
                    "exclusion_reason",
                    "prediction_as_of_date",
                    "planned_entry_session",
                    "entry_schedule_status",
                    "entry_data_status",
                    "feature_json",
                    "warning_flags_json",
                )
            }
            for row in sorted(predictions, key=lambda item: item.ticker)
        ]
        published = list(
            db.scalars(
                select(models.WinnerCohortGeneration).where(
                    models.WinnerCohortGeneration.status == "PUBLISHED"
                )
            )
        )
        assert published
        publication_jobs = list(
            db.scalars(
                select(models.BackgroundJob).where(
                    models.BackgroundJob.job_type == "WINNER_COHORT_REFRESH",
                    models.BackgroundJob.status == "COMPLETED",
                )
            )
        )
        assert publication_jobs
        for generation in published:
            from app.services.winner_probability.cohort_authority import (
                validate_completion,
                validate_generation,
            )
            from app.services.winner_probability.config import load_winner_probability_config

            config = winner_config or load_winner_probability_config()
            validate_generation(db, generation, config)
            validate_completion(db, generation, config)
            state = db.get(models.WinnerCohortRefreshState, generation.refresh_state_id)
            assert state.published_generation_id == generation.id
            job = next(
                job
                for job in publication_jobs
                if job.result_json.get("generation_id") == generation.id
            )
            assert job.result_json["publication_status"] == "PUBLISHED"
            owner = require_semantic_authority(job)
            retained = require_semantic_authority(generation)
            assert (retained.acquisition_plan_id, retained.scope_id, retained.refresh_cycle_id) == (
                owner.acquisition_plan_id,
                owner.scope_id,
                owner.refresh_cycle_id,
            )
            statistics = list(
                db.scalars(
                    select(models.WinnerCohortStatistic).where(
                        models.WinnerCohortStatistic.generation_id == generation.id
                    )
                )
            )
            assert statistics and generation.evidence_row_count > 0
            members = list(
                db.scalars(
                    select(models.WinnerEvidenceManifestMember).where(
                        models.WinnerEvidenceManifestMember.manifest_id.in_(
                            [row.evidence_manifest_id for row in statistics]
                        )
                    )
                )
            )
            assert members
            raw["publication_members"] = [
                {
                    col.name: _mapped_value(row, col.name)
                    for col in models.WinnerEvidenceManifestMember.__table__.columns
                }
                for row in members
            ]
        raw["published_generations"] = [
            {
                col.name: _mapped_value(row, col.name)
                for col in models.WinnerCohortGeneration.__table__.columns
            }
            for row in published
        ]
        financial["cohorts"] = [
            {
                key: getattr(row, key)
                for key in (
                    "status",
                    "evidence_row_count",
                    "planned_group_count",
                    "completed_group_count",
                )
            }
            for row in published
        ]
        statistics = list(
            db.scalars(
                select(models.WinnerCohortStatistic).where(
                    models.WinnerCohortStatistic.generation_id.in_([row.id for row in published])
                )
            )
        )
        metadata_fields = {
            "id",
            "created_at",
            "updated_at",
            "calculated_at",
            "generation_id",
            "evidence_manifest_id",
            "outcome_definition_id",
            "acquisition_plan_id",
            "scope_id",
            "refresh_cycle_id",
            "configuration_resolution_id",
        }
        financial["cohort_statistics"] = sorted(
            [
                {
                    col.name: _mapped_value(row, col.name)
                    for col in models.WinnerCohortStatistic.__table__.columns
                    if not col.foreign_keys
                    and col.name not in metadata_fields
                    and (not col.name.endswith("_hash") or col.name == "config_hash")
                }
                for row in statistics
            ],
            key=str,
        )
        # Native completion above recomputes every statistic from its exact
        # manifest/population and validates each proof. Distinct executions
        # retain distinct receipt/body digests, never identical proof objects.
        for statistic in financial["cohort_statistics"]:
            statistic["metadata_json"] = {
                key: value
                for key, value in (statistic.get("metadata_json") or {}).items()
                if key != "native_cohort_proof"
            }
        raw["cohort_statistics"] = [
            {
                col.name: _mapped_value(row, col.name)
                for col in models.WinnerCohortStatistic.__table__.columns
            }
            for row in statistics
        ]
        result = {
            "terminal": pipeline.status,
            "financial": financial,
            "frozen_shared": shared,
            "authority_relationships": sorted(links, key=str),
            "raw": raw,
        }
    destination.write_text(json.dumps(result, default=str, sort_keys=True, indent=2))
    return json.loads(
        json.dumps(
            {key: value for key, value in result.items() if key != "raw"},
            default=str,
            sort_keys=True,
        )
    )
