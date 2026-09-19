"""Native supporting operations retain source rows without decision identity."""

from types import SimpleNamespace

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriManualReview,
)
from app.routers import ceri_routes
from app.services.ceri.manual_review_service import CeriManualReviewService
from app.services.ceri.sec.identity_repair import resolve_and_persist_sec_identity

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_source_override_missing_changed_and_superseded_authority_rolls_back(
    contextual_engine, monkeypatch
):
    monkeypatch.setattr(ceri_routes, "_require_local_admin", lambda *_: None)
    with Session(contextual_engine) as db:
        company = CeriCompany(ticker="T14DSUPPORT")
        db.add(company)
        db.flush()
        target = CeriCatalystEvent(company_id=company.id, category="TEST", subject_key="source")
        db.add(target)
        db.flush()
        original = CeriCatalystEventRevision(
            catalyst_event_id=target.id,
            revision_number=1,
            is_current=True,
            status="ANNOUNCED",
            direction="POSITIVE",
            materiality=1.0,
        )
        db.add(original)
        db.commit()
        target_id, revision_id = target.id, original.id
        service = CeriManualReviewService()
        arguments = dict(
            current_revision=original,
            new_values={"materiality": 2.0},
            reviewer="native-human",
            reason="Explicit source correction",
        )
        for changed, code in (
            ({"reviewer": " "}, "REVIEWER_AND_REASON_REQUIRED"),
            ({"reason": " "}, "REVIEWER_AND_REASON_REQUIRED"),
            ({"new_values": {}}, "UNSUPPORTED_SOURCE_FIELDS"),
            ({"new_values": {"opportunity_score": 99}}, "UNSUPPORTED_SOURCE_FIELDS"),
        ):
            with pytest.raises(ValueError, match=code):
                service.create_catalyst_override(db, **{**arguments, **changed})
            assert db.get(CeriCatalystEventRevision, revision_id).is_current
            assert db.scalar(select(func.count()).select_from(CeriManualReview)) == 0
        original.materiality = 999
        with pytest.raises(ValueError, match="MUTATION_SOURCE_RECORD_ARGUMENT_MISMATCH"):
            service.create_catalyst_override(db, **arguments)
        assert db.get(CeriCatalystEventRevision, revision_id).materiality == 1.0
        _, appended = service.create_catalyst_override(db, **arguments)
        db.commit()
        appended_id = appended.id
        with pytest.raises(ValueError, match="CURRENT_REVISION_REQUIRED"):
            service.create_catalyst_override(db, **arguments)
        retained = db.get(CeriCatalystEventRevision, revision_id)
        assert retained.materiality == 1.0 and not retained.is_current
        assert db.get(CeriCatalystEventRevision, appended_id).materiality == 2.0
        assert db.scalar(select(func.count()).select_from(CeriManualReview)) == 1
        first = ceri_routes.review_ceri_event(target_id, object(), db, None)
        assert ceri_routes.review_ceri_event(target_id, object(), db, None)["id"] == first["id"]
        with pytest.raises(HTTPException) as error:
            ceri_routes.review_ceri_event(
                target_id, object(), db, {"new_value": {"review_state": "REJECTED"}}
            )
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            ceri_routes.review_ceri_event(987654321, object(), db, None)
        assert error.value.status_code == 404


