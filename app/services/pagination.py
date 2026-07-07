from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Page:
    items: list[Any]
    page: int
    page_size: int
    total_items: int
    total_pages: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def paginate_query(
    db: Session,
    statement: Select,
    page: int,
    page_size: int,
    max_page_size: int = 200,
) -> Page:
    page = max(page, 1)
    page_size = max(1, min(page_size, max_page_size))
    total_items = int(
        db.scalar(
            select(func.count()).select_from(
                statement.order_by(None).limit(None).offset(None).subquery()
            )
        )
        or 0
    )
    total_pages = max(1, ceil(total_items / page_size))
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    items = list(db.execute(statement.limit(page_size).offset(offset)).all())
    return Page(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )
