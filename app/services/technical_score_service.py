import hashlib
import json
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from time import perf_counter
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import RawCompanyRow, TechnicalScore
from app.services.ib_fetch_executor import TickerReadyEvent
from app.services.leadership_v5 import rank_leadership_v5
from app.services.operational_metrics import operational_metrics
from app.services.pine_replica_engine import (
    ENGINE_VERSION,
    PineReplicaScore,
    relative_strength_score,
    score_from_feature_result,
)
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.price_series_version_service import load_series_versions
from app.services.relative_leadership import calculate_beta_adjusted_rs, rank_technical_universe
from app.services.sector_benchmark_service import (
    SectorBenchmarkResolution,
    mark_benchmark_data_missing,
    resolutions_for_tickers,
)
from app.services.technical_artifact_cache import (
    LocalArtifactKey,
    build_local_artifact_key,
    canonical_json,
    config_hash,
    get_local_artifact,
    record_local_artifact_shadow_validation,
    upsert_local_artifact,
)
from app.services.technical_explainability import add_leadership_to_explainability
from app.services.technical_indicators import (
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
    load_pine_defaults,
)
from app.services.technical_score_v4 import (
    TechnicalScoreV4,
    technical_score_v4_from_base_score,
)
from app.services.technical_score_v5 import TechnicalScoreV5, technical_score_v5_from_base_score
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.services.technical_work import (
    TechnicalWorkItem,
    TechnicalWorkResult,
    build_technical_work_item,
    execute_technical_work_item,
)
from app.settings import Settings, get_settings


class TechnicalScoringError(ValueError):
    pass


@dataclass(frozen=True)
class TechnicalV5RunContext:
    resolutions: dict[str, SectorBenchmarkResolution]
    sector_features: dict[str, dict[str, float | None]]


def score_run_technicals(
    db: Session,
    run_id: int,
    tickers: list[str] | None = None,
    benchmark_ticker: str = "SPY",
) -> list[TechnicalScore]:
    input_started = perf_counter()
    symbols = _normalize_tickers(tickers or _tickers_for_run(db, run_id))
    v4_params = load_technical_scoring_v4_config()
    v5_params = load_technical_scoring_v5_config()
    pine_params = load_pine_defaults()
    benchmark_price = _load_price_frame(db, benchmark_ticker)
    market_features = _market_features(benchmark_price, benchmark_ticker)
    sector_price = _sector_benchmark_price(db, pine_params)
    qqq_market_features = _optional_market_features(db, "QQQ", v4_params)

    settings = get_settings()
    calculate_v5 = getattr(settings, "technical_v5_enabled", False) or getattr(
        settings, "technical_v5_shadow_compare_enabled", True
    )
    v5_context = (
        _technical_v5_run_context(db, run_id, symbols, v5_params)
        if calculate_v5
        else TechnicalV5RunContext(resolutions={}, sector_features={})
    )
    indicator_config_hash = config_hash(pine_params)
    scoring_config_hash = config_hash(
        {"v4": v4_params, "v5": v5_params} if calculate_v5 else v4_params
    )
    artifact_cache_active = settings.technical_artifact_cache_writes_enabled
    _record_technical_duration("input_load", input_started, run_id=run_id)
    worker_started = perf_counter()
    if settings.technical_process_pool_enabled:
        score_results = _score_tickers_process_pool(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            pine_params=pine_params,
            v4_params=v4_params,
            settings=settings,
            run_id=run_id,
            indicator_config_hash=indicator_config_hash,
            scoring_config_hash=scoring_config_hash,
        )
    elif (
        settings.technical_pure_boundary_enabled
        or settings.technical_pure_boundary_shadow_compare_enabled
        or artifact_cache_active
    ):
        score_results = _score_tickers_pure_sequential(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            pine_params=pine_params,
            v4_params=v4_params,
            shadow_compare=settings.technical_pure_boundary_shadow_compare_enabled,
            run_id=run_id,
            settings=settings,
            indicator_config_hash=indicator_config_hash,
            scoring_config_hash=scoring_config_hash,
        )
    else:
        score_results = _score_tickers_legacy(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            v4_params=v4_params,
            run_id=run_id,
        )
    _record_technical_duration("worker_span", worker_started, run_id=run_id)

    finalize_started = perf_counter()
    scores = finalize_technical_scores(
        db,
        run_id,
        score_results,
        symbols=symbols,
        v4_params=v4_params,
        v5_params=v5_params,
        v5_context=v5_context,
        settings=settings,
    )
    _record_technical_duration("finalize", finalize_started, run_id=run_id)
    return scores


def finalize_technical_scores(
    db: Session,
    run_id: int,
    score_results: list[PineReplicaScore | TechnicalScore],
    *,
    symbols: list[str] | None = None,
    v4_params: dict[str, Any] | None = None,
    v5_params: dict[str, Any] | None = None,
    v5_context: TechnicalV5RunContext | None = None,
    settings: Settings | None = None,
) -> list[TechnicalScore]:
    v4_params = v4_params or load_technical_scoring_v4_config()
    v5_params = v5_params or load_technical_scoring_v5_config()
    settings = settings or get_settings()
    pine_params = load_pine_defaults()
    symbols = symbols or [
        result.ticker.upper()
        for result in score_results
        if isinstance(result, (PineReplicaScore, TechnicalScore))
    ]
    scored = [result for result in score_results if isinstance(result, PineReplicaScore)]
    leadership = rank_technical_universe(
        [_leadership_rank_input(score) for score in scored],
        v4_params.get("relative_leadership", {}),
    )
    calculate_v5 = getattr(settings, "technical_v5_enabled", False) or getattr(
        settings, "technical_v5_shadow_compare_enabled", True
    )
    v5_context = v5_context or (
        _technical_v5_run_context(db, run_id, symbols, v5_params)
        if calculate_v5
        else TechnicalV5RunContext(resolutions={}, sector_features={})
    )
    v5_leadership = (
        rank_leadership_v5(
            [
                _leadership_v5_rank_input(
                    score,
                    v5_context.resolutions.get(score.ticker.upper()),
                    v5_context.sector_features,
                    v5_params["sector_benchmarks"],
                )
                for score in scored
            ],
            v5_params["leadership"],
        )
        if calculate_v5
        else {}
    )
    scores: list[TechnicalScore] = []
    for result in score_results:
        if not isinstance(result, PineReplicaScore):
            scores.append(result)
            continue
        base_with_v4_leadership = _with_leadership_debug(result, leadership)
        v4_score = technical_score_v4_from_base_score(base_with_v4_leadership, v4_params)
        v5_score = None
        if calculate_v5:
            resolution = v5_context.resolutions.get(
                result.ticker.upper(),
                SectorBenchmarkResolution(
                    None,
                    None,
                    "MISSING_SECTOR",
                    "sector_not_available; using broad-market RS only",
                ),
            )
            sector_score = _v5_sector_relative_score(
                result,
                resolution,
                v5_context.sector_features,
            )
            if resolution.status == "RESOLVED" and sector_score is None:
                resolution = mark_benchmark_data_missing(resolution)
            v5_base = _with_v5_sector_debug(result, sector_score)
            v5_score = technical_score_v5_from_base_score(
                v5_base,
                leadership=v5_leadership.get(result.ticker.upper()),
                sector_resolution=resolution,
                v5_config=v5_params,
                pine_config=pine_params,
            )
        scores.append(
            build_technical_score(
                run_id=run_id,
                score=v4_score,
                v5_score=v5_score,
                v5_active=getattr(settings, "technical_v5_enabled", False),
                persist_v5=(
                    getattr(settings, "technical_v5_enabled", False)
                    or getattr(settings, "technical_v5_persist_shadow_results", True)
                ),
            )
        )
    if symbols:
        db.execute(
            delete(TechnicalScore).where(
                TechnicalScore.run_id == run_id,
                TechnicalScore.ticker.in_(symbols),
            )
        )
    db.add_all(scores)
    db.flush()
    return scores


