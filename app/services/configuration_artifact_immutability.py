"""Seal configuration proof while allowing native operational metadata updates."""

from sqlalchemy import inspect, select


def seal_configuration_member(model, field, *, members=()):
    from sqlalchemy import event

    def protect(_mapper, connection, target):
        state = inspect(target)
        if _mapper is not None and not state.attrs[field].history.has_changes():
            return
        stored = connection.execute(
            select(getattr(model.__table__.c, field)).where(model.__table__.c.id == target.id)
        ).scalar_one()
        before = (stored or {}).get("effective_configuration_at_creation")
        after = (getattr(target, field) or {}).get("effective_configuration_at_creation")
        changed_member = before is not None and any(
            (stored or {}).get(member) != (getattr(target, field) or {}).get(member)
            for member in members
        )
        if before != after or changed_member or (before is not None and _mapper is None):
            raise ValueError("IMMUTABLE_CONFIGURATION_PROOF_MUTATION_REJECTED")

    def delete(_mapper, connection, target):
        protect(None, connection, target)

    event.listen(model, "before_update", protect)
    event.listen(model, "before_delete", delete)
