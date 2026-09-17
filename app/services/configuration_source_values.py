"""Ephemeral native resolver results with non-value provenance.

Metadata is outside the dict/dataclass value tree and is consumed only when the
shared effective snapshot is frozen. It never changes existing scoring/debug.
"""

from typing import Any

from app.services.effective_configuration import ConfigurationSource, ConfigurationSourceKind


def configuration_leaves(values: dict[str, Any], prefix: str = ""):
    for key, value in values.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            yield from configuration_leaves(value, path)
        else:
            yield path, value


def winning_sources(
    values: dict[str, Any], raw: dict[str, Any], identifier: str | None, default_identifier: str
) -> tuple[tuple[str, ConfigurationSource], ...]:
    provided = dict(configuration_leaves(raw))
    return tuple(
        (
            key,
            ConfigurationSource(
                ConfigurationSourceKind.PROFILE
                if key in provided
                else ConfigurationSourceKind.CODE_DEFAULT,
                identifier if key in provided else default_identifier,
            ),
        )
        for key, _ in configuration_leaves(values)
    )


class SourcedConfigurationValues(dict):
    def __init__(
        self, values: dict[str, Any], sources: tuple[tuple[str, ConfigurationSource], ...]
    ):
        super().__init__(values)
        self.configuration_sources = sources
