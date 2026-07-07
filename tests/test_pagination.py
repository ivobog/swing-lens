from types import SimpleNamespace

from sqlalchemy import literal, select

from app.services.pagination import paginate_query


def test_paginate_query_clamps_page_size_and_returns_page_metadata() -> None:
    db = FakeDb(total=55, rows=[SimpleNamespace(id=1), SimpleNamespace(id=2)])

    page = paginate_query(db, select(literal(1)), page=99, page_size=500, max_page_size=25)

    assert page.items == db.rows
    assert page.page == 3
    assert page.page_size == 25
    assert page.total_items == 55
    assert page.total_pages == 3
    assert page.has_previous is True
    assert page.has_next is False


class FakeExecuteResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, total: int, rows) -> None:
        self.total = total
        self.rows = rows
        self.executed = []

    def scalar(self, _statement):
        return self.total

    def execute(self, statement):
        self.executed.append(statement)
        return FakeExecuteResult(self.rows)
