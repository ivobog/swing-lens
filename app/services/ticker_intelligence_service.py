"""Bounded read adapters for the workspace's four independent intelligence panels."""

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriEarningsActual,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    EstimateKind,
    EstimateSource,
    RankingResult,
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalChangeEvent,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services.ceri.evidence_eligibility import current_snapshot_authority_predicate
from app.services.ceri.feature_flags import ceri_flags
from app.services.ceri.pit_eligibility import source_record_historical_eligibility_predicate
from app.services.ceri.query_service import (
    PURGE_INVALIDATION_FLAG,
    CeriQueryService,
    _earnings_payload,
    _earnings_surprise_features,
    _event_payload,
    _guidance_summary,
    _score_snapshot_payload,
    _snapshot_freshness,
    pinned_revision_details,
)
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    get_certified_evidence_for_row,
    get_evidence_by_id,
)
from app.services.producer_readiness import ReadinessStatus, readiness_from_evidence
from app.services.setup_lifecycle.query_service import (
    _prime_alert_payload_context,
    alert_payload,
    episode_payload,
    lifecycle_event_payload,
    signal_change_payload,
    snapshot_payload,
)
from app.services.setup_lifecycle.repository import (
    current_canonical_snapshot_predicate,
    published_lifecycle_select,
)
from app.services.ticker_search_service import eligible_ticker_decisions, resolve_ticker_identity
from app.services.ticker_workspace_service import _source_matches
from app.services.winner_probability.api_service import (
    _estimate_payload,
    _outcome_definition_payload,
    _prediction_payload,
    _prediction_visible_clause,
    estimate_read_statement,
    estimate_selection_order,
)
from app.services.winner_probability.dtos import WinnerProbabilityApiQuery

SECTION_TITLES = {
    "ceri": "CERI Intelligence",
    "lifecycle": "Setup Lifecycle",
    "rankings": "Ranking Profiles",
    "winner": "Winner Evidence",
}
RECENT_LIMIT = 10
RANKING_PAGE_SIZE = 100


def ticker_intelligence(
    db, ticker, identity, run_id, evidence_id, section, *, detail, settings, offset=0
):
    resolved = resolve_ticker_identity(db, ticker, identity)
    if resolved["state"] != "resolved":
        return {"state": resolved["state"], "summary": "Verified ticker identity unavailable."}
    item = resolved["identity"]
    if (
        section in {"ceri", "lifecycle"}
        and resolve_ticker_identity(db, item["ticker"])["state"] != "resolved"
    ):
        return {
            "state": "identity_ambiguous",
            "summary": "Current symbol-only intelligence cannot verify this issuer identity.",
        }
    if section == "ceri" and not ceri_flags(settings).ui:
        return {"state": "disabled", "summary": "CERI UI is disabled."}
    selected = None
    if section in {"rankings", "winner"}:
        if run_id is None:
            return {"state": "unavailable", "summary": "No eligible decision run selected."}
        row = next(eligible_ticker_decisions(db, item, run_id=run_id), None)
        if row is None:
            return {
                "state": "unavailable",
                "summary": f"Run {run_id} is unavailable for this identity.",
            }
        if evidence_id is not None and evidence_id != row["id"]:
            return {
                "state": "changed",
                "summary": "Decision evidence changed. Refresh the workspace.",
            }
        selected = get_evidence_by_id(db, evidence_id=row["id"], kind=CoreEvidenceKind.COMBINED)
    reader = {"ceri": _ceri, "lifecycle": _lifecycle, "rankings": _rankings, "winner": _winner}[
        section
    ]
    if section == "rankings":
        return reader(db, item, selected, detail=detail, offset=offset)
    return reader(db, item, selected, detail=detail)