class TechnicalScoringOverlapCoordinator:
    """Submit committed ticker inputs while the serialized IB loop continues."""

    def __init__(
        self,
        db: Session,
        *,
        run_id: int,
        tickers: list[str],
        settings: Settings | None = None,
        should_cancel: Callable[[], bool] | None = None,
        lease_guard: Callable[[], None] | None = None,
        required_market_tickers: list[str] | tuple[str, ...] | None = None,
        wait_for_market_events: bool = False,
    ) -> None:
        input_started = perf_counter()
        self.db = db
        self.run_id = run_id
        self.symbols = _normalize_tickers(tickers)
        self.settings = settings or get_settings()
        self.should_cancel = should_cancel or (lambda: False)
        self.lease_guard = lease_guard or (lambda: None)
        self.pine_params = load_pine_defaults()
        self.v4_params = load_technical_scoring_v4_config()
        self.v5_params = load_technical_scoring_v5_config()
        self.indicator_config_hash = config_hash(self.pine_params)
        self.scoring_config_hash = config_hash(
            {"v4": self.v4_params, "v5": self.v5_params}
            if getattr(self.settings, "technical_v5_enabled", False)
            or getattr(self.settings, "technical_v5_shadow_compare_enabled", True)
            else self.v4_params
        )
        self._ready: set[str] = set()
        self._pending: set[str] = set()
        self._results: dict[str, PineReplicaScore | TechnicalScore] = {}
        self._work_results: dict[str, TechnicalWorkResult] = {}
        self._futures: dict[Any, str] = {}
        self._submitted_market_signatures: dict[str, str] = {}
        self._required_market_tickers = {
            ticker.strip().upper() for ticker in (required_market_tickers or ()) if ticker.strip()
        }
        self._wait_for_market_events = bool(
            wait_for_market_events and self._required_market_tickers
        )
        self._closed = False
        self._fetch_active = True
        self._completed_during_fetch = 0
        self._fallback_reason: str | None = None
        self._cancelled = False
        self._refresh_run_level_inputs()
        _record_technical_duration("input_load", input_started, run_id=self.run_id)
        self._worker_started_at = perf_counter()
        self._executor: ProcessPoolExecutor | None = None
        try:
            self._executor = ProcessPoolExecutor(max_workers=_technical_worker_count(self.settings))
        except Exception as exc:
            self._activate_sequential_fallback(exc)

    def on_ticker_ready(self, event: TickerReadyEvent) -> None:
        if self._closed or self._fallback_reason is not None:
            return
        try:
            ticker = event.ticker.upper()
            self._ready.add(ticker)
            if ticker in self.symbols:
                self._pending.add(ticker)
            if ticker in self._required_market_tickers or ticker in {"SPY", "QQQ"}:
                self._refresh_run_level_inputs()
            self._submit_ready(block_when_full=False)
        except Exception as exc:
            self._activate_sequential_fallback(exc)

    def mark_fetch_complete(self) -> None:
        if self._fallback_reason is None:
            try:
                self._drain_completed(block=False)
            except Exception as exc:
                self._activate_sequential_fallback(exc)
        self._fetch_active = False

    def finalize(self) -> list[TechnicalScore]:
        try:
            self.mark_fetch_complete()
            self.lease_guard()
            if self.should_cancel() or self._cancelled:
                raise TechnicalScoringError("Technical overlap was cancelled.")
            if self._fallback_reason is not None:
                return self._finalize_sequential_fallback()
            self._ready.update(self.symbols)
            self._pending.update(self.symbols)
            # Fetching has finished before finalize is called. Refresh once more and
            # release the barrier even if a custom fetch executor omitted callbacks.
            self._refresh_run_level_inputs()
            self._wait_for_market_events = False
            self._submit_ready(block_when_full=True)
            while self._futures:
                self._drain_one()
                self._submit_ready(block_when_full=True)
            if self._fallback_reason is not None:
                return self._finalize_sequential_fallback()
            # A benchmark can change after work was submitted but before fetching
            # finishes. Never persist a technical result calculated from a different
            # market-wide input snapshot than the final run-level snapshot.
            self._refresh_run_level_inputs()
            self._resubmit_stale_market_results()
            while self._futures:
                self._drain_one()
                self._submit_ready(block_when_full=True)
            if self._fallback_reason is not None:
                return self._finalize_sequential_fallback()
            for ticker in self.symbols:
                if ticker not in self._results:
                    self._results[ticker] = unavailable_technical_score(
                        self.run_id,
                        ticker,
                        "Ticker was not submitted for technical overlap.",
                        v4_params=self.v4_params,
                    )
            ordered = [self._results[ticker] for ticker in self.symbols]
            self._persist_completed_artifacts()
            _record_technical_duration("worker_span", self._worker_started_at, run_id=self.run_id)
            finalize_started = perf_counter()
            scores = finalize_technical_scores(
                self.db,
                self.run_id,
                ordered,
                symbols=self.symbols,
                v4_params=self.v4_params,
                v5_params=self.v5_params,
                settings=self.settings,
            )
            _record_technical_duration("finalize", finalize_started, run_id=self.run_id)
            return scores
        finally:
            self._closed = True
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=True)

    def abort(self) -> None:
        if self._closed:
            return
        self._closed = True
        for future in self._futures:
            future.cancel()
        self._futures.clear()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _submit_ready(self, *, block_when_full: bool) -> None:
        if (
            self._closed
            or self._fallback_reason is not None
            or self._executor is None
            or not self._benchmark_price_available
            or not self._market_inputs_ready
        ):
            return
        self._drain_completed(block=False)
        for ticker in sorted(tuple(self._pending)):
            if (
                ticker not in self.symbols
                or ticker in self._results
                or ticker in self._futures.values()
            ):
                self._pending.discard(ticker)
                continue
            if len(self._futures) >= self._in_flight_limit:
                if not block_when_full:
                    break
                self._drain_one()
                if self._fallback_reason is not None:
                    return
            self.lease_guard()
            if self.should_cancel():
                self._cancelled = True
                return
            input_started = perf_counter()
            price, trades = load_preferred_ohlcv_frames(self.db, ticker)
            artifact_key, cached_artifact = _artifact_cache_context(
                db=self.db,
                ticker=ticker,
                settings=self.settings,
                indicator_config_hash=self.indicator_config_hash,
                scoring_config_hash=self.scoring_config_hash,
            )
            item = _build_work_item(
                ticker=ticker,
                price=price,
                trades=trades,
                benchmark_price=self._benchmark_price,
                sector_price=self._sector_price,
                market_features=self._market_features,
                qqq_market_features=self._qqq_market_features,
                pine_params=self.pine_params,
                v4_params=self.v4_params,
                artifact_key=artifact_key,
                cached_local_artifact=(
                    cached_artifact
                    if self.settings.technical_artifact_cache_active_reads_enabled
                    else None
                ),
                shadow_local_artifact=(
                    cached_artifact
                    if self.settings.technical_artifact_cache_shadow_validation_enabled
                    else None
                ),
            )
            _record_technical_duration("input_load", input_started, run_id=self.run_id)
            self._submitted_market_signatures[ticker] = self._market_input_signature
            try:
                future = self._executor.submit(execute_technical_work_item, item)
            except Exception as exc:
                self._activate_sequential_fallback(exc)
                return
            self._futures[future] = ticker
            self._pending.discard(ticker)

    def _drain_one(self) -> None:
        self._drain_completed(block=True)

    def _drain_completed(self, *, block: bool) -> None:
        if not self._futures:
            return
        completed, _ = wait(
            self._futures,
            timeout=None if block else 0,
            return_when=FIRST_COMPLETED,
        )
        for future in sorted(completed, key=lambda item: self._futures[item]):
            ticker = self._futures.pop(future)
            try:
                work_result = future.result()
                self._results[ticker] = _score_from_work_result(work_result)
                self._work_results[ticker] = work_result
                if self._fetch_active:
                    self._completed_during_fetch += 1
            except BrokenProcessPool as exc:
                self._activate_sequential_fallback(exc)
                return
            except Exception as exc:
                self._results[ticker] = unavailable_technical_score(
                    self.run_id,
                    ticker,
                    str(exc),
                    v4_params=self.v4_params,
                )
            self.lease_guard()
            if self.should_cancel():
                self._cancelled = True
                for outstanding in self._futures:
                    outstanding.cancel()
                self._futures.clear()
                return

    def _persist_completed_artifacts(self) -> None:
        for ticker in self.symbols:
            work_result = self._work_results.get(ticker)
            if work_result is None:
                continue
            self.lease_guard()
            if self.should_cancel():
                self._cancelled = True
                raise TechnicalScoringError("Technical overlap was cancelled.")
            artifact_key = (
                LocalArtifactKey(**work_result.artifact_key) if work_result.artifact_key else None
            )
            _persist_local_artifact(
                self.db,
                work_result,
                artifact_key,
                enabled=self.settings.technical_artifact_cache_writes_enabled,
            )
            _record_shadow_validation(
                self.db,
                work_result,
                artifact_key,
                run_id=self.run_id,
                enabled=self.settings.technical_artifact_cache_shadow_validation_enabled,
            )

    def _activate_sequential_fallback(self, exc: Exception) -> None:
        if self._fallback_reason is not None:
            return
        self._fallback_reason = type(exc).__name__
        operational_metrics.increment(
            "swinglens_pipeline_optimized_fallback_total",
            component="technical_overlap",
            reason=self._fallback_reason,
        )
        for future in self._futures:
            future.cancel()
        self._futures.clear()
        self._results.clear()
        self._work_results.clear()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _finalize_sequential_fallback(self) -> list[TechnicalScore]:
        self.lease_guard()
        if self.should_cancel() or self._cancelled:
            raise TechnicalScoringError("Technical overlap was cancelled.")
        self._refresh_run_level_inputs()
        results = _score_tickers_pure_sequential(
            db=self.db,
            symbols=self.symbols,
            benchmark_price=self._benchmark_price,
            sector_price=self._sector_price,
            market_features=self._market_features,
            qqq_market_features=self._qqq_market_features,
            pine_params=self.pine_params,
            v4_params=self.v4_params,
            shadow_compare=False,
            run_id=self.run_id,
            settings=self.settings,
            indicator_config_hash=self.indicator_config_hash,
            scoring_config_hash=self.scoring_config_hash,
        )
        _record_technical_duration(
            "worker_span",
            self._worker_started_at,
            run_id=self.run_id,
        )
        self.lease_guard()
        if self.should_cancel():
            raise TechnicalScoringError("Technical overlap was cancelled.")
        finalize_started = perf_counter()
        scores = finalize_technical_scores(
            self.db,
            self.run_id,
            results,
            symbols=self.symbols,
            v4_params=self.v4_params,
            v5_params=self.v5_params,
            settings=self.settings,
        )
        _record_technical_duration("finalize", finalize_started, run_id=self.run_id)
        return scores

    def _refresh_run_level_inputs(self) -> None:
        self._benchmark_price = _load_price_frame(self.db, "SPY")
        self._market_features = _market_features(self._benchmark_price, "SPY")
        self._sector_price = _sector_benchmark_price(self.db, self.pine_params)
        market_rs = self.pine_params.get("market_rs", {})
        self._sector_ticker = (
            str(market_rs.get("sectorSymbol") or "").strip().upper()
            if market_rs.get("useSectorBenchmark", False)
            else ""
        )
        market_regime_params = self.v4_params.get("market_regime_v4", {})
        if market_regime_params.get("use_qqq", True):
            self._qqq_market_price = _load_price_frame(self.db, "QQQ")
            self._qqq_market_features = _market_features(
                self._qqq_market_price,
                "QQQ",
            )
        else:
            self._qqq_market_price = pd.DataFrame()
            self._qqq_market_features = {}
        self._market_input_signature = _market_frames_signature(
            self._benchmark_price,
            self._qqq_market_price,
            sector_ticker=self._sector_ticker,
            sector_price=self._sector_price,
        )

    def _resubmit_stale_market_results(self) -> None:
        stale_tickers = [
            ticker
            for ticker in self.symbols
            if ticker in self._results
            and self._submitted_market_signatures.get(ticker) != self._market_input_signature
        ]
        if not stale_tickers:
            return
        for ticker in stale_tickers:
            self._results.pop(ticker, None)
            self._work_results.pop(ticker, None)
            self._pending.add(ticker)
        operational_metrics.increment(
            "swinglens_technical_overlap_market_rescore_total",
            value=len(stale_tickers),
        )
        self._submit_ready(block_when_full=True)

    @property
    def completed_during_fetch(self) -> int:
        return self._completed_during_fetch

    @property
    def fallback_reason(self) -> str | None:
        return self._fallback_reason

    @property
    def _benchmark_price_available(self) -> bool:
        return not self._benchmark_price.empty

    @property
    def _market_inputs_ready(self) -> bool:
        return not self._wait_for_market_events or self._required_market_tickers.issubset(
            self._ready
        )

    @property
    def _in_flight_limit(self) -> int:
        return min(
            self.settings.technical_max_in_flight,
            _technical_worker_count(self.settings) * 2,
        )


