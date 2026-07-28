from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.services.market_regime import MarketRegimeResult, classify_market_regime
from app.services.market_regime_policy import (
    MarketDataFreshness,
    MarketRegimeCommandCenterConfig,
    MarketRegimePolicyDto,
    MarketRegimePolicyService,
    load_market_regime_command_center_config,
)
from app.services.market_regime_repository import (
    MarketRegimeRepository,
    MarketRegimeSnapshotWrite,
)
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.technical_indicators import calculate_technical_features


@dataclass(frozen=True)
class IndexHealthDto:
    symbol: str
    latest_close: float | None
    as_of_date: date | None
    above_sma50: bool | None
    above_sma200: bool | None
    sma50_above_sma200: bool | None
    sma50_slope_pct: float | None
    roc21: float | None
    roc63: float | None
    distribution_count: float | None
    donchian_20_breakout: bool | None
    stale: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketParticipationDto:
    ticker_count: int = 0
    technical_count: int = 0
    average_technical_score: float | None = None
    clean_pullback_count: int = 0
    fresh_breakout_count: int = 0
    vcp_count: int = 0
    danger_count: int = 0
    market_risk_warning_count: int = 0
    above_sma50_pct: float | None = None
    above_sma200_pct: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SectorLeadershipRow:
    sector: str
    ticker_count: int
    average_technical_score: float | None
    average_fundamental_score: float | None
    top_25_count: int
    clean_pullback_count: int
    breakout_count: int
    vcp_count: int
    danger_count: int
    leadership_score: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketRegimeCommandCenterDto:
    as_of_date: date
    run_id: int | None
    calculation_version: str
    config_version: str | None
    regime: str
    risk_state: str
    score: float
    risk_off: bool
    gate_ok: bool
    confidence: str
    reasons: list[str]
    warnings: list[str]
    action_summary: str
    policy: MarketRegimePolicyDto
    index_health: dict[str, IndexHealthDto]
    universe_participation: MarketParticipationDto | None
    sector_leadership: list[SectorLeadershipRow]
    debug: dict[str, Any]


@dataclass(frozen=True)
class MarketInput:
    symbol: str
    features: dict[str, Any] | None
    as_of_date: date | None
    health: IndexHealthDto
    debug: dict[str, Any]