def _ceri(db, item, selected, *, detail):
    # Same eligible projection and cutoff ordering as the CERI ticker service.
    scope = [CeriScoreSnapshot.ticker == item["ticker"]]
    if item["identity"].startswith("ceri:"):
        scope.append(CeriScoreSnapshot.company_id == int(item["identity"].split(":")[1]))
    snapshot = db.scalar(
        select(CeriScoreSnapshot)
        .where(
            *scope,
            current_snapshot_authority_predicate(),
        )
        .order_by(
            CeriScoreSnapshot.cutoff_at.desc().nullslast(),
            CeriScoreSnapshot.as_of_session.desc().nullslast(),
            CeriScoreSnapshot.id.desc(),
        )
        .limit(1)
    )
    if snapshot is None:
        captured = db.scalar(select(CeriScoreSnapshot.id).where(*scope).limit(1))
        return {
            "state": "excluded" if captured else "empty",
            "summary": "Captured evidence is excluded, quarantined, invalidated or uncertified."
            if captured
            else "No eligible CERI evidence.",
        }
    row = _score_snapshot_payload(snapshot)
    age = max(0, (datetime.now(UTC) - snapshot.cutoff_at).days)
    result = {
        "state": "available",
        "summary": (
            f"Opportunity {_number(snapshot.opportunity_score)} · "
            f"Event risk {_number(snapshot.event_risk_score)} · "
            f"{snapshot.data_confidence or 'Confidence unavailable'}"
        ),
        "as_of": snapshot.cutoff_at,
        "age_days": age,
        "latest": row,
    }
    if not detail:
        return result
    company = snapshot.company_id
    service = CeriQueryService()
    result["changes"] = service.recent_changes(db, snapshot, limit=RECENT_LIMIT)
    result["alerts"] = service.recent_alerts(db, snapshot, limit=RECENT_LIMIT)
    result["freshness"] = _snapshot_freshness(db, snapshot, latest_checks_only=True)
    revision_details = pinned_revision_details(db, snapshot, limit=30)
    result["revisions"] = revision_details.pop("items")
    result["revision_provenance"] = revision_details
    earnings_features = {
        r["earnings_actual_id"]: r
        for r in _earnings_surprise_features(db, snapshot)
        if r.get("earnings_actual_id") is not None
    }
    earnings = db.scalars(
        select(CeriEarningsActual)
        .join(CeriSourceRecord, CeriSourceRecord.id == CeriEarningsActual.source_record_id)
        .where(
            CeriEarningsActual.company_id == company,
            CeriEarningsActual.report_at <= snapshot.cutoff_at,
            or_(
                CeriEarningsActual.report_session.is_(None),
                CeriEarningsActual.report_session <= snapshot.as_of_session,
            ),
            CeriSourceRecord.quarantine_reason.is_(None),
            CeriSourceRecord.export_policy != "purged",
            source_record_historical_eligibility_predicate(snapshot.cutoff_at),
        )
        .order_by(CeriEarningsActual.report_at.desc(), CeriEarningsActual.id.desc())
        .limit(RECENT_LIMIT)
    )
    result["earnings"] = [_earnings_payload(r, earnings_features.get(r.id)) for r in earnings]
    catalysts = db.execute(
        select(CeriCatalystEvent, CeriCatalystEventRevision)
        .join(
            CeriCatalystEventRevision,
            CeriCatalystEventRevision.catalyst_event_id == CeriCatalystEvent.id,
        )
        .join(CeriSourceRecord, CeriSourceRecord.id == CeriCatalystEventRevision.source_record_id)
        .where(
            CeriCatalystEvent.company_id == company,
            CeriCatalystEventRevision.is_current.is_(True),
            CeriCatalystEventRevision.issuer_relevance.is_(True),
            func.coalesce(CeriCatalystEventRevision.review_state, "") != "REJECTED",
            or_(
                CeriCatalystEventRevision.effective_session.is_(None),
                CeriCatalystEventRevision.effective_session <= snapshot.as_of_session,
            ),
            or_(
                CeriCatalystEventRevision.announced_at.is_(None),
                CeriCatalystEventRevision.announced_at <= snapshot.cutoff_at,
            ),
            CeriSourceRecord.quarantine_reason.is_(None),
            CeriSourceRecord.export_policy != "purged",
            source_record_historical_eligibility_predicate(snapshot.cutoff_at),
            ~func.coalesce(CeriCatalystEventRevision.conflict_flags_json, []).contains(
                [PURGE_INVALIDATION_FLAG]
            ),
        )
        .order_by(
            CeriCatalystEventRevision.expected_date.desc().nullslast(), CeriCatalystEvent.id.desc()
        )
        .limit(RECENT_LIMIT)
    )
    result["catalysts"] = [
        _event_payload(e, ticker=item["ticker"], current_revision=r) for e, r in catalysts
    ]
    result["guidance"] = _guidance_summary(db, company, snapshot.cutoff_at, include_rejected=False)
    return result