def _score_tickers_legacy(
    *,
    db: Session,
    symbols: list[str],
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    v4_params: dict[str, Any],
    run_id: int,
) -> list[PineReplicaScore | TechnicalScore]:
    score_results: list[PineReplicaScore | TechnicalScore] = []
    for ticker in symbols:
        try:
            score_results.append(
                _score_ticker(
                    db=db,
                    ticker=ticker,
                    benchmark_price=benchmark_price,
                    sector_price=sector_price,
                    market_features=market_features,
                    qqq_market_features=qqq_market_features,
                    v4_params=v4_params,
                )
            )
        except Exception as exc:
            score_results.append(
                unavailable_technical_score(run_id, ticker, str(exc), v4_params=v4_params)
            )
    operational_metrics.increment(
        "swinglens_technical_scoring_runs_total",
        mode="legacy",
    )
    return score_results


def _score_tickers_pure_sequential(
    *,
    db: Session,
    symbols: list[str],
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    pine_params: dict[str, Any],
    v4_params: dict[str, Any],
    shadow_compare: bool,
    run_id: int,
    settings: Settings,
    indicator_config_hash: str,
    scoring_config_hash: str,
) -> list[PineReplicaScore | TechnicalScore]:
    results: list[PineReplicaScore | TechnicalScore] = []
    for ticker in symbols:
        try:
            price, trades = load_preferred_ohlcv_frames(db, ticker)
            artifact_key, cached_artifact = _artifact_cache_context(
                db=db,
                ticker=ticker,
                settings=settings,
                indicator_config_hash=indicator_config_hash,
                scoring_config_hash=scoring_config_hash,
            )
            item = _build_work_item(
                ticker=ticker,
                price=price,
                trades=trades,
                benchmark_price=benchmark_price,
                sector_price=sector_price,
                market_features=market_features,
                qqq_market_features=qqq_market_features,
                pine_params=pine_params,
                v4_params=v4_params,
                artifact_key=artifact_key,
                cached_local_artifact=(
                    cached_artifact
                    if settings.technical_artifact_cache_active_reads_enabled
                    else None
                ),
                shadow_local_artifact=(
                    cached_artifact
                    if settings.technical_artifact_cache_shadow_validation_enabled
                    else None
                ),
            )
            pure_result = execute_technical_work_item(item)
            pure_score = _score_from_work_result(pure_result)
            if shadow_compare:
                legacy_score = _legacy_score_or_error(
                    db=db,
                    ticker=ticker,
                    benchmark_price=benchmark_price,
                    sector_price=sector_price,
                    market_features=market_features,
                    qqq_market_features=qqq_market_features,
                    v4_params=v4_params,
                    run_id=run_id,
                )
                if _technical_score_fingerprint(pure_score) != _technical_score_fingerprint(
                    legacy_score
                ):
                    operational_metrics.increment(
                        "swinglens_technical_pure_boundary_shadow_mismatches_total"
                    )
                    pure_score = legacy_score
            _persist_local_artifact(
                db,
                pure_result,
                artifact_key,
                enabled=settings.technical_artifact_cache_writes_enabled,
            )
            _record_shadow_validation(
                db,
                pure_result,
                artifact_key,
                run_id=run_id,
                enabled=settings.technical_artifact_cache_shadow_validation_enabled,
            )
            results.append(pure_score)
        except Exception as exc:
            results.append(
                unavailable_technical_score(run_id, ticker, str(exc), v4_params=v4_params)
            )
    operational_metrics.increment(
        "swinglens_technical_scoring_runs_total",
        mode="pure_sequential_shadow" if shadow_compare else "pure_sequential",
    )
    return results


