from __future__ import annotations

from enum import StrEnum


class LifecycleState(StrEnum):
    DISCOVERED = "DISCOVERED"
    DEVELOPING = "DEVELOPING"
    TIGHTENING = "TIGHTENING"
    READY = "READY"
    TRIGGERED = "TRIGGERED"
    CONFIRMED = "CONFIRMED"
    EXTENDED = "EXTENDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class SetupFamily(StrEnum):
    BREAKOUT = "BREAKOUT"
    PULLBACK = "PULLBACK"
    VCP = "VCP"
    CONTINUATION = "CONTINUATION"
    GENERIC = "GENERIC"


class Actionability(StrEnum):
    ACTIONABLE = "ACTIONABLE"
    WATCH_ONLY = "WATCH_ONLY"
    BLOCKED = "BLOCKED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class EventSeverity(StrEnum):
    INFO = "INFO"
    NOTABLE = "NOTABLE"
    ACTIONABLE = "ACTIONABLE"
    RISK = "RISK"


class ConfidenceLabel(StrEnum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class DataQualityLabel(StrEnum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class SnapshotOrigin(StrEnum):
    LIVE_RUN = "LIVE_RUN"
    REPLAY = "REPLAY"
    RECONSTRUCTED = "RECONSTRUCTED"


class EpisodeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class EvaluationMode(StrEnum):
    LIVE = "LIVE"
    DRY_RUN = "DRY_RUN"
    REPLAY = "REPLAY"
    REPAIR = "REPAIR"


class EvaluationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SignalValueType(StrEnum):
    FLOAT = "float"
    PERCENTAGE = "percentage"
    INTEGER_RANK = "integer_rank"
    BOOLEAN = "boolean"
    ENUM = "enum"
    SET = "set"
    DATE = "date"
    NULLABILITY = "nullability"


class SignalCategory(StrEnum):
    SETUP = "SETUP"
    SCORE = "SCORE"
    TREND = "TREND"
    VOLATILITY_VOLUME = "VOLATILITY_VOLUME"
    LEADERSHIP = "LEADERSHIP"
    MARKET = "MARKET"
    RISK = "RISK"
    DATA_QUALITY = "DATA_QUALITY"


class AlertStatus(StrEnum):
    UNREAD = "UNREAD"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DISMISSED = "DISMISSED"