def _lifecycle(db, item, selected, *, detail):
    episodes = list(
        db.scalars(
            published_lifecycle_select(SetupLifecycleEpisode)
            .where(
                SetupLifecycleEpisode.ticker == item["ticker"],
                SetupLifecycleEpisode.timeframe == "1d",
                SetupLifecycleEpisode.status == "ACTIVE",
                SetupLifecycleEpisode.latest_evaluation_evidence_id.is_not(None),
            )
            .order_by(
                SetupLifecycleEpisode.is_primary.desc(),
                SetupLifecycleEpisode.primary_rank.asc().nullslast(),
                SetupLifecycleEpisode.current_as_of_date.desc(),
                SetupLifecycleEpisode.id,
            )
            .limit(20)
        )
    )
    result = {
        "state": "available" if episodes else "empty",
        "summary": "No eligible published active episodes.",
        "episodes": [episode_payload(e) for e in episodes],
        "timeframe": "1d",
    }
    for episode, row in zip(result["episodes"], episodes, strict=True):
        episode["age_calendar_days"] = (row.current_as_of_date - row.opened_on).days
    if episodes:
        primary = episodes[0]
        result.update(
            summary=(
                f"{primary.current_state} · {primary.setup_family} · "
                f"{primary.current_actionability}"
            ),
            as_of=primary.current_as_of_date,
        )
    if not detail:
        return result
    snapshots = (
        list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(
                    SetupSignalSnapshot.id.in_([e.current_snapshot_id for e in episodes]),
                    current_canonical_snapshot_predicate(),
                )
                .limit(20)
            )
        )
        if episodes
        else []
    )
    result["snapshots"] = {s.id: snapshot_payload(s, is_current_canonical=True) for s in snapshots}
    timeline = []
    for model, serializer, kind in (
        (SetupLifecycleEvent, lifecycle_event_payload, "lifecycle"),
        (SignalChangeEvent, signal_change_payload, "signal"),
        (SignalAlertEvent, None, "alert"),
    ):
        statement = published_lifecycle_select(model).where(
            model.ticker == item["ticker"], model.timeframe == "1d"
        )
        if model is SetupLifecycleEvent:
            statement = statement.where(
                model.is_current_version.is_(True), model.transition_evidence_id.is_not(None)
            )
        if model is SignalAlertEvent:
            statement = statement.where(
                model.status.not_in(("RETIRED", "SUPERSEDED", "INVALIDATED"))
            )
        rows = list(
            db.scalars(
                statement.order_by(model.effective_date.desc(), model.id.desc()).limit(RECENT_LIMIT)
            )
        )
        context = _prime_alert_payload_context(db, rows) if model is SignalAlertEvent else None
        for row in rows:
            payload = (
                alert_payload(row, db=db, context=context)
                if context is not None
                else serializer(row)
            )
            timeline.append({"kind": kind, **payload})
    result["timeline"] = sorted(
        timeline, key=lambda r: (r.get("effective_date") or "", r["id"]), reverse=True
    )[:20]
    signal_snapshot_ids = {
        row["current_snapshot_id"]
        for row in result["timeline"]
        if row["kind"] == "signal" and row.get("current_snapshot_id")
    }
    source_evidence = {row.id: row.evidence_id for row in snapshots}
    missing_sources = signal_snapshot_ids - source_evidence.keys()
    if missing_sources:
        source_evidence.update(
            dict(
                db.execute(
                    select(SetupSignalSnapshot.id, SetupSignalSnapshot.evidence_id).where(
                        SetupSignalSnapshot.id.in_(missing_sources)
                    )
                ).all()
            )
        )
    for row in result["timeline"]:
        if row["kind"] == "signal":
            row["setup_evidence_id"] = source_evidence.get(row.get("current_snapshot_id"))
    result["active_alerts"] = sum(
        row["kind"] == "alert"
        and row.get("status") in {"UNREAD", "ACKNOWLEDGED"}
        and row.get("record_status") in {"CURRENT", "CURRENT_CANONICAL"}
        for row in result["timeline"]
    )
    result["latest_change_date"] = max(
        (
            row.get("effective_date")
            for row in result["timeline"]
            if row["kind"] in {"lifecycle", "signal"} and row.get("effective_date")
        ),
        default=None,
    )
    return result


