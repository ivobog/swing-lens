"""Explicit local D2 inspection/capture. Never migrates, acquires data or creates jobs."""

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.market_sector_intelligence_capture_authority import MarketSectorCaptureAuthority
from app.services.market_sector_intelligence_config import MarketSectorIntelligenceConfigError
from app.services.market_sector_intelligence_rotation_repository import (
    MarketSectorRotationRepository,
)
from app.services.market_sector_intelligence_rotation_service import MarketSectorRotationService
from app.services.runtime_mutation_authority import RuntimeMutationAuthorityError


def run_command(*, capture, settings, session_factory, service=None, repository=None):
    # Resolve configured role/capability before any capture price/database work.
    authority = MarketSectorCaptureAuthority.from_settings(settings) if capture else None
    with session_factory() as db:
        preview = (service or MarketSectorRotationService()).preview(db)
    if not capture:
        return {"status": "DRY_RUN", "preview": preview}
    with session_factory() as db:
        return (repository or MarketSectorRotationRepository()).capture(
            db, preview, authority=authority
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Inspect cached prices without writing"
    )
    mode.add_argument(
        "--capture", action="store_true", help="Capture only a complete current cohort"
    )
    args = parser.parse_args(argv)
    from app.db import SessionLocal
    from app.settings import get_settings

    try:
        result = run_command(
            capture=args.capture, settings=get_settings(), session_factory=SessionLocal
        )
    except MarketSectorIntelligenceConfigError:
        result = {"status": "MARKET_SECTOR_CONFIGURATION_INVALID"}
    except RuntimeMutationAuthorityError as exc:
        result = {"status": exc.code}
    except Exception:
        result = {"status": "MARKET_SECTOR_CAPTURE_UNAVAILABLE"}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["status"] in {"DRY_RUN", "CREATED", "ALREADY_EXISTS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