def test_sec_exact_identity_branches_do_not_guess_or_overwrite_native_identifier(
    contextual_engine,
):
    with Session(contextual_engine) as db:
        db.add(CeriCompany(ticker="T14DIDENT", exchange="US"))
        db.commit()
        for observations, expected in (((), "UNRESOLVED"), (("123", "456"), "AMBIGUOUS")):
            result = resolve_and_persist_sec_identity(
                db,
                provider=SimpleNamespace(
                    resolve_cik_candidates=lambda _, values=observations: values
                ),
                ticker="T14DIDENT",
            )
            db.commit()
            assert result.status == expected
            assert (
                db.scalar(select(CeriCompany).where(CeriCompany.ticker == "T14DIDENT")).cik is None
            )
        exact = SimpleNamespace(resolve_cik_candidates=lambda _: ("123",))
        result = resolve_and_persist_sec_identity(db, provider=exact, ticker=" t14dident ")
        db.commit()
        assert result.status == "RESOLVED" and result.cik == "0000000123"
        result = resolve_and_persist_sec_identity(
            db,
            provider=SimpleNamespace(resolve_cik_candidates=lambda _: ("456",)),
            ticker="T14DIDENT",
        )
        db.commit()
        assert result.status == "ALREADY_RESOLVED" and result.cik == "0000000123"
        result = resolve_and_persist_sec_identity(db, provider=exact, ticker="T14DNEWIDENT")
        db.commit()
        assert result.status == "RESOLVED"
        assert (
            db.scalar(select(CeriCompany).where(CeriCompany.ticker == "T14DNEWIDENT")).cik
            == "0000000123"
        )


def test_change_rebuild_delivers_its_explicit_cutoff_to_native_normalized_writer(
    contextual_engine, monkeypatch
):
    from datetime import UTC, datetime
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import _seed_ceri_manual_evidence

    from app.models.ceri_tables import CeriChangeEvent
    from app.services.ceri.change_rebuild_service import (
        CeriChangeRebuildRequest,
        CeriChangeRebuildService,
    )
    from app.services.market_clock_service import MarketClockService

    with Session(contextual_engine) as db:
        cutoff = MarketClockService().cutoff_for(
            datetime.now(UTC), reason="T14D_NATIVE_CHANGE_REBUILD"
        )
        _seed_ceri_manual_evidence(db, as_of_session=cutoff.latest_completed_session)
        db.commit()
        cutoff = MarketClockService().cutoff_for(
            datetime.now(UTC), reason="T14D_NATIVE_CHANGE_REBUILD_DELIVERY"
        )
        request = CeriChangeRebuildRequest(
            ticker="ALFA",
            as_of_session=cutoff.latest_completed_session,
            cutoff_at=cutoff.cutoff_at,
        )
        result = CeriChangeRebuildService().rebuild(db, request)
        assert result.failed == 0, result.as_dict()
        db.commit()
        sources = list(db.scalars(select(CeriChangeEvent)))
        normalized = [
            row
            for row in sources
            if (row.delta_json or {}).get("native_change_proof", {}).get("source_kind")
            == "NORMALIZED_EVENT_DERIVATION"
        ]
        assert normalized
        for row in normalized:
            assert row.delta_json["native_change_proof"]["operation_time"]["cutoff_at"]
        repeated = CeriChangeRebuildService().rebuild(db, request)
        assert repeated.failed == 0 and repeated.changes == 0, repeated.as_dict()
        db.commit()
        assert db.scalar(select(func.count()).select_from(CeriChangeEvent)) == len(sources)


