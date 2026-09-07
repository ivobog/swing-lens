from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.services.alembic_heads import database_alembic_heads


@pytest.mark.parametrize(
    ("heads", "expected"),
    [
        (("head-a",), ("head-a",)),
        (("head-b", "head-a"), ("head-a", "head-b")),
        (("head-a", "extra"), ("extra", "head-a")),
        ((), ()),
    ],
)
def test_database_heads_are_complete_sorted_sets(heads, expected) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table alembic_version (version_num text not null)"))
        for head in heads:
            connection.execute(
                text("insert into alembic_version values (:head)"), {"head": head}
            )
        assert database_alembic_heads(connection) == expected


def test_missing_alembic_version_is_empty_head_set() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        assert database_alembic_heads(connection) == ()