class MarketRegimeCommandCenterService:
    def __init__(
        self,
        policy_service: MarketRegimePolicyService | None = None,
        repository: MarketRegimeRepository | None = None,
    ) -> None:
        self.policy_service = policy_service or MarketRegimePolicyService()
        self.repository = repository or MarketRegimeRepository()

    def build_snapshot(
        self,
        db: Session,
        run_id: int | None = None,
        today: date | None = None,
        config_path: Path | None = None,
    ) -> MarketRegimeCommandCenterDto:
        today = today or date.today()
        config = load_market_regime_command_center_config(
            config_path
            if config_path is not None
            else Path("config/market_regime_command_center.yaml")
        )
        primary_symbol = str(config.symbols["primary_market"]).strip().upper()
        risk_symbol = str(config.symbols.get("risk_proxy") or "").strip().upper()
        use_risk_proxy = bool(config.symbols.get("use_risk_proxy", True)) and bool(
            risk_symbol
        )

        primary = self._load_market_input(db, primary_symbol, config, today)
        risk_proxy = (
            self._load_market_input(db, risk_symbol, config, today)
            if use_risk_proxy
            else None
        )

        regime_result = classify_market_regime(
            primary.features,
            risk_proxy.features if risk_proxy is not None else None,
            config.market_regime_params,
        )
        freshness = self._freshness_for([primary, risk_proxy], config, today)
        policy = self.policy_service.policy_for(regime_result, config, freshness=freshness)
        index_health = {
            primary.symbol: primary.health,
            **({risk_proxy.symbol: risk_proxy.health} if risk_proxy is not None else {}),
        }
        warnings = self._combined_warnings(policy, index_health)
        as_of_date = self._snapshot_date([primary, risk_proxy], today)
        action_summary = self._action_summary(regime_result, policy)
        input_symbols = {
            "primary_market": primary_symbol,
            "risk_proxy": risk_symbol if use_risk_proxy else None,
            "use_risk_proxy": use_risk_proxy,
        }

        dto = MarketRegimeCommandCenterDto(
            as_of_date=as_of_date,
            run_id=run_id,
            calculation_version=config.calculation_version,
            config_version=config.config_version,
            regime=regime_result.regime,
            risk_state=policy.risk_state,
            score=round(float(regime_result.score), 4),
            risk_off=regime_result.risk_off,
            gate_ok=regime_result.gate_ok,
            confidence=regime_result.confidence,
            reasons=list(regime_result.reasons),
            warnings=warnings,
            action_summary=action_summary,
            policy=policy,
            index_health=index_health,
            universe_participation=None,
            sector_leadership=[],
            debug={
                "input_symbols": input_symbols,
                "market_inputs": {
                    item.symbol: item.debug for item in [primary, risk_proxy] if item is not None
                },
            },
        )

        self.repository.upsert_snapshot(db, self._snapshot_write(dto, input_symbols), run_id)
        return dto

    def _load_market_input(
        self,
        db: Session,
        symbol: str,
        config: MarketRegimeCommandCenterConfig,
        today: date,
    ) -> MarketInput:
        price, trades = load_preferred_ohlcv_frames(db, symbol)
        as_of_date = _frame_as_of_date(price)
        if price.empty:
            return MarketInput(
                symbol=symbol,
                features=None,
                as_of_date=None,
                health=IndexHealthDto(
                    symbol=symbol,
                    latest_close=None,
                    as_of_date=None,
                    above_sma50=None,
                    above_sma200=None,
                    sma50_above_sma200=None,
                    sma50_slope_pct=None,
                    roc21=None,
                    roc63=None,
                    distribution_count=None,
                    donchian_20_breakout=None,
                    stale=True,
                    warnings=[f"missing_{symbol.lower()}_market_data"],
                ),
                debug={"missing": True},
            )

        feature_result = calculate_technical_features(price, trades, ticker=symbol)
        freshness = self._input_freshness(as_of_date, config, today)
        warnings = [f"stale_{symbol.lower()}_market_data"] if freshness.stale else []
        return MarketInput(
            symbol=symbol,
            features=feature_result.latest,
            as_of_date=as_of_date,
            health=self._index_health(
                symbol,
                feature_result.latest,
                as_of_date,
                freshness,
                warnings,
            ),
            debug={
                "missing": False,
                "feature_debug": feature_result.debug,
                "missing_data": feature_result.missing_data,
                "insufficient_data": feature_result.insufficient_data,
            },
        )

    def _index_health(
        self,
        symbol: str,
        features: dict[str, Any],
        as_of_date: date | None,
        freshness: MarketDataFreshness,
        warnings: list[str],
    ) -> IndexHealthDto:
        close = _float_or_none(features.get("close"))
        sma50 = _float_or_none(features.get("sma50"))
        sma200 = _float_or_none(features.get("sma200"))
        return IndexHealthDto(
            symbol=symbol,
            latest_close=close,
            as_of_date=as_of_date,
            above_sma50=_greater_than(close, sma50),
            above_sma200=_greater_than(close, sma200),
            sma50_above_sma200=_greater_than(sma50, sma200),
            sma50_slope_pct=_float_or_none(features.get("sma50_slope_pct")),
            roc21=_float_or_none(features.get("roc21")),
            roc63=_float_or_none(features.get("roc63")),
            distribution_count=_float_or_none(features.get("distribution_count")),
            donchian_20_breakout=_bool_or_none(features.get("donchian_20_breakout")),
            stale=freshness.stale,
            warnings=warnings,
        )

    def _freshness_for(
        self,
        inputs: list[MarketInput | None],
        config: MarketRegimeCommandCenterConfig,
        today: date,
    ) -> MarketDataFreshness:
        freshness_values = [
            self._input_freshness(item.as_of_date, config, today)
            for item in inputs
            if item is not None
        ]
        warnings: list[str] = []
        for item in inputs:
            if item is None:
                continue
            warnings.extend(item.health.warnings)
        return MarketDataFreshness(
            stale=any(item.stale for item in freshness_values),
            severely_stale=any(item.severely_stale for item in freshness_values),
            warnings=_unique_list(warnings),
        )

    def _input_freshness(
        self,
        as_of_date: date | None,
        config: MarketRegimeCommandCenterConfig,
        today: date,
    ) -> MarketDataFreshness:
        if as_of_date is None:
            return MarketDataFreshness(stale=True, severely_stale=True)
        max_stale_days = int(config.freshness.get("max_stale_trading_days", 3))
        age_days = max(0, (today - as_of_date).days)
        return MarketDataFreshness(
            stale=age_days > max_stale_days,
            severely_stale=age_days > max_stale_days * 2,
        )

    def _snapshot_date(self, inputs: list[MarketInput | None], today: date) -> date:
        dates = [item.as_of_date for item in inputs if item is not None and item.as_of_date]
        return max(dates) if dates else today

    def _action_summary(
        self,
        regime_result: MarketRegimeResult,
        policy: MarketRegimePolicyDto,
    ) -> str:
        size_pct = int(round(policy.position_size_multiplier * 100))
        summary = f"{policy.summary} Use {size_pct}% of normal starter size."
        if regime_result.confidence == "low":
            summary = f"{summary} Confidence is low; verify market data before acting."
        return summary

    def _combined_warnings(
        self,
        policy: MarketRegimePolicyDto,
        index_health: dict[str, IndexHealthDto],
    ) -> list[str]:
        warnings = list(policy.warnings)
        for health in index_health.values():
            warnings.extend(health.warnings)
        return _unique_list(warnings)

    def _snapshot_write(
        self,
        dto: MarketRegimeCommandCenterDto,
        input_symbols: dict[str, Any],
    ) -> MarketRegimeSnapshotWrite:
        return MarketRegimeSnapshotWrite(
            as_of_date=dto.as_of_date,
            calculation_version=dto.calculation_version,
            config_version=dto.config_version,
            regime=dto.regime,
            risk_state=dto.risk_state,
            score=dto.score,
            risk_off=dto.risk_off,
            gate_ok=dto.gate_ok,
            confidence=dto.confidence,
            action_summary=dto.action_summary,
            position_size_multiplier=dto.policy.position_size_multiplier,
            preferred_profiles=dto.policy.preferred_profiles,
            allowed_profiles=dto.policy.allowed_profiles,
            reduced_profiles=dto.policy.reduced_profiles,
            blocked_profiles=dto.policy.blocked_profiles,
            allowed_setups=dto.policy.allowed_setups,
            blocked_setups=dto.policy.blocked_setups,
            input_symbols=input_symbols,
            index_health={
                symbol: _json_ready(asdict(health))
                for symbol, health in dto.index_health.items()
            },
            universe_participation=(
                _json_ready(asdict(dto.universe_participation))
                if dto.universe_participation is not None
                else {}
            ),
            sector_leadership=[
                _json_ready(asdict(row)) for row in dto.sector_leadership
            ],
            reasons=dto.reasons,
            warnings=dto.warnings,
            debug=_json_ready(dto.debug),
        )


def _frame_as_of_date(frame: pd.DataFrame) -> date | None:
    if frame.empty or "date" not in frame:
        return None
    value = pd.to_datetime(frame["date"].iloc[-1])
    return value.date()


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _greater_than(left: float | None, right: float | None) -> bool | None:
    if left is None or right is None:
        return None
    return left > right


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _unique_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value