def _score_tickers_process_pool(
    *,
    db: Session,
    symbols: list[str],
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    pine_params: dict[str, Any],
    v4_params: dict[str, Any],
    settings: Settings,
    run_id: int,
    indicator_config_hash: str,
    scoring_config_hash: str,
) -> list[PineReplicaScore | TechnicalScore]:
    items: list[tuple[int, TechnicalWorkItem]] = []
    results: list[PineReplicaScore | TechnicalScore | None] = [None] * len(symbols)
    for index, ticker in enumerate(symbols):
        try:
            price, trades = load_preferred_ohlcv_frames(db, ticker)
            artifact_key, cached_artifact = _artifact_cache_context(
                db=db,
                ticker=ticker,
                settings=settings,
                indicator_config_hash=indicator_config_hash,
                scoring_config_hash=scoring_config_hash,
            )
            items.append(
                (
                    index,
                    _build_work_item(
                        ticker=ticker,
                        price=price,
                        trades=trades,
                        benchmark_price=benchmark_price,
                        sector_price=sector_price,
                        market_features=market_features,
                        qqq_market_features=qqq_market_features,
                        pine_params=pine_params,
                        v4_params=v4_params,
                        artifact_key=artifact_key,
                        cached_local_artifact=(
                            cached_artifact
                            if settings.technical_artifact_cache_active_reads_enabled
                            else None
                        ),
                        shadow_local_artifact=(
                            cached_artifact
                            if settings.technical_artifact_cache_shadow_validation_enabled
                            else None
                        ),
                    ),
                )
            )
        except Exception as exc:
            results[index] = unavailable_technical_score(
                run_id, ticker, str(exc), v4_params=v4_params
            )

    try:
        workers = _technical_worker_count(settings)
        in_flight_limit = min(settings.technical_max_in_flight, workers * 2)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            pending = {}
            next_item = iter(items)
            while len(pending) < in_flight_limit:
                try:
                    index, item = next(next_item)
                except StopIteration:
                    break
                pending[executor.submit(execute_technical_work_item, item)] = (index, item)

            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    index, item = pending.pop(future)
                    try:
                        work_result = future.result()
                        results[index] = _score_from_work_result(work_result)
                        _persist_local_artifact(
                            db,
                            work_result,
                            artifact_key := (
                                LocalArtifactKey(**work_result.artifact_key)
                                if work_result.artifact_key
                                else None
                            ),
                            enabled=settings.technical_artifact_cache_writes_enabled,
                        )
                        _record_shadow_validation(
                            db,
                            work_result,
                            artifact_key,
                            run_id=run_id,
                            enabled=(settings.technical_artifact_cache_shadow_validation_enabled),
                        )
                    except Exception as exc:
                        results[index] = unavailable_technical_score(
                            run_id,
                            item.ticker,
                            str(exc),
                            v4_params=v4_params,
                        )
                    try:
                        next_index, next_item_value = next(next_item)
                    except StopIteration:
                        continue
                    pending[executor.submit(execute_technical_work_item, next_item_value)] = (
                        next_index,
                        next_item_value,
                    )
    except Exception as exc:
        operational_metrics.increment(
            "swinglens_technical_process_pool_fallback_total",
            reason=type(exc).__name__,
        )
        return _score_tickers_pure_sequential(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            pine_params=pine_params,
            v4_params=v4_params,
            shadow_compare=False,
            run_id=run_id,
            settings=settings,
            indicator_config_hash=indicator_config_hash,
            scoring_config_hash=scoring_config_hash,
        )

    operational_metrics.increment(
        "swinglens_technical_scoring_runs_total",
        mode="process_pool",
        workers=_technical_worker_count(settings),
    )
    return [
        result
        if result is not None
        else unavailable_technical_score(
            run_id, symbols[index], "Technical worker returned no result.", v4_params=v4_params
        )
        for index, result in enumerate(results)
    ]


