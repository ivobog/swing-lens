"""Actual-side fresh-session projections for the seven A05B domain contracts.

Only this module imports models. Expected truth is loaded from sealed literals;
no production calculator, selector or result DTO authors an expected value.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ceri_tables as c
from app.models import tables as m
from single_run_certification.golden_completion import (
    COMPLETION,
    KEYS,
    compare_vector,
    references_match,
    relationship_inventory,
    relationship_key,
)
from single_run_certification.golden_oracle import OracleMismatch, artifact, require_complete


def _project(row, fields, aliases=None):
    aliases = aliases or {}
    return {k: aliases[k] if k in aliases else getattr(row, k) for k in fields}


def _eligibility(values):
    return {
        role: {
            "included": item["included"],
            "status": item["decision"]["status"],
            "reasons": item["decision"]["reasons"],
            **({"requirement": item["requirement"]} if "requirement" in item else {}),
        }
        for role, item in values.items()
    }


def assert_completed_oracle(engine, env, run_id, pipeline_id):
    expected = json.loads((env.artifact_dir / "expected-manifest.json").read_text())
    require_complete(expected)
    domains = expected["completion"]["domains"]
    worksheet = json.loads((COMPLETION / "core-derivations.json").read_text())
    profiles = artifact("configuration.json")["ranking_profiles.yaml"]["profiles"]
    actual, relationships, errors = {}, [], []
    details = {}
    declared_relationships = relationship_inventory(expected)

    def relationship(name, condition, **evidence):
        relationships.append({"name": name, "passed": bool(condition), **evidence})

    # These reads happen after the native pipeline has committed, in a new session.
    with Session(engine) as db:
        pipeline = db.get(m.PipelineRun, pipeline_id)
        context_id = pipeline.market_calculation_context.id
        rows = {}
        for section, model in [
            ("technical", m.TechnicalScore),
            ("combined", m.CombinedResult),
            ("ranking", m.RankingResult),
            ("market_regime", m.MarketRegimeSnapshot),
            ("sector_parent", m.SectorRotationSnapshot),
            ("fundamental", m.FundamentalScore),
            ("raw", m.RawCompanyRow),
        ]:
            rows[section] = list(db.scalars(select(model).where(model.run_id == run_id)))
        by_ticker = {
            section: {r.ticker: r for r in values}
            for section, values in rows.items()
            if section not in ("market_regime", "sector_parent", "ranking")
        }

        def evidence(
            row, kind, *, sources=None, context=context_id, cutoff="20:30", pipeline_dimension=None
        ):
            retained = db.get(m.CoreCalculationEvidence, row.evidence_id)
            section = {
                "TECHNICAL": "technical",
                "COMBINED": "combined",
                "RANKING": "ranking",
                "REGIME": "market_regime",
                "SECTOR": "sector",
                "CERI": "captures",
                "SETUP": "setup",
            }[kind]
            member = (
                domains[section][0]
                if kind == "SECTOR"
                else {
                    key: getattr(row, "ranking_profile" if key == "profile" else key)
                    for key in KEYS[section]
                }
            )
            key = relationship_key(section, member)
            identity = retained.calculation_identity_json
            ownership = identity["ownership"]
            temporal = identity["temporal"]
            if getattr(row, "ticker", None) is not None:
                relationship(
                    "immutable_subject:" + kind + ":" + key,
                    identity["subject"]["ticker"].get("value") == row.ticker,
                )
            configuration = retained.payload_json["effective_configuration_at_creation"]
            digest = hashlib.sha256(
                json.dumps(
                    configuration["semantic"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            relationship(
                "immutable_configuration:" + kind + ":" + key,
                configuration["identity"]
                == identity["configuration"]["effective_configuration"]["value"]
                and digest == configuration["semantic_hash"]
                and digest == configuration["identity"]["fingerprint"]["digest"],
            )
            relationship(
                "immutable_scope:" + kind + ":" + key,
                retained.artifact_kind == kind
                and retained.run_id == run_id
                and retained.ticker == getattr(row, "ticker", None)
                and ownership["run_id"].get("value") == run_id
                and ownership["pipeline_id"]
                == (pipeline_dimension or {"state": "KNOWN", "value": pipeline_id})
                and temporal["as_of_session"].get("value") == "2026-10-26"
                and datetime.fromisoformat(temporal["calculation_cutoff"]["value"])
                == datetime.fromisoformat("2026-10-26T" + cutoff + ":00+00:00")
                and identity["calculation_context"]["market_calculation_context_id"].get("value")
                == context,
            )
            if sources is not None:
                found = {
                    r.source_role: r.source_evidence_id
                    for r in db.scalars(
                        select(m.CoreCalculationEvidenceSource).where(
                            m.CoreCalculationEvidenceSource.evidence_id == retained.id
                        )
                    )
                }
                relationship(
                    "immutable_sources:" + kind + ":" + key,
                    found == sources and retained.source_evidence_ids_json == sources,
                )
            details.setdefault("immutable_evidence", []).append(
                dict(
                    kind=kind,
                    semantic_member=key,
                    ticker=retained.ticker,
                    ownership=ownership,
                    temporal=temporal,
                    calculation_context=identity["calculation_context"],
                    expected_context_id=context,
                    configuration=identity["configuration"],
                    effective_configuration=configuration,
                    sources=retained.source_evidence_ids_json,
                    expected_sources=sources,
                )
            )

        def core():
            actual["technical"] = [
                _project(
                    r,
                    domains["technical"][0],
                    {
                        "base_dual_score": r.v4_debug_json["debug"]["base_dual_score"],
                        "warning_flags_json": r.warning_flags_json or [],
                    },
                )
                for r in rows["technical"]
            ]
            aliases = dict(
                trend="trend_quality",
                momentum="momentum_quality",
                setup="setup_quality",
                leadership="leadership_score",
                risk_control="risk_control",
                market="market_regime",
                execution="execution_quality",
            )
            actual["technical_contributions"] = [
                dict(
                    ticker=r.ticker,
                    **{k: r.v4_debug_json["debug"]["v4_components"][v] for k, v in aliases.items()},
                )
                for r in rows["technical"]
            ]
            actual["combined"] = [
                _project(
                    r,
                    domains["combined"][0],
                    {
                        "warning_flags": r.warning_flags_json or [],
                    },
                )
                for r in rows["combined"]
            ]
            actual["ranking"] = [
                _project(
                    r,
                    domains["ranking"][0],
                    dict(
                        profile=r.ranking_profile,
                        gates=r.gates_json,
                        penalties=r.penalties_json or {},
                        warning_flags=r.warning_flags_json or [],
                    ),
                )
                for r in rows["ranking"]
            ]
            for r in rows["technical"]:
                try:
                    compare_vector(
                        [
                            {
                                "ticker": r.ticker,
                                "weights": worksheet["technical"][r.ticker]["regime_weights"],
                            }
                        ],
                        [
                            {
                                "ticker": r.ticker,
                                "weights": r.v4_debug_json["debug"]["regime_weights"],
                            }
                        ],
                        keys=("ticker",),
                    )
                except OracleMismatch as exc:
                    errors.append("technical_weights:" + str(exc))
            for r in rows["ranking"]:
                try:
                    compare_vector(
                        [
                            {
                                "ticker": r.ticker,
                                "components": worksheet["ranking_components"][r.ticker],
                            }
                        ],
                        [{"ticker": r.ticker, "components": r.component_scores_json}],
                        keys=("ticker",),
                    )
                except OracleMismatch as exc:
                    errors.append("ranking_components:" + str(exc))
                policy = profiles[r.ranking_profile]
                try:
                    compare_vector(
                        [
                            {
                                "profile": r.ranking_profile,
                                **{k: policy[k] for k in ("weights", "technical_components")},
                            }
                        ],
                        [r.debug_json["profile"] | {"profile": r.ranking_profile}],
                        keys=("profile",),
                    )
                except OracleMismatch as exc:
                    errors.append("ranking_weights:" + str(exc))
            details["ranking_components"] = [
                dict(
                    ticker=r.ticker,
                    profile=r.ranking_profile,
                    components=r.component_scores_json,
                    policy=r.debug_json["profile"],
                )
                for r in rows["ranking"]
            ]
            details["technical_weights"] = [
                dict(ticker=r.ticker, weights=r.v4_debug_json["debug"]["regime_weights"])
                for r in rows["technical"]
            ]
            for r in rows["combined"]:
                source = r.debug_json["source_ids"]
                relationship(
                    "combined_sources:combined:"
                    + relationship_key("combined", {"ticker": r.ticker}),
                    references_match(
                        {
                            "raw_row_id": by_ticker["raw"][r.ticker].id,
                            "fundamental_score_id": by_ticker["fundamental"][r.ticker].id,
                            "technical_score_id": by_ticker["technical"][r.ticker].id,
                        },
                        source,
                    ),
                )
            for r in rows["technical"]:
                relationship(
                    "technical_context:technical:"
                    + relationship_key("technical", {"ticker": r.ticker}),
                    r.calculation_context_id == context_id
                    and r.input_as_of_session == date(2026, 10, 26)
                    and bool(r.evidence_id),
                )
            for section in ["combined", "ranking", "fundamental"]:
                relationship(
                    section + ":sealed_evidence", all(r.evidence_id for r in rows[section])
                )

            for r in rows["technical"]:
                evidence(r, "TECHNICAL")
            for section in ("combined", "ranking"):
                for r in rows[section]:
                    evidence(
                        r,
                        section.upper(),
                        sources={
                            "fundamental": by_ticker["fundamental"][r.ticker].evidence_id,
                            "technical": by_ticker["technical"][r.ticker].evidence_id,
                        },
                    )

        def context():
            actual["market_regime"] = [
                _project(
                    r,
                    domains["market_regime"][0],
                    dict(
                        input_symbols=r.input_symbols_json,
                        participation=r.universe_participation_json,
                        reasons=r.reasons_json or [],
                        constituents=[x.ticker for x in rows["raw"]],
                    ),
                )
                for r in rows["market_regime"]
            ]
            actual["sector"] = []
            for parent in rows["sector_parent"]:
                constituents = [x.ticker for x in rows["raw"] if x.sector == "Technology Services"]
                readiness = db.get(m.CoreCalculationEvidence, parent.evidence_id).payload_json[
                    "producer_readiness"
                ]
                evidence(
                    parent,
                    "SECTOR",
                    sources={
                        "regime": rows["market_regime"][0].evidence_id,
                        **{
                            f"ranking:{i:06d}": row.evidence_id
                            for i, row in enumerate(
                                sorted(
                                    rows["ranking"], key=lambda x: (x.ranking_profile, x.ticker)
                                ),
                                1,
                            )
                        },
                    },
                )
                for r in db.scalars(
                    select(m.SectorRotationRow).where(m.SectorRotationRow.snapshot_id == parent.id)
                ):
                    actual["sector"].append(
                        _project(
                            r,
                            domains["sector"][0],
                            dict(
                                producer_readiness=readiness["status"],
                                consumer_permission="POLICY_UNDECIDED"
                                if readiness["status"] == "DEGRADED"
                                else "ELIGIBLE",
                                as_of_date=parent.as_of_date,
                                constituents=constituents,
                                permission=r.sector_permission,
                                component_scores=r.component_scores_json,
                                setup_distribution=r.setup_distribution_json,
                                top_counts=dict(
                                    top_10=r.top_10_count,
                                    top_25=r.top_25_count,
                                    top_50=r.top_50_count,
                                ),
                            ),
                        )
                    )
                relationship(
                    "sector_context",
                    parent.calculation_context_id == context_id
                    and parent.market_regime_snapshot_id in {x.id for x in rows["market_regime"]}
                    and bool(parent.evidence_id),
                )
            relationship(
                "regime_context",
                all(
                    r.calculation_context_id == context_id and r.evidence_id
                    for r in rows["market_regime"]
                ),
            )

            for r in rows["market_regime"]:
                evidence(r, "REGIME")
                inputs = db.get(m.CoreCalculationEvidence, r.evidence_id).payload_json["debug"][
                    "market_inputs"
                ]
                relationship(
                    "regime_bounded_inputs",
                    set(inputs) == {"SPY", "QQQ"}
                    and all(
                        x["price_frame_evidence"]["row_count"] == 320
                        and x["feature_debug"]["end_date"] == "2026-10-26"
                        and not x["insufficient_data"]
                        for x in inputs.values()
                    ),
                )

        def ceri():
            companies = {
                r.id: r.ticker
                for r in db.scalars(
                    select(c.CeriCompany).where(c.CeriCompany.ticker.in_(by_ticker["raw"]))
                )
            }
            sources = {
                r.id: r
                for r in db.scalars(
                    select(c.CeriSourceRecord).where(
                        c.CeriSourceRecord.restricted_normalized_json["ticker"].astext.in_(
                            companies.values()
                        )
                    )
                )
            }

            def record(key):
                return sources[key].provider_record_id if key is not None else None

            def records(model):
                return list(db.scalars(select(model).where(model.company_id.in_(companies))))

            estimates = records(c.CeriEstimateSnapshot)
            actual["estimates"] = [
                _project(
                    r,
                    domains["estimates"][0],
                    dict(
                        ticker=companies[r.company_id],
                        record_key=record(r.source_record_id),
                        known_at=r.known_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    ),
                )
                for r in estimates
            ]
            actual["guidance"] = [
                _project(
                    r,
                    domains["guidance"][0],
                    dict(
                        ticker=companies[r.company_id],
                        record_key=record(r.source_record_id),
                        supersedes=r.supersedes_id,
                    ),
                )
                for r in records(c.CeriGuidanceEvent)
            ]
            target_context = pipeline.result_json["ceri_calculation_context_id"]

            def pipeline_rows(model):
                return list(
                    db.scalars(
                        select(model).where(
                            model.company_id.in_(companies),
                            model.calculation_context_id == target_context,
                        )
                    )
                )

            derived = pipeline_rows(c.CeriDerivedFeature)
            actual["derived"] = [
                _project(r, domains["derived"][0], dict(ticker=companies[r.company_id]))
                for r in derived
            ]
            catalyst_features = {
                r.company_id: (r.value_json or {}).get("items", [])
                for r in derived
                if r.feature_family == "catalysts"
            }
            actual["catalysts"] = []
            for event in records(c.CeriCatalystEvent):
                for r in db.scalars(
                    select(c.CeriCatalystEventRevision).where(
                        c.CeriCatalystEventRevision.catalyst_event_id == event.id
                    )
                ):
                    feature = next(
                        x
                        for x in catalyst_features[event.company_id]
                        if x["catalyst_revision_id"] == r.id
                    )
                    actual["catalysts"].append(
                        _project(
                            r,
                            domains["catalysts"][0],
                            dict(
                                ticker=companies[event.company_id],
                                record_key=record(r.source_record_id),
                                category=event.category,
                                subject_key=event.subject_key,
                                prior_revision=r.prior_revision_id,
                                feature_binary_eligible=feature["binary_eligible"],
                                **{
                                    k: feature[k]
                                    for k in [
                                        "selected",
                                        "rejection_reason",
                                        "opportunity_available",
                                        "opportunity_component",
                                        "binary_risk_score",
                                    ]
                                },
                            ),
                        )
                    )
            revisions = pipeline_rows(c.CeriRevisionFeature)
            actual["revisions"] = [
                _project(
                    r,
                    domains["revisions"][0],
                    dict(
                        ticker=companies[r.company_id],
                        current_record_key=record(r.current_source_record_id),
                        baseline_record_key=record(r.baseline_source_record_id),
                        cutoff_at=r.calculation_cutoff_at,
                        warnings=r.warnings_json or [],
                    ),
                )
                for r in revisions
            ]
            prices = pipeline_rows(c.CeriPriceResponseFeature)
            actual["price_response"] = [
                _project(
                    r,
                    domains["price_response"][0],
                    dict(
                        quality=(r.metrics_json or {}).get("quality"),
                        unavailable_reason=(r.metrics_json or {}).get("unavailable_reason"),
                        price_members=r.price_bar_ids_json or [],
                    ),
                )
                for r in prices
            ]
            captures = list(
                db.scalars(select(c.CeriScoreSnapshot).where(c.CeriScoreSnapshot.run_id == run_id))
            )
            actual["captures"] = [
                _project(
                    r,
                    domains["captures"][0],
                    dict(
                        components={x["name"]: x["value"] for x in r.component_json["components"]},
                        confidence_score=r.confidence_ledger_json["score"],
                        pipeline_ownership_state=db.get(
                            m.CoreCalculationEvidence, r.evidence_id
                        ).calculation_identity_json["ownership"]["pipeline_id"]["state"],
                        warnings=r.warnings_json or [],
                    ),
                )
                for r in captures
            ]
            actual["earnings"] = [
                dict(ticker=companies[r.company_id]) for r in records(c.CeriEarningsActual)
            ]
            capture_ids = {r.id for r in captures}
            actual["changes"] = [
                dict(ticker=companies[r.company_id])
                for r in db.scalars(
                    select(c.CeriChangeEvent).where(
                        c.CeriChangeEvent.to_snapshot_id.in_(capture_ids)
                    )
                )
            ]
            actual["alerts"] = [
                dict(ticker=r.ticker)
                for r in db.scalars(
                    select(c.CeriAlertEvent).where(c.CeriAlertEvent.ticker.in_(by_ticker["raw"]))
                )
            ]
            relationship(
                "ceri_capture_context",
                all(r.calculation_context_id == target_context and r.evidence_id for r in captures),
            )
            for section, population in (
                ("derived", derived),
                ("revisions", revisions),
                ("price_response", prices),
            ):
                for r in population:
                    relationship(
                        "ceri_pipeline_owned:"
                        + section
                        + ":"
                        + relationship_key(
                            section,
                            {
                                key: (
                                    companies[r.company_id] if key == "ticker" else getattr(r, key)
                                )
                                for key in KEYS[section]
                            },
                        ),
                        r.ownership_mode == "PIPELINE"
                        and r.calculation_cutoff_at
                        == datetime.fromisoformat("2026-10-26T20:32:00+00:00")
                        and bool(r.config_hash),
                    )
            for r in captures:
                evidence(
                    r,
                    "CERI",
                    context=target_context,
                    cutoff="20:32",
                    pipeline_dimension={"state": "UNKNOWN"},
                )
                relationship(
                    "ceri_capture_issuer:captures:"
                    + relationship_key("captures", {"ticker": r.ticker}),
                    companies.get(r.company_id) == r.ticker
                    and db.get(m.CoreCalculationEvidence, r.evidence_id)
                    .calculation_identity_json["subject"]["company_id"]
                    .get("value")
                    == r.company_id,
                )
            capture_jobs = list(
                db.scalars(
                    select(m.BackgroundJob).where(
                        m.BackgroundJob.related_run_id == run_id,
                        m.BackgroundJob.job_type == "CERI_CAPTURE_RUN",
                    )
                )
            )
            ceri_context = db.get(m.MarketCalculationContext, target_context)
            relationship(
                "ceri_native_workflow_owner",
                len(capture_jobs) == 1
                and capture_jobs[0].status == "COMPLETED"
                and capture_jobs[0].payload_json.get("pipeline_run_id") == pipeline_id
                and capture_jobs[0].payload_json.get("calculation_context_id") == target_context
                and ceri_context.upload_run_id == run_id
                and ceri_context.pipeline_run_id is None,
            )
            details["ceri_owner"] = dict(
                run_id=run_id,
                pipeline_id=pipeline_id,
                retained_context_id=target_context,
                jobs=[dict(id=j.id, payload=j.payload_json) for j in capture_jobs],
            )
            for r in estimates:
                source = sources[r.source_record_id]
                relationship(
                    "ceri_source_issuer:estimates:"
                    + relationship_key(
                        "estimates",
                        {
                            "ticker": companies[r.company_id],
                            "record_key": record(r.source_record_id),
                        },
                    ),
                    source.restricted_normalized_json["ticker"] == companies[r.company_id]
                    and source.retrieved_at <= datetime.fromisoformat("2026-10-26T20:32:00+00:00"),
                )

        def setup():
            snapshots = list(
                db.scalars(
                    select(m.SetupSignalSnapshot).where(m.SetupSignalSnapshot.run_id == run_id)
                )
            )
            actual["setup"] = [
                _project(
                    r,
                    domains["setup"][0],
                    dict(
                        source_eligibility=_eligibility(
                            r.source_lineage_json["contextual_consumer_eligibility"]
                        ),
                        context_completeness=sum(
                            r.source_lineage_json.get(key) is not None
                            and r.source_lineage_json["contextual_consumer_eligibility"][role][
                                "included"
                            ]
                            for role, key in (
                                ("regime", "market_regime_as_of"),
                                ("sector", "sector_rotation_as_of"),
                            )
                        )
                        / 2,
                    ),
                )
                for r in snapshots
            ]
            ids = {r.id for r in snapshots}
            selected = list(
                db.scalars(
                    select(m.SetupSignalSnapshotCurrentSelection).where(
                        m.SetupSignalSnapshotCurrentSelection.data_as_of_date == date(2026, 10, 26),
                        m.SetupSignalSnapshotCurrentSelection.ticker.in_(by_ticker["raw"]),
                    )
                )
            )
            relationship(
                "setup_canonical_selected",
                len(selected) == 10
                and {r.selected_snapshot_id for r in selected} == ids
                and references_match(
                    {r.ticker: r.id for r in snapshots},
                    {r.ticker: r.selected_snapshot_id for r in selected},
                )
                and all(r.selected_run_id == run_id for r in selected),
            )
            episodes = list(
                db.scalars(
                    select(m.SetupLifecycleEpisode).where(
                        m.SetupLifecycleEpisode.opening_snapshot_id.in_(ids)
                    )
                )
            )
            actual["setup_episodes"] = [_project(r, domains["setup_episodes"][0]) for r in episodes]
            events = list(
                db.scalars(
                    select(m.SetupLifecycleEvent).where(m.SetupLifecycleEvent.snapshot_id.in_(ids))
                )
            )
            actual["setup_events"] = [_project(r, domains["setup_events"][0]) for r in events]
            evaluations = list(
                db.scalars(
                    select(m.SetupLifecycleEvaluationRun).where(
                        m.SetupLifecycleEvaluationRun.source_run_id == run_id,
                        m.SetupLifecycleEvaluationRun.mode == "LIVE",
                    )
                )
            )
            evaluation_ids = {r.id for r in evaluations}
            actual["setup_changes"] = [
                dict(ticker=r.ticker)
                for r in db.scalars(
                    select(m.SignalChangeEvent).where(
                        m.SignalChangeEvent.evaluation_run_id.in_(evaluation_ids)
                    )
                )
            ]
            actual["setup_alerts"] = [
                dict(ticker=r.ticker)
                for r in db.scalars(
                    select(m.SignalAlertEvent).where(
                        m.SignalAlertEvent.evaluation_run_id.in_(evaluation_ids)
                    )
                )
            ]
            variant = expected["completion"]["variants"][
                str(expected["flags"]["setup_capture_handoff_enabled"]).lower()
            ]
            creators = [r for r in evaluations if "capture_snapshot_membership" in r.audit_json]
            publishers = [r for r in evaluations if "published_snapshots" in r.audit_json]
            relationship(
                "setup_evaluation_roles",
                len(creators) == variant["capture_evaluation_count"]
                and len(publishers) == variant["publication_evaluation_count"]
                and all(r.selected_evaluation_run_id == publishers[0].id for r in selected)
                and {r.evaluation_run_id for r in snapshots} <= {r.id for r in creators}
                and all(
                    set(ev.audit_json["capture_snapshot_membership"]["snapshots"])
                    == {str(i) for i in ids}
                    for ev in creators
                ),
            )
            details["setup_evaluation_roles"] = dict(
                snapshots=[
                    dict(ticker=r.ticker, id=r.id, creator=r.evaluation_run_id) for r in snapshots
                ],
                captures=[dict(id=r.id, audit=r.audit_json) for r in creators],
                publishers=[dict(id=r.id, audit=r.audit_json) for r in publishers],
                selections=[
                    dict(
                        ticker=r.ticker,
                        snapshot=r.selected_snapshot_id,
                        publisher=r.selected_evaluation_run_id,
                    )
                    for r in selected
                ],
            )
            relationship(
                "setup_live_publication",
                len([r for r in evaluations if "published_snapshots" in r.audit_json]) == 1
                and {
                    key for r in evaluations for key in r.audit_json.get("published_snapshots", {})
                }
                == {str(x) for x in ids},
            )
            relationship(
                "setup_episode_references",
                all(
                    r.current_snapshot_id in ids and r.opening_snapshot_id in ids for r in episodes
                ),
            )
            for r in snapshots:
                ranking_row = next(x for x in rows["ranking"] if x.id == r.ranking_result_id)
                evidence(
                    r,
                    "SETUP",
                    sources={
                        "fundamental": by_ticker["fundamental"][r.ticker].evidence_id,
                        "technical": by_ticker["technical"][r.ticker].evidence_id,
                        "combined": by_ticker["combined"][r.ticker].evidence_id,
                        "ranking_metadata": ranking_row.evidence_id,
                        "regime": rows["market_regime"][0].evidence_id,
                        "sector": rows["sector_parent"][0].evidence_id,
                    },
                )
                relationship(
                    "setup_source:setup:" + relationship_key("setup", _project(r, KEYS["setup"])),
                    r.calculation_context_id == context_id
                    and r.evidence_id
                    and r.technical_score_id == by_ticker["technical"][r.ticker].id
                    and r.combined_result_id == by_ticker["combined"][r.ticker].id,
                )
            variant = expected["completion"]["variants"][
                str(expected["flags"]["setup_capture_handoff_enabled"]).lower()
            ]
            lines = []
            for path in env.artifact_dir.rglob("*.log"):
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if "setup.lifecycle.evaluation_inputs" in line:
                        lines.append(line)
            relationship(
                "setup_handoff_path",
                any(
                    variant["capture_path"] in line
                    and ("true" if variant["handoff_supplied"] else "false") in line.lower()
                    for line in lines
                ),
                lines=lines,
            )

        def winner():
            predictions = list(
                db.scalars(
                    select(m.WinnerPredictionSnapshot).where(
                        m.WinnerPredictionSnapshot.run_id == run_id
                    )
                )
            )
            ids = {r.id: r for r in predictions}
            actual["winner"] = [
                _project(
                    r,
                    domains["winner"][0],
                    dict(
                        features=r.feature_json,
                        warning_flags=r.warning_flags_json or [],
                        source_eligibility=_eligibility(
                            r.lineage_json["winner_consumer_eligibility"]
                        ),
                    ),
                )
                for r in predictions
            ]
            acquisition = list(
                db.scalars(
                    select(m.WinnerAcquisitionDecisionRecord).where(
                        m.WinnerAcquisitionDecisionRecord.pipeline_run_id == pipeline_id
                    )
                )
            )
            actual["winner_acquisition"] = [
                _project(r, domains["winner_acquisition"][0]) for r in acquisition
            ]
            forwards = list(
                db.scalars(
                    select(m.WinnerForwardOutcome).where(
                        m.WinnerForwardOutcome.prediction_id.in_(ids)
                    )
                )
            )
            actual["winner_forwards"] = [
                _project(r, domains["winner_forwards"][0], dict(ticker=ids[r.prediction_id].ticker))
                for r in forwards
            ]
            definitions = {r.id: r for r in db.scalars(select(m.WinnerOutcomeDefinition))}
            targets = list(
                db.scalars(
                    select(m.WinnerTargetStopOutcome).where(
                        m.WinnerTargetStopOutcome.prediction_id.in_(ids)
                    )
                )
            )
            actual["winner_targets"] = [
                _project(
                    r,
                    domains["winner_targets"][0],
                    dict(
                        ticker=ids[r.prediction_id].ticker,
                        definition=definitions[r.outcome_definition_id].definition_id,
                    ),
                )
                for r in targets
            ]
            estimates = list(
                db.scalars(
                    select(m.WinnerProbabilityEstimate).where(
                        m.WinnerProbabilityEstimate.prediction_id.in_(ids)
                    )
                )
            )
            actual["winner_estimates"] = [
                _project(
                    r,
                    domains["winner_estimates"][0],
                    dict(
                        ticker=ids[r.prediction_id].ticker,
                        definition=definitions[r.outcome_definition_id].definition_id,
                        insufficient_reasons=r.insufficient_reasons_json or [],
                    ),
                )
                for r in estimates
            ]
            manifest = db.scalar(
                select(m.TransitionDecisionHandoffManifest).where(
                    m.TransitionDecisionHandoffManifest.pipeline_run_id == pipeline_id
                )
            )
            ranking = {(r.ticker, r.ranking_profile): r for r in rows["ranking"]}
            for r in predictions:
                source = r.source_ids_json
                expected_source = {
                    "upload_run_id": run_id,
                    "raw_row_id": by_ticker["raw"][r.ticker].id,
                    "fundamental_score_id": by_ticker["fundamental"][r.ticker].id,
                    "technical_score_id": by_ticker["technical"][r.ticker].id,
                    "combined_result_id": by_ticker["combined"][r.ticker].id,
                    "ranking_result_id": ranking[(r.ticker, r.ranking_profile)].id,
                    "market_regime_snapshot_id": rows["market_regime"][0].id,
                    "sector_rotation_snapshot_id": None,
                    "sector_rotation_row_id": None,
                    "sector_evidence_id": None,
                }
                relationship(
                    "winner_sources:winner:"
                    + relationship_key("winner", _project(r, KEYS["winner"])),
                    references_match(expected_source, source),
                )
                native_fields = (
                    "raw_row_id",
                    "combined_result_id",
                    "ranking_result_id",
                    "market_regime_snapshot_id",
                    "sector_rotation_snapshot_id",
                )
                relationship(
                    "winner_native_foreign_keys:winner:"
                    + relationship_key("winner", _project(r, KEYS["winner"])),
                    references_match(
                        {k: expected_source[k] for k in native_fields},
                        {k: getattr(r, k) for k in native_fields},
                    ),
                )
                lineage = r.lineage_json
                relationship(
                    "winner_handoff:winner:"
                    + relationship_key("winner", _project(r, KEYS["winner"])),
                    lineage["decision_handoff"]["manifest_id"] == manifest.id
                    and lineage["decision_handoff"]["manifest_fingerprint"]
                    == manifest.manifest_fingerprint
                    and r.decision_at == manifest.created_at
                    and bool(r.config_hash),
                )
                relationship(
                    "winner_feature_scope:winner:"
                    + relationship_key("winner", _project(r, KEYS["winner"])),
                    not any("setup_snapshot" in k for k in r.feature_json),
                )
            relationship(
                "winner_acquisition_references",
                all(r.prediction_id in ids and r.upload_run_id == run_id for r in acquisition),
            )
            forward_ids = {r.id: r for r in forwards}
            relationship(
                "winner_target_references",
                all(
                    r.forward_outcome_id in forward_ids
                    and forward_ids[r.forward_outcome_id].prediction_id == r.prediction_id
                    and definitions[r.outcome_definition_id].entry_model == r.entry_model
                    for r in targets
                ),
            )
            relationship(
                "winner_estimate_configuration",
                all(
                    r.config_hash == ids[r.prediction_id].config_hash
                    and r.feature_schema_version == ids[r.prediction_id].feature_schema_version
                    for r in estimates
                ),
            )

        for name, read in [
            ("core", core),
            ("context", context),
            ("ceri", ceri),
            ("setup", setup),
            ("winner", winner),
        ]:
            try:
                read()
            except Exception as exc:
                errors.append(name + ":PROJECTION_ERROR:" + repr(exc))
    for section, wanted in domains.items():
        if section not in actual:
            errors.append(section + ":MISSING_PROJECTION")
            continue
        try:
            compare_vector(
                wanted,
                actual[section],
                keys=KEYS[section],
                tolerance="0"
                if section == "revisions"
                else "0.000001"
                if section == "captures"
                else "0.00000001",
            )
        except Exception as exc:
            errors.append(section + ":" + type(exc).__name__ + ":" + str(exc))
    found = [r["name"] for r in relationships]
    if sorted(found) != declared_relationships:
        errors.append("RELATIONSHIP_INVENTORY_MISMATCH")
    errors.extend("RELATIONSHIP:" + r["name"] for r in relationships if not r["passed"])
    path = env.artifact_dir / "db-results" / "independent-complete-oracle.json"
    path.write_text(
        json.dumps(
            dict(
                expected=expected["completion"],
                actual=actual,
                relationships=relationships,
                component_details=details,
                errors=errors,
                declared_relationships=declared_relationships,
                execution_id=env.execution_id,
                run_id=run_id,
                pipeline_id=pipeline_id,
                fresh_committed_session=True,
            ),
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    assert not errors, "\n".join(errors)
