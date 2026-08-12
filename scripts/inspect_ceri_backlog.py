from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.db import SessionLocal
from app.services.ceri.backlog_cleanup_service import (
    CLEANUP_CONFIRMATION,
    apply_legacy_ceri_backlog_cleanup,
    inspect_legacy_ceri_backlog,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect legacy CERI backlog; changes require explicit apply confirmation."
    )
    parser.add_argument("--superseded-run-id", type=int, action="append", default=[])
    parser.add_argument("--preserve-run-id", type=int, action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason")
    parser.add_argument("--confirm")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.apply:
            result = apply_legacy_ceri_backlog_cleanup(
                db,
                superseded_run_ids=tuple(args.superseded_run_id),
                preserved_run_ids=tuple(args.preserve_run_id),
                reason=args.reason or "",
                confirmation=args.confirm or "",
            )
            db.commit()
            payload: dict[str, Any] = result.as_dict()
            payload["dry_run"] = False
        else:
            report = inspect_legacy_ceri_backlog(
                db,
                superseded_run_ids=tuple(args.superseded_run_id),
                preserved_run_ids=tuple(args.preserve_run_id),
            )
            payload = report.as_dict()
            payload["apply_instructions"] = {
                "required_confirmation": CLEANUP_CONFIRMATION,
                "requires_reason": True,
            }

    rendered = json.dumps(payload, default=str, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