def _record_technical_duration(name: str, started_at: float, *, run_id: int) -> None:
    operational_metrics.increment(
        f"swinglens_technical_{name}_ms_total",
        value=max(0.0, (perf_counter() - started_at) * 1000),
        run_id=run_id,
    )


def _build_work_item(
    *,
    ticker: str,
    price: pd.DataFrame,
    trades: pd.DataFrame | None,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    pine_params: dict[str, Any],
    v4_params: dict[str, Any],
    artifact_key: LocalArtifactKey | None = None,
    cached_local_artifact: dict[str, Any] | None = None,
    shadow_local_artifact: dict[str, Any] | None = None,
) -> TechnicalWorkItem:
    return build_technical_work_item(
        ticker=ticker,
        price=price,
        trades=trades,
        benchmark_price=benchmark_price,
        sector_price=sector_price,
        technical_config=v4_params,
        pine_config=pine_params,
        relative_config=v4_params.get("relative_leadership", {}),
        market_features=market_features,
        qqq_market_features=qqq_market_features,
        input_signature=artifact_key.input_signature if artifact_key else "",
        artifact_key=asdict(artifact_key) if artifact_key else None,
        cached_local_artifact=cached_local_artifact,
        shadow_local_artifact=shadow_local_artifact,
    )


def _artifact_cache_context(
    *,
    db: Session,
    ticker: str,
    settings: Settings,
    indicator_config_hash: str,
    scoring_config_hash: str,
) -> tuple[LocalArtifactKey | None, dict[str, Any] | None]:
    cache_reads = settings.technical_artifact_cache_reads_enabled
    cache_writes = settings.technical_artifact_cache_writes_enabled
    if not cache_reads and not cache_writes:
        return None, None
    versions = load_series_versions(db, ticker)
    if "ADJUSTED_LAST" not in versions or "TRADES" not in versions:
        return None, None
    key = build_local_artifact_key(
        ticker=ticker,
        adjusted_series_version=versions["ADJUSTED_LAST"],
        trades_series_version=versions["TRADES"],
        indicator_config_hash=indicator_config_hash,
        scoring_config_hash=scoring_config_hash,
        technical_engine_version=ENGINE_VERSION,
    )
    if not cache_reads:
        return key, None
    artifact = get_local_artifact(
        db,
        key,
        usage=(
            "shadow" if settings.technical_artifact_cache_shadow_validation_enabled else "active"
        ),
    )
    return key, artifact.artifact_json if artifact is not None else None


def _persist_local_artifact(
    db: Session,
    result: TechnicalWorkResult,
    key: LocalArtifactKey | None,
    *,
    enabled: bool,
) -> None:
    if not enabled or key is None or result.score is None or result.error is not None:
        return
    upsert_local_artifact(
        db,
        key,
        artifact_json={
            "feature_result": result.feature_result,
            "htf_features": result.htf_features,
        },
        warning_flags=result.warnings,
    )


def _record_shadow_validation(
    db: Session,
    result: TechnicalWorkResult,
    key: LocalArtifactKey | None,
    *,
    run_id: int,
    enabled: bool,
) -> None:
    if (
        not enabled
        or key is None
        or result.score is None
        or (result.shadow_score is None and result.shadow_error is None)
    ):
        return
    fresh_fingerprint = _technical_score_digest(result.score)
    cached_fingerprint = (
        _technical_score_digest(result.shadow_score) if result.shadow_score is not None else None
    )
    record_local_artifact_shadow_validation(
        db,
        key,
        matched=(result.shadow_error is None and cached_fingerprint == fresh_fingerprint),
        fresh_fingerprint=fresh_fingerprint,
        cached_fingerprint=cached_fingerprint,
        run_id=run_id,
        error=result.shadow_error,
        differences=(
            _technical_score_differences(result.score, result.shadow_score)
            if result.shadow_score is not None
            else {}
        ),
    )


def _score_from_work_result(result: TechnicalWorkResult) -> PineReplicaScore:
    if result.score is None:
        raise TechnicalScoringError(result.error or "Technical worker failed.")
    return result.score


def _legacy_score_or_error(
    *,
    db: Session,
    ticker: str,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    v4_params: dict[str, Any],
    run_id: int,
) -> PineReplicaScore | TechnicalScore:
    try:
        return _score_ticker(
            db=db,
            ticker=ticker,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            v4_params=v4_params,
        )
    except Exception as exc:
        return unavailable_technical_score(run_id, ticker, str(exc), v4_params=v4_params)


def _technical_score_fingerprint(score: PineReplicaScore | TechnicalScore) -> str:
    return json.dumps(_technical_score_payload(score), default=str, sort_keys=True)


def _technical_score_payload(
    score: PineReplicaScore | TechnicalScore,
) -> dict[str, Any]:
    if isinstance(score, TechnicalScore):
        return {
            key: getattr(score, key)
            for key in (
                "ticker",
                "trend_score",
                "local_trend_score",
                "momentum_score",
                "setup_score",
                "risk_score",
                "market_score",
                "relative_strength_score",
                "sector_relative_strength_score",
                "combined_relative_strength_score",
                "htf_score",
                "dual_score",
                "classification",
                "action_bias",
                "pullback_health",
                "suggested_stop",
                "suggested_target",
                "reward_risk",
                "entry_risk_pct",
                "technical_confidence",
                "technical_engine_version",
                "data_quality_score",
                "stage",
                "market_regime",
                "leadership_score",
                "vcp_score",
                "box_tightness_score",
                "breakout_quality_score",
                "climax_risk_score",
                "atr_percentile_252",
                "volume_percentile_252",
                "range_percentile_252",
                "extension_percentile_252",
                "feature_flags_json",
                "warning_flags_json",
                "sub_tags_json",
                "v4_debug_json",
                "insufficient_data",
                "missing_data_json",
                "debug_json",
            )
        }
    return asdict(score)


