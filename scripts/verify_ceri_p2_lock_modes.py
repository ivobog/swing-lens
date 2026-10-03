"""Rollback-only PostgreSQL proof for the P2 retained-source lock selection."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models.tables import PriceBar
from app.services.source_mutation_authority import PrefetchedSourceBodies


def _timeout_code(exc: OperationalError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def main() -> int:
    output = Path("artifacts/forensics/ceri_p2_lock_mode_proof.json")
    report: dict[str, object] = {"rollback_only": True, "price_bar_id": 1}
    with SessionLocal() as old_reader, SessionLocal() as old_contender:
        old_reader.execute(
            select(PriceBar.id).where(PriceBar.id == 1).with_for_update()
        ).all()
        old_contender.execute(text("SET LOCAL lock_timeout = '500ms'"))
        started = perf_counter()
        try:
            old_contender.execute(
                select(PriceBar.id).where(PriceBar.id == 1).with_for_update()
            ).all()
        except OperationalError as exc:
            report["old_exclusive_reader"] = {
                "blocked": True,
                "sqlstate": _timeout_code(exc),
                "wait_seconds": perf_counter() - started,
            }
            old_contender.rollback()
        else:
            old_contender.rollback()
            report["old_exclusive_reader"] = {"blocked": False}
        old_reader.rollback()

    with SessionLocal() as first, SessionLocal() as second:
        report["isolation_level"] = first.scalar(text("SHOW transaction_isolation"))
        before = first.scalar(select(PriceBar.data_hash).where(PriceBar.id == 1))
        first_bundle = PrefetchedSourceBodies(first)
        first_bundle.load(PriceBar, select(PriceBar).where(PriceBar.id == 1))

        second.execute(text("SET LOCAL lock_timeout = '500ms'"))
        started = perf_counter()
        second_bundle = PrefetchedSourceBodies(second)
        second_bundle.load(PriceBar, select(PriceBar).where(PriceBar.id == 1))
        report["second_share_reader_seconds"] = perf_counter() - started
        report["share_readers_compatible"] = True

        with SessionLocal() as core_writer:
            core_writer.execute(text("SET LOCAL lock_timeout = '500ms'"))
            started = perf_counter()
            try:
                core_writer.execute(
                    update(PriceBar)
                    .where(PriceBar.id == 1)
                    .values(data_hash="p2-must-rollback")
                )
            except OperationalError as exc:
                report["core_update"] = {
                    "blocked": True,
                    "sqlstate": _timeout_code(exc),
                    "wait_seconds": perf_counter() - started,
                }
                core_writer.rollback()
            else:
                core_writer.rollback()
                report["core_update"] = {"blocked": False}

        with SessionLocal() as raw_writer:
            raw_writer.execute(text("SET LOCAL lock_timeout = '500ms'"))
            started = perf_counter()
            try:
                raw_writer.execute(text("DELETE FROM price_bars WHERE id = 1"))
            except OperationalError as exc:
                report["raw_delete"] = {
                    "blocked": True,
                    "sqlstate": _timeout_code(exc),
                    "wait_seconds": perf_counter() - started,
                }
                raw_writer.rollback()
            else:
                raw_writer.rollback()
                report["raw_delete"] = {"blocked": False}
        second.rollback()
        first.rollback()

    with SessionLocal() as key_reader, SessionLocal() as non_key_writer:
        key_reader.execute(
            select(PriceBar.id)
            .where(PriceBar.id == 1)
            .with_for_update(read=True, key_share=True)
        ).all()
        non_key_writer.execute(text("SET LOCAL lock_timeout = '500ms'"))
        started = perf_counter()
        non_key_writer.execute(
            update(PriceBar)
            .where(PriceBar.id == 1)
            .values(data_hash="p2-key-share-allows-body-update")
        )
        report["key_share_non_key_update"] = {
            "blocked": False,
            "seconds": perf_counter() - started,
        }
        non_key_writer.rollback()
        key_reader.rollback()

    with SessionLocal() as verification:
        after = verification.scalar(select(PriceBar.data_hash).where(PriceBar.id == 1))
        report["before_data_hash"] = before
        report["after_data_hash"] = after
        report["durable_body_unchanged"] = before == after
        verification.rollback()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(
        not (
            report["old_exclusive_reader"]["blocked"]
            and report["share_readers_compatible"]
            and report["core_update"]["blocked"]
            and report["raw_delete"]["blocked"]
            and not report["key_share_non_key_update"]["blocked"]
            and report["durable_body_unchanged"]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
