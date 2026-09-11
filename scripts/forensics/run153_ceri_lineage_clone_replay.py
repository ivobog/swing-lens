from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database_safety import assert_disposable_database
from app.models.ceri_tables import (
    CeriCompany,
    CeriDerivedFeature,
    CeriFeatureBuildState,
    CeriPriceResponseFeature,
    CeriRevisionFeature,
)
from app.models.tables import MarketCalculationContext
from app.services.ceri.artifact_lineage import CeriArtifactOwnership
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.market_calculation_context_service import cutoff_from_row
from app.settings import get_settings

ARTIFACT_MODELS = (
    CeriRevisionFeature,
    CeriDerivedFeature,
    CeriPriceResponseFeature,
    CeriFeatureBuildState,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--ticker", default="DRS")
    args = parser.parse_args()
    database_url = make_url(get_settings().database_url).set(database=args.database_name)
    assert_disposable_database(database_url.render_as_string(hide_password=False))
    engine = create_engine(database_url, pool_pre_ping=True)
    report: dict[str, object] = {"ticker": args.ticker.upper()}
    try:
        with Session(engine, expire_on_commit=False) as db:
            context_row = db.scalars(
                select(MarketCalculationContext).order_by(MarketCalculationContext.id.desc())
            ).first()
            company = db.scalar(
                select(CeriCompany).where(CeriCompany.ticker == args.ticker.upper())
            )
            if context_row is None or company is None:
                raise RuntimeError("clone lacks the required context or CERI company")
            market_cutoff = cutoff_from_row(context_row)
            request = CeriFeatureRebuildRequest(
                company_ids=(company.id,),
                as_of_session=market_cutoff.latest_completed_session,
                cutoff_at=market_cutoff.cutoff_at,
                calculation_context_id=context_row.id,
                calendar_version=market_cutoff.calendar_version,
                ownership_mode=CeriArtifactOwnership.PIPELINE.value,
            )
            first = CeriFeatureRebuildService().rebuild(db, request)
            db.commit()
            report["context_id"] = context_row.id
            report["first_result"] = asdict(first)
            report["first_artifacts"] = _artifact_state(db, context_row.id, company.id)

        with Session(engine, expire_on_commit=False) as db:
            second = CeriFeatureRebuildService().rebuild(db, request)
            db.commit()
            report["second_result"] = asdict(second)
            report["second_artifacts"] = _artifact_state(db, context_row.id, company.id)
            report["retry_preserved_identity"] = (
                report["first_artifacts"] == report["second_artifacts"]
            )
            report["pipeline_owned_null_context_rows"] = {
                model.__tablename__: len(
                    list(
                        db.scalars(
                            select(model).where(
                                model.ownership_mode == CeriArtifactOwnership.PIPELINE.value,
                                model.calculation_context_id.is_(None),
                            )
                        )
                    )
                )
                for model in ARTIFACT_MODELS
            }
            report["null_pipeline_insert_rejected"] = _invalid_insert_rejected(
                db, company.id, "PIPELINE"
            )
            report["unknown_ownership_insert_rejected"] = _invalid_insert_rejected(
                db, company.id, "UNKNOWN"
            )
            inspector = inspect(db.connection())
            report["constraint_tables"] = {
                model.__tablename__: sorted(
                    item["name"]
                    for item in inspector.get_check_constraints(model.__tablename__)
                    if item["name"]
                    and (
                        item["name"].endswith("pipeline_context")
                        or item["name"].endswith("ownership_mode")
                    )
                )
                for model in ARTIFACT_MODELS
            }
            db.rollback()
    finally:
        engine.dispose()
    report["certified"] = bool(
        report.get("retry_preserved_identity")
        and report.get("null_pipeline_insert_rejected")
        and report.get("unknown_ownership_insert_rejected")
        and not any(report.get("pipeline_owned_null_context_rows", {}).values())
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["certified"] else 1


def _artifact_state(db: Session, context_id: int, company_id: int) -> dict[str, object]:
    return {
        model.__tablename__: [
            {
                "id": row.id,
                "context_id": row.calculation_context_id,
                "ownership_mode": row.ownership_mode,
                "cutoff_at": row.calculation_cutoff_at,
                "calendar_version": row.calendar_version,
            }
            for row in db.scalars(
                select(model)
                .where(
                    model.company_id == company_id,
                    model.calculation_context_id == context_id,
                )
                .order_by(model.id)
            )
        ]
        for model in ARTIFACT_MODELS
    }


def _invalid_insert_rejected(db: Session, company_id: int, ownership_mode: str) -> bool:
    try:
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO ceri_price_response_features "
                    "(company_id,ticker,event_type,evidence_hash,event_key,config_version,"
                    "config_hash,calculation_version,ownership_mode) VALUES "
                    "(:company_id,'DRS','NONE','hash',:event_key,'v','h','c',:ownership_mode)"
                ),
                {
                    "company_id": company_id,
                    "event_key": f"lineage-rejection-{ownership_mode.lower()}",
                    "ownership_mode": ownership_mode,
                },
            )
            db.flush()
    except IntegrityError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
