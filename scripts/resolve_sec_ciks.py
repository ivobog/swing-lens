from __future__ import annotations

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.models.ceri_tables import CeriCompany
from app.models.tables import RawCompanyRow
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient
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
            rows = list(
                db.scalars(select(CeriCompany).where(CeriCompany.ticker == ticker)).all()
            )
            known = sorted({str(row.cik).zfill(10) for row in rows if row.cik})
            if len(known) > 1:
                conflicts[ticker] = known
                continue
            if known:
                already_mapped[ticker] = known[0]
                continue
            cik = provider.resolve_cik(ticker)
            if cik is None:
                unresolved.append(ticker)
                continue
            normalized = str(cik).zfill(10)
            resolved[ticker] = normalized
            if args.dry_run:
                continue
            if rows:
                for row in rows:
                    row.cik = normalized
            else:
                db.add(
                    CeriCompany(
                        ticker=ticker,
                        exchange="US",
                        cik=normalized,
                        sec_applicability="REQUIRED",
                    )
                )
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
