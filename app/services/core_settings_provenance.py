"""Trace three core and five contextual boolean winners while Pydantic resolves.

Source dictionaries are returned unchanged; only field names/source kinds enter
the context. No credentials or other Settings values are retained here.
"""

from contextvars import ContextVar

from pydantic_settings import PydanticBaseSettingsSource

CORE_SETTING_KEYS = (
    "technical_v5_enabled",
    "technical_v5_shadow_compare_enabled",
    "technical_v5_persist_shadow_results",
)
CONTEXTUAL_SETTING_KEYS = (
    "ceri_enabled",
    "ceri_run_capture_enabled",
    "ib_market_intelligence_enabled",
    "ib_volatility_intelligence_enabled",
    "ib_short_pressure_enabled",
)
CORE_SETTINGS_TRACE: ContextVar[dict[str, str] | None] = ContextVar(
    "core_settings_trace", default=None
)


class TracedCoreSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, source: PydanticBaseSettingsSource, kind: str):
        super().__init__(source.settings_cls)
        self.source = source
        self.kind = kind
        self.__name__ = type(source).__name__

    def get_field_value(self, field, field_name):
        return self.source.get_field_value(field, field_name)

    def __call__(self):
        self.source._set_current_state(self.current_state)
        self.source._set_settings_sources_data(self.settings_sources_data)
        values = self.source()
        trace = CORE_SETTINGS_TRACE.get()
        if trace is not None:
            for key in (*CORE_SETTING_KEYS, *CONTEXTUAL_SETTING_KEYS):
                if key in values:
                    trace.setdefault(key, self.kind)
        return values
