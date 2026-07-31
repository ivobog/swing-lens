from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

FEATURE_SCHEMA_VERSION = "owpe-features-1.0.0"


class FeatureSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureMetadata:
    name: str
    data_type: str
    source_path: str
    availability_rule: str
    missingness_policy: str
    normalization_rule: str
    categorical_vocabulary: tuple[str, ...] = ()
    indexed_core_column: bool = False


class FeatureSchemaRegistry:
    def __init__(self, version: str = FEATURE_SCHEMA_VERSION) -> None:
        if version != FEATURE_SCHEMA_VERSION:
            raise FeatureSchemaError(f"Unsupported OWPE feature schema version: {version}")
        self.version = version
        self._features = _build_v1_features()

    def get(self, name: str) -> FeatureMetadata:
        try:
            return self._features[name]
        except KeyError as exc:
            raise FeatureSchemaError(f"Unknown OWPE feature: {name}") from exc

    def require_feature_names(self, names: list[str] | tuple[str, ...], field_name: str) -> None:
        unknown = sorted({name for name in names if name not in self._features})
        if unknown:
            raise FeatureSchemaError(
                f"{field_name} contains unknown feature(s): {', '.join(unknown)}"
            )

    def list_features(self) -> tuple[FeatureMetadata, ...]:
        return tuple(self._features.values())

    def validate_source_available_at(
        self,
        feature_name: str,
        *,
        source_available_at: datetime | date,
        prediction_cutoff_at: datetime,
    ) -> None:
        self.get(feature_name)
        available_at = _as_datetime(source_available_at, prediction_cutoff_at)
        if available_at > prediction_cutoff_at:
            raise FeatureSchemaError(
                f"{feature_name} source is available after prediction cutoff"
            )