def _technical_score_digest(score: PineReplicaScore | TechnicalScore) -> str:
    return hashlib.sha256(_technical_score_fingerprint(score).encode("utf-8")).hexdigest()


def _technical_score_differences(
    fresh: PineReplicaScore | TechnicalScore,
    cached: PineReplicaScore | TechnicalScore,
) -> dict[str, Any]:
    fresh_payload = _technical_score_payload(fresh)
    cached_payload = _technical_score_payload(cached)
    differing_fields = sorted(
        key
        for key in fresh_payload.keys() | cached_payload.keys()
        if canonical_json(fresh_payload.get(key)) != canonical_json(cached_payload.get(key))
    )
    return {
        "differing_fields": differing_fields,
        "value_preview": {
            key: {
                "fresh": _diagnostic_preview(fresh_payload.get(key)),
                "cached": _diagnostic_preview(cached_payload.get(key)),
            }
            for key in differing_fields[:20]
        },
    }


def _diagnostic_preview(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (list, tuple)) and len(value) <= 20:
        return value
    if isinstance(value, dict) and len(value) <= 20:
        return value
    return f"<{type(value).__name__}>"


def _technical_worker_count(settings: Settings) -> int:
    return settings.technical_worker_processes


def unavailable_technical_score(
    run_id: int,
    ticker: str,
    reason: str,
    v4_params: dict[str, Any] | None = None,
) -> TechnicalScore:
    error_payload = _v4_error_payload(reason, v4_params or {})
    return TechnicalScore(
        run_id=run_id,
        ticker=ticker.upper(),
        classification="No trade",
        action_bias="No data",
        technical_confidence="error",
        technical_engine_version=error_payload["engine_version"],
        data_quality_score=Decimal("0.0"),
        feature_flags_json=[],
        warning_flags_json=["technical_error"],
        sub_tags_json=[],
        v4_debug_json=error_payload,
        insufficient_data=True,
        missing_data_json={
            "unavailable": True,
            "reason": reason,
            "technical_error": True,
        },
        debug_json={
            "error": reason,
            "explainability": error_payload,
        },
    )


def build_technical_score(
    run_id: int,
    score: PineReplicaScore | TechnicalScoreV4,
    *,
    v5_score: TechnicalScoreV5 | None = None,
    v5_active: bool = False,
    persist_v5: bool = True,
) -> TechnicalScore:
    base_score = _base_score(score)
    debug = score.debug if isinstance(score, TechnicalScoreV4) else base_score.debug
    v4_dual_score = (
        score.final_v4_score if isinstance(score, TechnicalScoreV4) else base_score.dual_score
    )
    v4_classification = (
        score.final_v4_classification
        if isinstance(score, TechnicalScoreV4)
        else base_score.classification
    )
    v4_action_bias = (
        score.final_v4_action if isinstance(score, TechnicalScoreV4) else base_score.action_bias
    )
    v4_confidence = base_score.technical_confidence or (
        "low" if base_score.insufficient_data else "normal"
    )
    v4_fields = _v4_persistence_fields(debug)
    use_v5 = bool(v5_active and v5_score is not None)
    dual_score = v5_score.technical_composite_score if use_v5 else v4_dual_score
    classification = v5_score.classification if use_v5 else v4_classification
    action_bias = v5_score.action_bias if use_v5 else v4_action_bias
    confidence = v5_score.technical_confidence if use_v5 else v4_confidence
    v5_fields = _v5_persistence_fields(
        v5_score if persist_v5 else None,
        v4_score=v4_dual_score,
        v4_classification=v4_classification,
        v4_confidence=v4_confidence,
        active=use_v5,
    )
    if use_v5:
        v4_fields = {
            **v4_fields,
            "technical_engine_version": v5_score.engine_version,
            "data_quality_score": _to_decimal(v5_score.data_quality_score),
            "stage": v5_score.setup_quality.stage,
            "market_regime": v5_score.debug["composite"]["regime"],
            "feature_flags_json": list(v5_score.feature_flags),
            "warning_flags_json": list(v5_score.warning_flags),
        }
    return TechnicalScore(
        run_id=run_id,
        ticker=base_score.ticker.upper(),
        trend_score=_to_decimal(base_score.trend_score),
        local_trend_score=_to_decimal(base_score.local_trend_score),
        momentum_score=_to_decimal(base_score.momentum_score),
        setup_score=_to_decimal(base_score.setup_score),
        risk_score=_to_decimal(base_score.risk_score),
        market_score=_to_decimal(base_score.market_score),
        relative_strength_score=_to_decimal(base_score.relative_strength_score),
        sector_relative_strength_score=_to_decimal(base_score.sector_relative_strength_score),
        combined_relative_strength_score=_to_decimal(base_score.combined_relative_strength_score),
        htf_score=_to_decimal(base_score.htf_score),
        dual_score=_to_decimal(dual_score),
        classification=classification,
        pullback_health=base_score.pullback_health,
        action_bias=action_bias,
        suggested_stop=_to_decimal(base_score.suggested_stop),
        suggested_target=_to_decimal(base_score.suggested_target),
        reward_risk=_to_decimal(base_score.reward_risk),
        entry_risk_pct=_to_decimal(base_score.entry_risk_pct),
        technical_confidence=confidence,
        **v4_fields,
        **v5_fields,
        insufficient_data=base_score.insufficient_data,
        missing_data_json=base_score.missing_data,
        debug_json=debug,
    )


def _score_ticker(
    db: Session,
    ticker: str,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any] | None = None,
    v4_params: dict[str, Any] | None = None,
) -> PineReplicaScore:
    v4_params = v4_params or load_technical_scoring_v4_config()
    price, trades = load_preferred_ohlcv_frames(db, ticker)
    if price.empty:
        raise TechnicalScoringError(
            f"No cached OHLCV bars for {ticker.upper()}. Fetch IB data first."
        )

    features = calculate_technical_features(price, trades, ticker=ticker)
    htf_features = calculate_htf_trend_features(price) if not price.empty else {}
    relative_strength_features = _relative_strength_features(
        price,
        benchmark_price,
        sector_price,
        v4_params.get("relative_leadership", {}),
    )

    return score_from_feature_result(
        features,
        htf_features=htf_features,
        relative_strength_features=relative_strength_features,
        market_features=market_features,
        qqq_market_features=qqq_market_features,
        v4_params=v4_params,
    )