@pytest.mark.parametrize("population", [1, 50])
def test_normalized_change_rebuild_authority_queries_are_bounded(
    contextual_engine, population, record_property
):
    import inspect
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import event

    from app.models.ceri_tables import CeriProcessingRun
    from app.services.ceri.change_rebuild_service import (
        CeriChangeRebuildRequest,
        CeriChangeRebuildService,
    )
    from app.services.ceri.enums import CeriDataset
    from app.services.ceri.normalization_service import CeriNormalizationService
    from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
    from app.services.ceri.provider_registry import CeriProviderRegistry
    from app.services.ceri.providers.manual_provider import ManualCeriProvider
    from app.services.market_clock_service import MarketClockService

    with Session(contextual_engine) as db:
        identity = resolve_and_persist_sec_identity(
            db,
            provider=SimpleNamespace(resolve_cik_candidates=lambda _: ("987654",)),
            ticker="BOUNDED",
        )
        assert identity.status == "RESOLVED"
        db.commit()
        cutoff = MarketClockService().cutoff_for(
            datetime.now(UTC), reason="T14D_CHANGE_QUERY_FIXTURE"
        )
        published = (cutoff.latest_completed_session - timedelta(days=3)).isoformat() + "T19:00:00Z"
        records = [
            {
                "provider_record_id": f"bounded-guidance-{i}",
                "ticker": "BOUNDED",
                "action": "RAISED" if i % 2 == 0 else "LOWERED",
                "metric": "REVENUE",
                "period_type": "CURRENT_FISCAL_YEAR",
                "point": str(100 + i),
                "currency": "USD",
                "confidence": "high",
                "announced_at": published,
                "published_at": published,
            }
            for i in range(population)
        ]
        provider = ManualCeriProvider({CeriDataset.GUIDANCE: records})
        result = CeriIngestionService(
            registry=CeriProviderRegistry(providers={"manual": provider})
        ).ingest(
            db,
            CeriIngestionRequest(
                provider="manual",
                dataset=CeriDataset.GUIDANCE,
                ticker="BOUNDED",
                request_key=f"t14d-change-bounded-{population}",
            ),
        )
        assert result.failed == 0, result.as_dict()
        db.commit()
        processing = CeriProcessingRun(
            job_type="CERI_NORMALIZE",
            status="RUNNING",
            deterministic_request_key=f"t14d-change-normalize-{population}",
        )
        db.add(processing)
        db.flush()
        normalized = CeriNormalizationService().normalize(
            db, processing_run=processing, ingestion_run_id=result.ingestion_run_id
        )
        assert normalized.failed == 0
        db.commit()
        cutoff = MarketClockService().cutoff_for(
            datetime.now(UTC), reason="T14D_CHANGE_QUERY_OPERATION"
        )
        statements = []

        def record(_conn, _cursor, sql, _params, _context, _many):
            if sql.lstrip().upper().startswith("SELECT") and (
                any(frame.function == "_normalized_source" for frame in inspect.stack())
                or (
                    "FOR UPDATE" in sql.upper()
                    and any(
                        name in sql
                        for name in (
                            "ceri_guidance_events",
                            "ceri_catalyst_events",
                            "ceri_catalyst_event_revisions",
                            "ceri_source_records",
                        )
                    )
                )
            ):
                statements.append(sql)

        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            rebuilt = CeriChangeRebuildService().rebuild(
                db,
                CeriChangeRebuildRequest(
                    as_of_session=cutoff.latest_completed_session, cutoff_at=cutoff.cutoff_at
                ),
            )
            assert rebuilt.failed == 0, rebuilt.as_dict()
            assert rebuilt.changes == population, rebuilt.as_dict()
            authority_selects = len(statements)
            record_property("population", population)
            record_property("authority_selects", authority_selects)
            assert authority_selects <= 12, {
                "population": population,
                "authority_selects": authority_selects,
            }
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)
        if population == 1:
            from decimal import Decimal

            from app.models.ceri_tables import CeriChangeEvent, CeriGuidanceEvent

            db.commit()
            count_before = db.scalar(select(func.count()).select_from(CeriChangeEvent))
            guidance = db.scalar(select(CeriGuidanceEvent))
            original_point = guidance.point_value
            guidance.point_value = Decimal("999999")
            rejected = CeriChangeRebuildService().rebuild(
                db,
                CeriChangeRebuildRequest(
                    as_of_session=cutoff.latest_completed_session, cutoff_at=cutoff.cutoff_at
                ),
            )
            assert rejected.failed == 1, rejected.as_dict()
            assert any(
                code in str(rejected.errors)
                for code in ("NORMALIZED_SOURCE_BODY_MISMATCH", "MUTATION_SOURCE_BUNDLE_CHANGED")
            )
            db.rollback()
            assert db.get(CeriGuidanceEvent, guidance.id).point_value == original_point
            assert db.scalar(select(func.count()).select_from(CeriChangeEvent)) == count_before
