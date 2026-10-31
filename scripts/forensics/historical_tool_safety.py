"""Early, stdlib-only authorization for the retained A10 manual incident utilities.

This module provides no command, database connection or operation dispatcher.
Historical entry points retain their own operations and production safety checks.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import unquote, urlsplit, urlunsplit


def authorize_manual_tool(
    tool: str,
    *,
    operations: dict[str, bool],
    default_operation: str | None = None,
    clone_required: bool = False,
    required_database: str | None = None,
) -> None:
    """Refuse before application imports; True operation values mean consequential work."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--historical-target-url")
    parser.add_argument("--historical-authorize")
    parser.add_argument("--historical-clone")
    parser.add_argument("--allow-normal-database", action="store_true")
    if any(
        os.environ.get(key, "").lower() not in {"", "0", "false"}
        for key in ("CI", "GITHUB_ACTIONS", "TF_BUILD", "GITLAB_CI")
    ):
        parser.error("HISTORICAL_MANUAL_TOOL_FORBIDDEN_IN_CI")
    if os.environ.get("RUNTIME_MODE", "").upper() == "CERTIFICATION":
        parser.error("HISTORICAL_MANUAL_TOOL_FORBIDDEN_IN_CERTIFICATION")
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "Historical manual incident utility. Its output is not current release certification."
        )
        parser.print_help()
        raise SystemExit(0)
    args, remaining = parser.parse_known_args(sys.argv[1:])
    if not args.historical_target_url:
        parser.error("HISTORICAL_EXPLICIT_TARGET_REQUIRED; no configured database fallback")
    try:
        url = urlsplit(args.historical_target_url)
        database = unquote(url.path.removeprefix("/"))
        valid = (
            url.scheme in {"postgresql", "postgresql+psycopg"}
            and url.hostname in {"localhost", "127.0.0.1", "::1"}
            and not url.query
            and not url.fragment
            and bool(url.port or 5432)
        )
    except ValueError:
        valid = False
        database = ""
    if not valid or not (
        database == "swinglens" or re.fullmatch(r"swinglens_pytest_[A-Za-z0-9_]+", database)
    ):
        parser.error("HISTORICAL_UNKNOWN_OR_NONLOCAL_DATABASE_REFUSED")
    if database == "swinglens" and not args.allow_normal_database:
        parser.error("HISTORICAL_NORMAL_DATABASE_REQUIRES_EXPLICIT_OPT_IN")
    if required_database is not None and database != required_database:
        parser.error("HISTORICAL_TARGET_DOES_NOT_MATCH_RETAINED_INCIDENT")
    operation = "apply" if "--apply" in remaining and "apply" in operations else default_operation
    if default_operation is None:
        operation = next((arg for arg in remaining if arg in operations), None)
    if operation not in operations:
        parser.error("HISTORICAL_EXPLICIT_KNOWN_OPERATION_REQUIRED")
    clone = args.historical_clone
    if clone_required and (not clone or not re.fullmatch(r"swinglens_pytest_[A-Za-z0-9_]+", clone)):
        parser.error("HISTORICAL_EXPLICIT_DISPOSABLE_TARGET_REQUIRED")
    authorization = f"{tool}:{operation}:{database}" + (f":{clone}" if clone_required else "")
    if args.historical_authorize != authorization:
        parser.error("HISTORICAL_EXACT_OPERATION_AUTHORIZATION_REQUIRED")
    if operations[operation]:
        if not sys.stdin.isatty():
            parser.error("HISTORICAL_HUMAN_TERMINAL_CONFIRMATION_REQUIRED")
        try:
            confirmed = input(
                f"Type APPLY {authorization} to authorize this historical operation: "
            )
        except EOFError:
            confirmed = ""
        if confirmed != "APPLY " + authorization:
            parser.error("HISTORICAL_HUMAN_CONFIRMATION_REFUSED")
    os.environ["DATABASE_URL"] = args.historical_target_url
    os.environ["SWINGLENS_TEST_POSTGRES_ADMIN_URL"] = urlunsplit(
        (url.scheme.removesuffix("+psycopg"), url.netloc, "/postgres", "", "")
    )
    if clone_required:
        os.environ["SWINGLENS_HISTORICAL_DISPOSABLE_TARGET"] = clone
    sys.argv[:] = [sys.argv[0], *remaining]


def require_clone_target(database: str) -> None:
    """A retained metadata file cannot silently choose a different clone to mutate/drop."""
    if database != os.environ.get("SWINGLENS_HISTORICAL_DISPOSABLE_TARGET"):
        raise RuntimeError("HISTORICAL_CLONE_TARGET_DOES_NOT_MATCH_EXPLICIT_AUTHORIZATION")