def _relative_strength_features(
    price: pd.DataFrame,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None = None,
    relative_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if price.empty or benchmark_price.empty:
        return {}
    features = calculate_relative_strength_features(price, benchmark_price, sector_price)
    relative_params = relative_params or {}
    if relative_params.get("beta_adjusted_rs", False):
        features.update(calculate_beta_adjusted_rs(price, benchmark_price, relative_params))
    return features


def _sector_benchmark_price(
    db: Session,
    pine_params: dict[str, Any],
) -> pd.DataFrame | None:
    market_rs = pine_params.get("market_rs", {})
    if not market_rs.get("useSectorBenchmark", False):
        return None

    sector_symbol = str(market_rs.get("sectorSymbol") or "").strip().upper()
    if not sector_symbol:
        return None
    return _load_price_frame(db, sector_symbol)


def _technical_v5_run_context(
    db: Session,
    run_id: int,
    symbols: list[str],
    v5_params: dict[str, Any],
) -> TechnicalV5RunContext:
    sector_result = db.execute(
        select(RawCompanyRow.ticker, RawCompanyRow.sector).where(RawCompanyRow.run_id == run_id)
    )
    sector_rows = sector_result.all() if hasattr(sector_result, "all") else []
    sectors: dict[str, str | None] = {symbol.upper(): None for symbol in symbols}
    for ticker, sector in sector_rows:
        normalized = str(ticker).strip().upper()
        if normalized in sectors and (sectors[normalized] is None or str(sector or "").strip()):
            sectors[normalized] = sector
    resolutions = resolutions_for_tickers(sectors, v5_params["sector_benchmarks"])
    sector_features: dict[str, dict[str, float | None]] = {}
    for symbol in sorted(
        {
            resolution.benchmark_symbol
            for resolution in resolutions.values()
            if resolution.status == "RESOLVED" and resolution.benchmark_symbol
        }
    ):
        frame = _load_price_frame(db, symbol)
        features = _benchmark_roc_features(frame)
        if features:
            sector_features[symbol] = features
    resolutions = {
        ticker: (
            mark_benchmark_data_missing(resolution)
            if resolution.status == "RESOLVED"
            and resolution.benchmark_symbol not in sector_features
            else resolution
        )
        for ticker, resolution in resolutions.items()
    }
    return TechnicalV5RunContext(resolutions=resolutions, sector_features=sector_features)


def _benchmark_roc_features(frame: pd.DataFrame) -> dict[str, float | None]:
    if frame.empty or "close" not in frame:
        return {}
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return {}
    return {
        f"roc{lookback}": (
            round(float((close.iloc[-1] / close.iloc[-lookback - 1] - 1.0) * 100.0), 4)
            if len(close) > lookback
            else None
        )
        for lookback in (21, 63, 126)
    }


def _v5_sector_relative_score(
    score: PineReplicaScore,
    resolution: SectorBenchmarkResolution | None,
    sector_features: dict[str, dict[str, float | None]],
) -> float | None:
    if resolution is None or resolution.status != "RESOLVED" or not resolution.benchmark_symbol:
        return None
    benchmark = sector_features.get(resolution.benchmark_symbol)
    if not benchmark or any(benchmark.get(f"roc{lookback}") is None for lookback in (21, 63, 126)):
        return None
    derived = _dict(score.debug.get("derived"))
    stock = {
        21: _optional_float(derived.get("stock_roc_short")),
        63: _optional_float(derived.get("stock_roc_medium")),
        126: _optional_float(derived.get("stock_roc_long")),
    }
    if any(stock[lookback] is None for lookback in stock):
        return None
    differences = {
        lookback: float(stock[lookback]) - float(benchmark[f"roc{lookback}"]) for lookback in stock
    }
    return relative_strength_score(
        rs_above_sma=sum(differences.values()) > 0,
        rs_roc_short=differences[21],
        rs_roc_medium=differences[63],
        rs_roc_long=differences[126],
        beats_short=differences[21] > 0,
        beats_medium=differences[63] > 0,
        beats_long=differences[126] > 0,
        rs_new_high_value=False,
    )


def _with_v5_sector_debug(score: PineReplicaScore, sector_score: float | None) -> PineReplicaScore:
    debug = {**(score.debug or {})}
    debug["derived"] = {**_dict(debug.get("derived")), "v5_sector_rs_score": sector_score}
    return replace(score, debug=debug)


def _market_features(price: pd.DataFrame, ticker: str) -> dict[str, Any]:
    if price.empty:
        return {}
    return calculate_technical_features(price, ticker=ticker).latest


def _market_frames_signature(
    spy_price: pd.DataFrame,
    qqq_price: pd.DataFrame,
    *,
    sector_ticker: str = "",
    sector_price: pd.DataFrame | None = None,
) -> str:
    digest = hashlib.sha256()
    frames = [("SPY", spy_price), ("QQQ", qqq_price)]
    if sector_ticker:
        frames.append(
            (
                f"SECTOR:{sector_ticker}",
                sector_price if sector_price is not None else pd.DataFrame(),
            )
        )
    for symbol, frame in frames:
        digest.update(symbol.encode("utf-8"))
        if frame.empty:
            digest.update(b"empty")
            continue
        columns = [
            column
            for column in ("date", "open", "high", "low", "close", "volume")
            if column in frame.columns
        ]
        digest.update("|".join(columns).encode("utf-8"))
        digest.update(pd.util.hash_pandas_object(frame[columns], index=False).values.tobytes())
    return digest.hexdigest()


def _load_price_frame(db: Session, ticker: str) -> pd.DataFrame:
    price, _ = load_preferred_ohlcv_frames(db, ticker)
    return price


def _optional_market_features(
    db: Session,
    ticker: str,
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    market_regime_params = v4_params.get("market_regime_v4", {})
    if ticker.upper() == "QQQ" and not market_regime_params.get("use_qqq", True):
        return {}
    price = _load_price_frame(db, ticker)
    return _market_features(price, ticker)


def _leadership_rank_input(score: PineReplicaScore) -> dict[str, Any]:
    derived = score.debug.get("derived", {}) if score.debug else {}
    return {
        "ticker": score.ticker,
        "roc21": derived.get("stock_roc_short"),
        "roc63": derived.get("stock_roc_medium"),
        "roc126": derived.get("stock_roc_long"),
        "benchmark_rs_score": score.relative_strength_score,
        "dual_score": score.dual_score,
        "setup_score": score.setup_score,
    }


def _leadership_v5_rank_input(
    score: PineReplicaScore,
    resolution: SectorBenchmarkResolution | None,
    sector_features: dict[str, dict[str, float | None]],
    sector_config: dict[str, Any],
) -> dict[str, Any]:
    derived = _dict(score.debug.get("derived"))
    sector_score = _v5_sector_relative_score(score, resolution, sector_features)
    benchmark_rs = float(score.relative_strength_score)
    if sector_score is not None:
        mix = sector_config["benchmark_rs_mix"]
        benchmark_rs = round(
            benchmark_rs * float(mix["broad_market"]) + sector_score * float(mix["sector"]),
            4,
        )
    return {
        "ticker": score.ticker,
        "roc21": _optional_float(derived.get("stock_roc_short")),
        "roc63": _optional_float(derived.get("stock_roc_medium")),
        "roc126": _optional_float(derived.get("stock_roc_long")),
        "benchmark_rs_score": benchmark_rs,
        "residual_momentum_score": _optional_float(derived.get("residual_momentum_score")),
    }


def _with_leadership_debug(
    score: PineReplicaScore,
    leadership: dict[str, Any],
) -> PineReplicaScore:
    leadership_result = leadership.get(score.ticker.upper())
    if leadership_result is None:
        return score
    debug = {
        **(score.debug or {}),
        "leadership": asdict(leadership_result),
    }
    debug = add_leadership_to_explainability(debug, leadership_result)
    return replace(score, debug=debug)


def _tickers_for_run(db: Session, run_id: int) -> list[str]:
    return list(
        db.scalars(
            select(RawCompanyRow.ticker)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )


def _normalize_tickers(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for ticker in tickers:
        symbol = ticker.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    return normalized


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 4)))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _base_score(score: PineReplicaScore | TechnicalScoreV4) -> PineReplicaScore:
    return score.base_score if isinstance(score, TechnicalScoreV4) else score


def _v4_persistence_fields(debug: dict[str, Any] | None) -> dict[str, Any]:
    explainability = _dict((debug or {}).get("explainability"))
    adaptive = _dict(explainability.get("adaptive"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    stage = _dict(explainability.get("stage"))
    regime = _dict(explainability.get("regime"))
    leadership = _dict(explainability.get("leadership"))
    climax = _dict(explainability.get("climax"))
    data_readiness = _dict(explainability.get("data_readiness"))

    return {
        "technical_engine_version": explainability.get("engine_version"),
        "data_quality_score": _to_decimal(data_readiness.get("data_quality_score")),
        "stage": stage.get("stage"),
        "market_regime": regime.get("regime"),
        "leadership_score": _to_decimal(leadership.get("leadership_score")),
        "vcp_score": _to_decimal(contraction.get("vcp_score")),
        "box_tightness_score": _to_decimal(box.get("box_tightness_score")),
        "breakout_quality_score": _to_decimal(box.get("breakout_quality_score")),
        "climax_risk_score": _to_decimal(climax.get("climax_risk_score")),
        "atr_percentile_252": _to_decimal(adaptive.get("atr_percentile_252")),
        "volume_percentile_252": _to_decimal(adaptive.get("volume_percentile_252")),
        "range_percentile_252": _to_decimal(adaptive.get("range_percentile_252")),
        "extension_percentile_252": _to_decimal(adaptive.get("extension_percentile_252")),
        "feature_flags_json": _list_or_none(explainability.get("feature_flags")),
        "warning_flags_json": _list_or_none(explainability.get("warning_flags")),
        "sub_tags_json": _list_or_none(explainability.get("sub_tags")),
        "v4_debug_json": explainability or None,
    }


def _v5_persistence_fields(
    score: TechnicalScoreV5 | None,
    *,
    v4_score: float,
    v4_classification: str,
    v4_confidence: str,
    active: bool,
) -> dict[str, Any]:
    if score is None:
        return {
            "technical_strength_score": None,
            "setup_quality_score": None,
            "entry_quality_score": None,
            "technical_composite_score": None,
            "confidence_adjusted_score": None,
            "leadership_v5_score": None,
            "residual_momentum_score": None,
            "trigger_distance_atr": None,
            "stop_distance_atr": None,
            "stage_modifier": None,
            "setup_type": None,
            "sector_benchmark_symbol": None,
            "v5_debug_json": None,
        }
    debug = {
        **score.debug,
        "rollout": {"mode": "active" if active else "shadow", "dual_score_mirrors_tcs": active},
        "shadow_comparison": {
            "ticker": score.ticker,
            "v4_score": round(float(v4_score), 4),
            "v5_technical_strength": score.technical_strength_score,
            "v5_setup_quality": score.setup_quality_score,
            "v5_entry_quality": score.entry_quality_score,
            "v5_composite": score.technical_composite_score,
            "v4_classification": v4_classification,
            "v5_classification": score.classification,
            "score_delta": round(score.technical_composite_score - float(v4_score), 4),
            "danger_difference": {
                "v4": v4_classification
                if v4_classification
                in {
                    "Failed breakout",
                    "Climax reversal risk",
                    "Blowoff top",
                    "Distribution risk",
                    "Late-stage extension",
                }
                else None,
                "v5": score.entry_quality.danger_state,
            },
            "confidence_difference": {"v4": v4_confidence, "v5": score.technical_confidence},
        },
    }
    return {
        "technical_strength_score": _to_decimal(score.technical_strength_score),
        "setup_quality_score": _to_decimal(score.setup_quality_score),
        "entry_quality_score": _to_decimal(score.entry_quality_score),
        "technical_composite_score": _to_decimal(score.technical_composite_score),
        "confidence_adjusted_score": _to_decimal(score.confidence_adjusted_score),
        "leadership_v5_score": _to_decimal(score.leadership_v5_score),
        "residual_momentum_score": _to_decimal(score.residual_momentum_score),
        "trigger_distance_atr": _to_decimal(score.trigger_distance_atr),
        "stop_distance_atr": _to_decimal(score.stop_distance_atr),
        "stage_modifier": _to_decimal(score.stage_modifier),
        "setup_type": score.setup_type,
        "sector_benchmark_symbol": score.sector_benchmark_symbol,
        "v5_debug_json": debug,
    }


def _v4_error_payload(reason: str, v4_params: dict[str, Any]) -> dict[str, Any]:
    engine = _dict(v4_params.get("engine"))
    engine_version = str(engine.get("version") or "4.0.0")
    return {
        "engine_version": engine_version,
        "data_readiness": {
            "confidence": "error",
            "data_quality_score": 0.0,
            "missing_reasons": ["technical_error"],
        },
        "adaptive": {},
        "contraction": {},
        "box": {},
        "stage": {"stage": "Unknown"},
        "regime": {"regime": "Unknown"},
        "leadership": None,
        "climax": {},
        "feature_flags": [],
        "warning_flags": ["technical_error"],
        "sub_tags": [],
        "final_v4_score": None,
        "final_v4_classification": "No trade",
        "final_v4_action": "No data",
        "error": {"reason": reason},
        "debug": {
            "score_source": "technical_error",
            "error": reason,
        },
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_none(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None