def _build_v1_features() -> dict[str, FeatureMetadata]:
    features = [
        FeatureMetadata(
            name="ticker",
            data_type="categorical",
            source_path="RawCompanyRow.ticker",
            availability_rule="available at upload processing time",
            missingness_policy="required_for_eligible_prediction",
            normalization_rule="uppercase_symbol",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="setup_family",
            data_type="categorical",
            source_path="CombinedResult.setup_label",
            availability_rule="available when combined result is persisted for the run",
            missingness_policy="required_for_eligible_prediction",
            normalization_rule="trimmed_lowercase_bucket",
            categorical_vocabulary=(
                "buyable",
                "watch",
                "danger",
                "insufficient",
                "unknown",
            ),
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="trigger_state",
            data_type="categorical",
            source_path="TechnicalScore.latest_state",
            availability_rule="completed signal session only",
            missingness_policy="nullable_warning",
            normalization_rule="trimmed_lowercase_bucket",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="ranking_profile",
            data_type="categorical",
            source_path="RankingResult.profile_name",
            availability_rule="available after ranking profile calculation",
            missingness_policy="required_for_eligible_prediction",
            normalization_rule="trimmed_profile_name",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="fundamental_score",
            data_type="numeric",
            source_path="FundamentalScore.total_score",
            availability_rule="available after fundamental scoring for the run",
            missingness_policy="nullable_warning",
            normalization_rule="bounded_0_10",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="technical_score",
            data_type="numeric",
            source_path="TechnicalScore.total_score",
            availability_rule="completed signal session only",
            missingness_policy="required_for_eligible_prediction",
            normalization_rule="bounded_0_10",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="combined_score",
            data_type="numeric",
            source_path="CombinedResult.combined_score",
            availability_rule="available after combined result refresh",
            missingness_policy="required_for_eligible_prediction",
            normalization_rule="bounded_0_10",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="dual_score_band",
            data_type="categorical",
            source_path="derived.fundamental_score+technical_score",
            availability_rule="derived from point-in-time scores",
            missingness_policy="required_when_cohort_dimension_selected",
            normalization_rule="configured_score_band",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="score_band",
            data_type="categorical",
            source_path="derived.combined_score",
            availability_rule="derived from point-in-time combined score",
            missingness_policy="required_when_cohort_dimension_selected",
            normalization_rule="configured_score_band",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="market_regime",
            data_type="categorical",
            source_path="MarketRegimeSnapshot.regime",
            availability_rule="available after market regime snapshot",
            missingness_policy="nullable_warning",
            normalization_rule="canonical_market_regime",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="market_regime_family",
            data_type="categorical",
            source_path="derived.MarketRegimeSnapshot.regime",
            availability_rule="derived from point-in-time market regime snapshot",
            missingness_policy="nullable_warning",
            normalization_rule="configured_regime_family",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="market_risk_state",
            data_type="categorical",
            source_path="MarketRegimeSnapshot.risk_state",
            availability_rule="available after market regime snapshot",
            missingness_policy="nullable_warning",
            normalization_rule="Green/Yellow/Orange/Red/Gray",
            categorical_vocabulary=("Green", "Yellow", "Orange", "Red", "Gray"),
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="sector_state",
            data_type="categorical",
            source_path="SectorRotationSnapshot.rows.state",
            availability_rule="available after sector rotation snapshot",
            missingness_policy="nullable_warning",
            normalization_rule="canonical_sector_rotation_state",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="sector_rank",
            data_type="numeric",
            source_path="SectorRotationSnapshot.rows.rank",
            availability_rule="available after sector rotation snapshot",
            missingness_policy="nullable_warning",
            normalization_rule="positive_integer_rank",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="sector_leadership_bucket",
            data_type="categorical",
            source_path="derived.SectorRotationSnapshot.rows.rank",
            availability_rule="derived from point-in-time sector rotation snapshot",
            missingness_policy="nullable_warning",
            normalization_rule="configured_leadership_bucket",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="reward_risk",
            data_type="numeric",
            source_path="CombinedResult.reward_risk",
            availability_rule="available after combined result refresh",
            missingness_policy="nullable_warning",
            normalization_rule="positive_float",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="earnings_risk",
            data_type="categorical",
            source_path="CombinedResult.earnings_risk",
            availability_rule="available after earnings risk gate calculation",
            missingness_policy="nullable_unknown",
            normalization_rule="canonical_earnings_risk",
            categorical_vocabulary=("none", "low", "medium", "high", "unknown"),
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="technical_data_quality",
            data_type="categorical",
            source_path="TechnicalScore.confidence",
            availability_rule="completed signal session only",
            missingness_policy="required_for_eligible_prediction",
            normalization_rule="canonical_data_quality",
            categorical_vocabulary=("ok", "warning", "insufficient", "unknown"),
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="fundamental_coverage",
            data_type="numeric",
            source_path="FundamentalScore.coverage",
            availability_rule="available after fundamental scoring for the run",
            missingness_policy="nullable_warning",
            normalization_rule="bounded_0_1",
            indexed_core_column=True,
        ),
        FeatureMetadata(
            name="universe_provenance",
            data_type="categorical",
            source_path="UploadRun.filename",
            availability_rule="available at upload processing time",
            missingness_policy="required_for_eligible_prediction",
            normalization_rule="stable_source_identifier",
            indexed_core_column=False,
        ),
        FeatureMetadata(
            name="screener_provenance",
            data_type="categorical",
            source_path="UploadRun.notes",
            availability_rule="available at upload processing time",
            missingness_policy="nullable_unknown",
            normalization_rule="stable_source_identifier",
            indexed_core_column=False,
        ),
    ]
    return {feature.name: feature for feature in features}


def _as_datetime(value: datetime | date, prediction_cutoff_at: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    available_at = datetime.combine(value, time(23, 59, 59))
    if available_at.tzinfo is None and prediction_cutoff_at.tzinfo is not None:
        return available_at.replace(tzinfo=prediction_cutoff_at.tzinfo)
    return available_at
