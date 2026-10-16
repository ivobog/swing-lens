"""Reliable autocommit PostgreSQL observer used by focused certification."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text


class PostgresTransactionObserver:
    def __init__(self, database_url: str, output: Path, *, application_name: str) -> None:
        self.output = output
        self.application_name = application_name
        self.sequence = 0
        self._lock = threading.Lock()
        self.engine = create_engine(
            database_url,
            isolation_level="AUTOCOMMIT",
            connect_args={"application_name": application_name},
        )

    def sample(self) -> dict[str, object]:
        with self.engine.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        "select pid, application_name, xact_start, "
                        "extract(epoch from (clock_timestamp()-xact_start)) as age_seconds, "
                        "state, wait_event_type, wait_event, left(query,800) as query "
                        "from pg_stat_activity where datname=current_database() "
                        "and pid <> pg_backend_pid() and xact_start is not null "
                        "and application_name <> :observer order by pid"
                    ),
                    {"observer": self.application_name},
                ).mappings()
            ]
            observer_state = dict(
                connection.execute(
                    text(
                        "select pid, xact_start, state from pg_stat_activity "
                        "where pid=pg_backend_pid()"
                    )
                )
                .mappings()
                .one()
            )
        for row in rows:
            row["age_seconds"] = float(row["age_seconds"] or 0)
        self.sequence += 1
        record = {
            "sequence": self.sequence,
            "observed_at": datetime.now(UTC).isoformat(),
            "transactions": rows,
            "open_transaction_count": len(rows),
            "idle_transaction_count": sum(row["state"] == "idle in transaction" for row in rows),
            "observer": observer_state,
            "gap_from_previous": 0,
        }
        self._append(record)
        return record

    def _append(self, record: dict[str, object]) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def close(self) -> None:
        self.engine.dispose()