def _rankings(db, item, selected, *, detail, offset=0):
    # Read only this ticker's persisted profiles; never invoke rank_profile/config loaders.
    scope = (RankingResult.run_id == selected.run_id, RankingResult.ticker == item["ticker"])
    total = db.scalar(select(func.count()).select_from(RankingResult).where(*scope))
    rows = db.execute(
        select(RankingResult, CoreCalculationEvidence)
        .outerjoin(
            CoreCalculationCurrentProjection,
            and_(
                CoreCalculationCurrentProjection.artifact_kind == "RANKING",
                CoreCalculationCurrentProjection.run_id == RankingResult.run_id,
                CoreCalculationCurrentProjection.ticker == RankingResult.ticker,
                CoreCalculationCurrentProjection.ranking_profile_key
                == RankingResult.ranking_profile,
                CoreCalculationCurrentProjection.evidence_id == RankingResult.evidence_id,
            ),
        )
        .outerjoin(
            CoreCalculationEvidence,
            CoreCalculationEvidence.id == CoreCalculationCurrentProjection.evidence_id,
        )
        .where(*scope)
        .order_by(RankingResult.ranking_profile)
        .offset(offset)
        .limit(RANKING_PAGE_SIZE)
    ).all()
    profiles = []
    for row, evidence in rows:
        valid = _source_matches(selected, evidence, "RANKING")
        if valid:
            try:
                get_certified_evidence_for_row(db, kind=CoreEvidenceKind.RANKING, current_row=row)
                readiness = readiness_from_evidence(evidence)
                valid = readiness.status is ReadinessStatus.READY and not readiness.blocking_reasons
            except (EvidenceUnavailableError, ValueError, TypeError, KeyError):
                valid = False
        payload = dict(evidence.payload_json) if valid else {}
        if valid:
            configuration = payload.get("effective_configuration_at_creation") or {}
            payload["effective_configuration_at_creation"] = {
                key: configuration[key]
                for key in (
                    "identity",
                    "semantic_hash",
                    "resolution_hash",
                    "producer_version",
                    "compatibility_policy",
                )
                if key in configuration
            }
        profiles.append(
            {
                "key": row.ranking_profile,
                "label": payload.get("ranking_label") or row.ranking_label,
                "state": "available" if valid else "Not calculated or published for this run",
                "payload": payload,
                "evidence_id": evidence.id if valid else None,
                "as_of": evidence.calculated_at if valid else None,
            }
        )
    valid = [p for p in profiles if p["state"] == "available"]
    best = min(
        (
            p["payload"].get("profile_rank")
            for p in valid
            if p["payload"].get("profile_rank") is not None
        ),
        default=None,
    )
    summary = (
        f"{len(valid)} profiles available · Best profile rank #{best}"
        if valid
        else "Not calculated or published for this run."
    )
    if total > RANKING_PAGE_SIZE or offset:
        summary = (
            f"{total} persisted profiles · {len(valid)} available on this page"
            + (f" · Best profile rank on this page #{best}" if valid else "")
        )
    return {
        "state": "available" if valid else "empty",
        "summary": summary,
        "profiles": profiles,
        "run_id": selected.run_id,
        "as_of": selected.calculated_at,
        "identity": item["identity"],
        "decision_evidence_id": selected.id,
        "pagination": {
            "total": total,
            "offset": offset,
            "limit": RANKING_PAGE_SIZE,
            "returned": len(profiles),
            "first": offset + 1 if profiles else 0,
            "last": offset + len(profiles) if profiles else 0,
            "next_offset": offset + RANKING_PAGE_SIZE
            if offset + RANKING_PAGE_SIZE < total else None,
            "previous_offset": max(0, offset - RANKING_PAGE_SIZE) if offset else None,
        },
    }


def _winner(db, item, selected, *, detail):
    query = WinnerProbabilityApiQuery()
    predictions = list(
        db.scalars(
            select(WinnerPredictionSnapshot)
            .where(
                WinnerPredictionSnapshot.run_id == selected.run_id,
                WinnerPredictionSnapshot.ticker == item["ticker"],
                _prediction_visible_clause(query),
                WinnerPredictionSnapshot.source_ids_json["combined_evidence_id"].as_integer()
                == selected.id,
            )
            .order_by(
                WinnerPredictionSnapshot.prediction_as_of_date.desc(),
                WinnerPredictionSnapshot.id.desc(),
            )
            .limit(10)
        )
    )
    if not predictions:
        return {
            "state": "empty",
            "summary": "No matching visible Winner prediction for this decision run.",
            "run_id": selected.run_id,
        }
    # Decision-time only: a current rescore from another context is never substituted.
    candidates = (
        estimate_read_statement(query, estimate_kind=EstimateKind.DECISION_TIME)
        .where(WinnerProbabilityEstimate.prediction_id.in_([p.id for p in predictions]))
        .with_only_columns(
            WinnerProbabilityEstimate.id,
            func.row_number()
            .over(
                partition_by=(
                    WinnerProbabilityEstimate.prediction_id,
                    WinnerProbabilityEstimate.outcome_definition_id,
                ),
                order_by=estimate_selection_order(),
            )
            .label("selection_rank"),
        )
        .subquery("selected_decision_time_estimates")
    )
    estimates = list(
        db.execute(
            select(WinnerProbabilityEstimate, WinnerOutcomeDefinition)
            .join(
                candidates,
                and_(
                    candidates.c.id == WinnerProbabilityEstimate.id,
                    candidates.c.selection_rank == 1,
                ),
            )
            .join(
                WinnerOutcomeDefinition,
                WinnerOutcomeDefinition.id == WinnerProbabilityEstimate.outcome_definition_id,
            )
            .options(selectinload(WinnerProbabilityEstimate.model_version))
            .order_by(
                WinnerOutcomeDefinition.is_primary.desc(),
                WinnerOutcomeDefinition.horizon_sessions,
                WinnerProbabilityEstimate.id,
            )
            .limit(100)
        )
    )
    # Prime the exact frozen upstream rows in one query for prediction metadata.
    # Retain strong references so get_evidence_by_id uses the request identity map.
    evidence_ids = {
        value
        for prediction in predictions
        for key, value in (prediction.source_ids_json or {}).items()
        if key in {"combined_evidence_id", "ranking_evidence_id"} and type(value) is int
    }
    upstream = (
        list(
            db.scalars(
                select(CoreCalculationEvidence).where(CoreCalculationEvidence.id.in_(evidence_ids))
            )
        )
        if detail
        else []
    )
    items = []
    estimate_payloads = {}
    for prediction in predictions:
        eligible = prediction.eligibility_status == "ELIGIBLE"
        matching = [
            (e, d)
            for e, d in estimates
            if e.prediction_id == prediction.id
            and (
                eligible
                or (prediction.eligibility_status == "INSUFFICIENT" and e.point_probability is None)
            )
        ]
        for estimate, _ in matching:
            # Preserve useful sample/cohort reasons without sending a second copy
            # of the entire frozen execution configuration into the detail markup.
            metadata = estimate.metadata_json or {}
            estimate_payload = _estimate_payload(None, estimate)
            estimate_payload["metadata"] = {
                key: metadata[key]
                for key in (
                    "wins",
                    "raw_rate",
                    "cohort_key",
                    "cohort_level",
                    "attempted_cohort_key",
                    "attempted_cohort_level",
                    "first_zero_stage",
                    "oldest_evidence_date",
                    "newest_evidence_date",
                    "calculation_version",
                )
                if key in metadata
            }
            estimate_payload["evidence_composition"] = {
                key: metadata[key]
                for key in (
                    "native_1_1_n",
                    "pre11_compatible_n",
                    "reconstructed_label_n",
                    "oldest_evidence_date",
                    "newest_evidence_date",
                )
                if key in metadata
            }
            if estimate.model_version is None and estimate.source != EstimateSource.COHORT:
                # Canonical fallback source labels are not persisted model registry facts.
                estimate_payload["model_key"] = None
                estimate_payload["model_version_label"] = None
                estimate_payload["model_status"] = None
            # Payloads are detached dictionaries; immutable database values are untouched.
            estimate_payload["lifecycle_status"] = estimate.lifecycle_status
            estimate_payload["published_at"] = estimate.published_at
            estimate_payloads[estimate.id] = estimate_payload
        items.append(
            {
                "prediction": _prediction_payload(prediction, db=db)
                if detail
                else {
                    "id": prediction.id,
                    "eligibility_status": prediction.eligibility_status,
                    "source_data_cutoff_at": prediction.source_data_cutoff_at,
                },
                "estimates": [
                    {
                        "estimate": estimate_payloads[e.id],
                        "outcome": _outcome_definition_payload(d),
                        "lifecycle_status": e.lifecycle_status,
                        "published_at": e.published_at,
                    }
                    for e, d in matching
                ],
            }
        )
    first = next((e["estimate"] for i in items for e in i["estimates"]), None)
    del upstream
    supported = first is not None and first["point_probability"] is not None
    return {
        "state": "available" if supported else "insufficient",
        "summary": (
            f"Probability {first['point_probability']:.0%} · "
            f"Evidence grade {first['evidence_grade']}"
        )
        if supported
        else "Insufficient evidence — no supported published decision-time probability.",
        "items": items,
        "run_id": selected.run_id,
        "as_of": predictions[0].source_data_cutoff_at,
    }


def _number(value):
    return "Unavailable" if value is None else f"{float(value):.2f}"
