"""Financial read boundaries for the SEC preparation projection."""

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.services.ceri.enums import CeriSourceProjection

PREPARATION_SOURCE = CeriSourceProjection.SEC_GUIDANCE_PREPARATION.value
PREPARATION_REQUEST_PREFIX = "sec-cache-preparation:"


def is_preparation_source(source) -> bool:
    return getattr(source, "dataset", None) == PREPARATION_SOURCE


def is_preparation_run(run) -> bool:
    return (getattr(run, "request_key", None) or "").startswith(PREPARATION_REQUEST_PREFIX)


def install_preparation_visibility(source_model, run_model) -> None:
    """Hide the private projection in ordinary ORM reads, including aliases.

    Receipt verification and preparation storage use explicit Core reads of the
    private projection. Financial consumers cannot opt out through query options.
    """

    @event.listens_for(Session, "do_orm_execute")
    def exclude_preparation_rows(state):
        if state.is_select and state.is_orm_statement and not state.is_relationship_load:
            state.statement = state.statement.options(
                with_loader_criteria(
                    source_model, lambda row: row.dataset != PREPARATION_SOURCE,
                    include_aliases=True,
                ),
                with_loader_criteria(
                    run_model, lambda row: ~row.request_key.startswith(PREPARATION_REQUEST_PREFIX),
                    include_aliases=True,
                ),
            )
