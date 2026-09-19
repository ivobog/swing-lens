from __future__ import annotations

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.models.tables import RawCompanyRow
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient
from app.services.ceri.sec.identity_repair import resolve_and_persist_sec_identity
from app.services.ceri.sec.provider import SecCeriProvider
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve and persist SEC CIKs for a run universe.")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    provider = SecCeriProvider(
        client=SecEdgarClient(
            SecClientConfig(
                user_agent=settings.sec_user_agent,
                requests_per_second=settings.sec_requests_per_second,
                timeout_seconds=settings.sec_http_timeout_seconds,
            )
        )
    )
    resolved: dict[str, str] = {}
    already_mapped: dict[str, str] = {}
    unresolved: list[str] = []
    conflicts: dict[str, list[str]] = {}
    with Session(engine) as db:
        tickers = tuple(
            sorted(
                {
                    str(value).strip().upper()
                    for value in db.scalars(
                        select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == args.run_id)
                    )
                    if value
                }
            )
        )
        for ticker in tickers:
            result = resolve_and_persist_sec_identity(db, provider=provider, ticker=ticker)
            if result.status == "AMBIGUOUS":
                conflicts[ticker] = [result.reason or "conflicting exact SEC identity"]
            elif result.status == "ALREADY_RESOLVED":
                already_mapped[ticker] = result.cik
            elif result.status == "RESOLVED":
                resolved[ticker] = result.cik
            else:
                unresolved.append(ticker)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    report = {
        "run_id": args.run_id,
        "dry_run": args.dry_run,
        "already_mapped": already_mapped,
        "resolved": resolved,
        "unresolved": unresolved,
        "conflicts": conflicts,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if unresolved or conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
