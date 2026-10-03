"""Profile real CERI writer declarations against an immutable retained manifest.

The diagnostic reconstructs a completed production feature batch through the normal
expected-manifest loader, forces selected companies through the real feature writers,
captures writer/source-authority telemetry and cProfile data, and rolls back. It never
updates the retained job, manifest, pipeline, or durable business state.
"""

from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import io
import json
import math
import os
import pstats
import subprocess
import tracemalloc
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path
from time import perf_counter, process_time

from sqlalchemy import select, text

import app.services.source_mutation_authority as source_authority
from app.db import SessionLocal
from app.models.ceri_tables import CeriFeatureBuildState
from app.models.tables import BackgroundJob
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.artifact_lineage import CeriArtifactOwnership
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.source_manifest_service import load_feature_source_manifest
from app.services.scope_refresh_adoption import require_semantic_authority
from app.services.source_mutation_authority import (
    compare_writer_fingerprint_paths,
    compare_writer_manifest_paths,
    reference_writer_fingerprint_path,
    reference_writer_manifest_path,
)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def _timing_summary(wall_ms: list[float], cpu_ms: list[float]) -> dict[str, float]:
    return {
        "repetitions": len(wall_ms),
        "wall_min_ms": min(wall_ms),
        "wall_median_ms": _percentile(wall_ms, 0.5),
        "wall_p90_ms": _percentile(wall_ms, 0.9),
        "wall_p95_ms": _percentile(wall_ms, 0.95),
        "wall_max_ms": max(wall_ms),
        "cpu_min_ms": min(cpu_ms),
        "cpu_median_ms": _percentile(cpu_ms, 0.5),
        "cpu_p90_ms": _percentile(cpu_ms, 0.9),
        "cpu_p95_ms": _percentile(cpu_ms, 0.95),
        "cpu_max_ms": max(cpu_ms),
    }


def _benchmark_fingerprint_pair(
    *,
    value: object,
    precomputed: object,
    fragments: object,
    repetitions: int,
) -> dict[str, object]:
    def reference() -> str:
        document = CanonicalEvidenceSerializer.bytes(value, precomputed=precomputed)
        return hashlib.sha256(document).hexdigest()

    def candidate() -> str:
        return CanonicalEvidenceSerializer.fingerprint_streaming(
            value,
            precomputed=precomputed,
            fragments=fragments,
        )

    reference_digest = reference()
    candidate_digest = candidate()
    if reference_digest != candidate_digest:
        raise ValueError("MUTATION_SOURCE_WRITER_MANIFEST_EQUIVALENCE_FAILED")

    timings: dict[str, dict[str, object]] = {}
    for name, operation in (("reference", reference), ("candidate", candidate)):
        wall_values: list[float] = []
        cpu_values: list[float] = []
        gc_before = [generation["collections"] for generation in gc.get_stats()]
        for _ in range(repetitions):
            wall_started = perf_counter()
            cpu_started = process_time()
            digest = operation()
            cpu_values.append((process_time() - cpu_started) * 1000)
            wall_values.append((perf_counter() - wall_started) * 1000)
            if digest != reference_digest:
                raise ValueError("MUTATION_SOURCE_WRITER_MANIFEST_EQUIVALENCE_FAILED")
        gc_after = [generation["collections"] for generation in gc.get_stats()]
        tracemalloc.start()
        operation()
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        timings[name] = {
            **_timing_summary(wall_values, cpu_values),
            "tracemalloc_peak_bytes": peak_bytes,
            "gc_collections": [
                after - before for before, after in zip(gc_before, gc_after, strict=True)
            ],
        }
    return {
        "digest": reference_digest,
        "digest_equal": True,
        "reference": timings["reference"],
        "candidate": timings["candidate"],
    }


class _ManifestDecompositionRecorder:
    """Capture exact writer-document and top-level segment identities without retaining 7 GB."""

    def __init__(self, *, capture_sections: bool) -> None:
        self.capture_sections = capture_sections
        self.invocations: list[dict[str, object]] = []
        self._first_document_by_owner: dict[str, bytes] = {}
        self._last_document_by_owner: dict[str, bytes] = {}

    @staticmethod
    def _shared_prefix(left: bytes, right: bytes, limit: int) -> int:
        index = 0
        stop = min(len(left), len(right), limit)
        while index < stop and left[index] == right[index]:
            index += 1
        return index

    @staticmethod
    def _shared_suffix(left: bytes, right: bytes, limit: int) -> int:
        index = 0
        stop = min(len(left), len(right), limit)
        while index < stop and left[-1 - index] == right[-1 - index]:
            index += 1
        return index

    def observe(
        self,
        manifest: object,
        document: bytes,
        *,
        precomputed: object,
        reference_bytes,
    ) -> None:
        if not (
            isinstance(manifest, dict)
            and set(manifest) == {"writer", "native_source"}
            and isinstance(manifest.get("native_source"), dict)
        ):
            return
        frames = source_authority._writer_telemetry_frames.get()
        frame = frames[-1] if frames else {}
        owner = str(manifest["writer"])
        self._first_document_by_owner.setdefault(owner, document)
        self._last_document_by_owner[owner] = document
        sections = {}
        if self.capture_sections:
            for key, value in manifest["native_source"].items():
                encoded = reference_bytes(value, precomputed=precomputed)
                sections[str(key)] = {
                    "bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                }
        self.invocations.append(
            {
                "owner": owner,
                "ticker": frame.get("ticker"),
                "depth": frame.get("depth"),
                "parent": frame.get("parent"),
                "semantic_arguments": frame.get("semantic_arguments"),
                "canonical_bytes": len(document),
                "digest": hashlib.sha256(document).hexdigest(),
                "sections": sections,
            }
        )

    def summary(self) -> dict[str, object]:
        owners: dict[str, dict[str, object]] = {}
        for owner in sorted({str(item["owner"]) for item in self.invocations}):
            rows = [item for item in self.invocations if item["owner"] == owner]
            section_names = sorted({name for row in rows for name in row["sections"]})
            sections = {}
            stable_payload_bytes = 0
            for name in section_names:
                values = [row["sections"].get(name) for row in rows]
                identities = {
                    (value["bytes"], value["sha256"])
                    for value in values
                    if value is not None
                }
                stable = len(identities) == 1 and all(value is not None for value in values)
                representative_bytes = int(values[0]["bytes"]) if values[0] else 0
                if stable:
                    stable_payload_bytes += representative_bytes
                sections[name] = {
                    "present_calls": sum(value is not None for value in values),
                    "unique_canonical_values": len(identities),
                    "stable_across_owner_calls": stable,
                    "representative_bytes": representative_bytes,
                    "min_bytes": min(int(value["bytes"]) for value in values if value),
                    "max_bytes": max(int(value["bytes"]) for value in values if value),
                }
            total = sum(int(row["canonical_bytes"]) for row in rows)
            first = self._first_document_by_owner[owner]
            last = self._last_document_by_owner[owner]
            owners[owner] = {
                "calls": len(rows),
                "total_canonical_bytes": total,
                "average_canonical_bytes": total / len(rows),
                "stable_top_level_payload_bytes_per_call": stable_payload_bytes,
                "stable_top_level_payload_percent": (
                    100 * stable_payload_bytes / (total / len(rows))
                ),
                "unique_complete_manifests": len({row["digest"] for row in rows}),
                "first_last_common_prefix_bytes": self._shared_prefix(
                    first, last, min(len(first), len(last))
                ),
                "first_last_common_suffix_bytes": self._shared_suffix(
                    first, last, min(len(first), len(last))
                ),
                "sections": sections,
            }
        return {
            "invocation_count": len(self.invocations),
            "unique_complete_manifests": len(
                {item["digest"] for item in self.invocations}
            ),
            "owners": owners,
            "invocations": self.invocations,
        }


def _parse_date(value: object) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _parse_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument(
        "--all-tickers",
        action="store_true",
        help="Exercise the complete retained feature batch.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-lines", type=int, default=80)
    parser.add_argument("--skip-cprofile", action="store_true")
    parser.add_argument(
        "--reference-path",
        action="store_true",
        help="Disable P1 retained-value reuse to measure the pre-P1 algorithm.",
    )
    parser.add_argument(
        "--reference-fingerprint-path",
        action="store_true",
        help="Use materialized P2 bytes while retaining current P1 source-value reuse.",
    )
    parser.add_argument(
        "--compare-paths",
        action="store_true",
        help="Build both manifests from each exact invocation and fail on any byte mismatch.",
    )
    parser.add_argument(
        "--compare-fingerprint-paths",
        action="store_true",
        help="Compare materialized and streaming bytes for the same native manifest.",
    )
    parser.add_argument(
        "--capture-manifest-decomposition",
        action="store_true",
        help="Record exact complete-document and top-level canonical segment identities.",
    )
    parser.add_argument(
        "--capture-manifest-identities",
        action="store_true",
        help="Record every complete document without reserializing top-level sections.",
    )
    parser.add_argument(
        "--benchmark-fingerprints",
        type=int,
        default=0,
        metavar="REPETITIONS",
        help=(
            "Benchmark materialized and streaming fingerprints against the same "
            "captured writer manifests; zero disables the isolated benchmark."
        ),
    )
    args = parser.parse_args()
    if args.benchmark_fingerprints < 0:
        parser.error("--benchmark-fingerprints must be non-negative")

    report: dict[str, object] = {
        "job_id": args.job_id,
        "process_id": os.getpid(),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, encoding="utf-8"
        ).strip(),
        "rollback_only": True,
        "reference_path": args.reference_path,
        "reference_fingerprint_path": args.reference_fingerprint_path,
        "compare_paths": args.compare_paths,
        "compare_fingerprint_paths": args.compare_fingerprint_paths,
    }
    with SessionLocal() as db:
        try:
            db.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": f"ceri-p2-profile-job-{args.job_id}"},
            )
            report["postgres_backend_pid"] = int(db.scalar(text("SELECT pg_backend_pid()")))
            job = db.get(BackgroundJob, args.job_id)
            if job is None or job.job_type != "CERI_FEATURE_BATCH":
                raise ValueError("job must be a retained CERI_FEATURE_BATCH")
            manifest = load_feature_source_manifest(db, job.id)
            if manifest is None:
                raise ValueError("feature source manifest is missing")
            payload = dict(job.payload_json or {})
            batch_tickers = tuple(str(value) for value in payload["tickers"])
            if args.all_tickers and args.ticker:
                raise ValueError("--all-tickers and --ticker are mutually exclusive")
            selected = (
                batch_tickers
                if args.all_tickers
                else tuple(args.ticker or batch_tickers[:1])
            )
            if not selected or any(ticker not in batch_tickers for ticker in selected):
                raise ValueError("selected ticker is not in the retained batch")
            authority = require_semantic_authority(job)
            service = CeriFeatureRebuildService()
            request = CeriFeatureRebuildRequest(
                tickers=batch_tickers,
                run_id=int(payload.get("run_id") or job.related_run_id),
                mode="AS_KNOWN",
                as_of_session=_parse_date(payload.get("as_of_session")),
                cutoff_at=_parse_datetime(payload.get("cutoff_at")),
                calculation_context_id=int(payload["calculation_context_id"]),
                calendar_version=str(payload["calendar_version"]),
                ownership_mode=CeriArtifactOwnership.PIPELINE.value,
                semantic_authority=authority,
                source_manifest_json=manifest.manifest_json,
            )
            load_started = perf_counter()
            context = service.prepare_batch(db, request)
            report["retained_load_wall_s"] = perf_counter() - load_started
            context.source_manifest_id = manifest.id
            bundle = context.source_bodies
            if bundle is None:
                raise ValueError("retained source bundle is missing")
            if bundle.body_fingerprint != manifest.bundle_fingerprint:
                raise ValueError("retained source fingerprint mismatch")
            report["source_count"] = manifest.source_count
            report["bundle_fingerprint"] = manifest.bundle_fingerprint
            report["selected_tickers"] = selected

            # The completed build state would correctly short-circuit. Remove only
            # the selected in-memory state keys so the rollback-only diagnostic
            # exercises the production writers without changing durable evidence.
            selected_ids = {
                company.id for company in context.companies if company.ticker in selected
            }
            context.feature_build_state = {
                key: value
                for key, value in context.feature_build_state.items()
                if key.company_id not in selected_ids
            }
            profiler = cProfile.Profile()
            wall_started = perf_counter()
            cpu_started = process_time()
            if not args.skip_cprofile:
                profiler.enable()
            results = []
            reference_scope = (
                reference_writer_manifest_path() if args.reference_path else nullcontext()
            )
            fingerprint_scope = (
                reference_writer_fingerprint_path()
                if args.reference_fingerprint_path
                else nullcontext()
            )
            comparison_scope = (
                compare_writer_manifest_paths() if args.compare_paths else nullcontext()
            )
            fingerprint_comparison_scope = (
                compare_writer_fingerprint_paths()
                if args.compare_fingerprint_paths
                else nullcontext()
            )
            recorder = _ManifestDecompositionRecorder(
                capture_sections=args.capture_manifest_decomposition
            )
            captured_fingerprints: list[dict[str, object]] = []
            original_bytes_descriptor = CanonicalEvidenceSerializer.__dict__["bytes"]
            original_bytes = CanonicalEvidenceSerializer.bytes
            original_streaming_descriptor = CanonicalEvidenceSerializer.__dict__[
                "fingerprint_streaming"
            ]
            original_streaming = CanonicalEvidenceSerializer.fingerprint_streaming

            def instrumented_bytes(cls, value, *, precomputed=None):
                document = original_bytes(value, precomputed=precomputed)
                recorder.observe(
                    value,
                    document,
                    precomputed=precomputed,
                    reference_bytes=original_bytes,
                )
                return document

            def instrumented_streaming(
                cls,
                value,
                *,
                precomputed=None,
                fragments=None,
                telemetry=None,
                buffer_size=256 * 1024,
            ):
                if (
                    isinstance(value, dict)
                    and set(value) == {"writer", "native_source"}
                    and args.benchmark_fingerprints
                ):
                    frames = source_authority._writer_telemetry_frames.get()
                    frame = frames[-1] if frames else {}
                    captured_fingerprints.append(
                        {
                            "owner": value["writer"],
                            "ticker": frame.get("ticker"),
                            "depth": frame.get("depth"),
                            "value": value,
                            "precomputed": precomputed,
                            "fragments": fragments,
                        }
                    )
                return original_streaming(
                    value,
                    precomputed=precomputed,
                    fragments=fragments,
                    telemetry=telemetry,
                    buffer_size=buffer_size,
                )

            if args.capture_manifest_decomposition or args.capture_manifest_identities:
                CanonicalEvidenceSerializer.bytes = classmethod(instrumented_bytes)
            if args.benchmark_fingerprints:
                CanonicalEvidenceSerializer.fingerprint_streaming = classmethod(
                    instrumented_streaming
                )
            try:
                with (
                    reference_scope,
                    fingerprint_scope,
                    comparison_scope,
                    fingerprint_comparison_scope,
                ):
                    for ticker in selected:
                        result = service.rebuild(
                            db,
                            CeriFeatureRebuildRequest(
                                ticker=ticker,
                                run_id=request.run_id,
                                mode=request.mode,
                                as_of_session=request.as_of_session,
                                cutoff_at=request.cutoff_at,
                                calculation_context_id=request.calculation_context_id,
                                calendar_version=request.calendar_version,
                                ownership_mode=request.ownership_mode,
                                semantic_authority=authority,
                            ),
                            batch_context=context,
                        )
                        if result.failed:
                            raise RuntimeError(f"profiled writer failed: {result.errors!r}")
                        results.append(
                            {
                                "ticker": ticker,
                                "features": result.features,
                                "inserted": result.features_inserted,
                                "updated": result.features_updated,
                                "deduplicated": result.features_deduplicated,
                                "persistence_ms": result.persistence_ms,
                                "batch_total_ms": result.batch_total_ms,
                            }
                        )
            finally:
                if args.capture_manifest_decomposition or args.capture_manifest_identities:
                    CanonicalEvidenceSerializer.bytes = original_bytes_descriptor
                if args.benchmark_fingerprints:
                    CanonicalEvidenceSerializer.fingerprint_streaming = (
                        original_streaming_descriptor
                    )
            if not args.skip_cprofile:
                profiler.disable()
            report["writer_wall_s"] = perf_counter() - wall_started
            report["writer_cpu_s"] = process_time() - cpu_started
            report["results"] = results
            if args.benchmark_fingerprints:
                isolated = []
                for captured in captured_fingerprints:
                    isolated.append(
                        {
                            "owner": captured["owner"],
                            "ticker": captured["ticker"],
                            "depth": captured["depth"],
                            **_benchmark_fingerprint_pair(
                                value=captured["value"],
                                precomputed=captured["precomputed"],
                                fragments=captured["fragments"],
                                repetitions=args.benchmark_fingerprints,
                            ),
                        }
                    )
                ordinary_documents = {
                    "small_scalar_mapping": {
                        "kind": "ordinary",
                        "enabled": True,
                        "count": 7,
                        "ratio": 1.25,
                        "tags": ["alpha", "beta"],
                    },
                    "nested_unicode_evidence": {
                        "owner": "SwingLens/Δ",
                        "payload": [
                            {"index": index, "text": f"row-{index}-é\\n\\t\\\""}
                            for index in range(50)
                        ],
                        "reason_codes": {"SECOND", "FIRST"},
                    },
                }
                ordinary = {
                    name: _benchmark_fingerprint_pair(
                        value=value,
                        precomputed=None,
                        fragments=None,
                        repetitions=max(50, args.benchmark_fingerprints),
                    )
                    for name, value in ordinary_documents.items()
                }
                report["isolated_fingerprint_benchmark"] = {
                    "repetitions": args.benchmark_fingerprints,
                    "writer_manifests": isolated,
                    "ordinary_documents": ordinary,
                }
            if args.capture_manifest_decomposition or args.capture_manifest_identities:
                report["manifest_decomposition"] = recorder.summary()
            bundle.full_audit_unchanged_in_memory()
            evidence_rows = db.execute(
                select(
                    CeriFeatureBuildState.company_id,
                    CeriFeatureBuildState.input_evidence_hash,
                    CeriFeatureBuildState.output_evidence_hash,
                    CeriFeatureBuildState.output_feature_count,
                )
                .where(CeriFeatureBuildState.company_id.in_(selected_ids))
                .where(CeriFeatureBuildState.as_of_session == request.as_of_session)
                .where(CeriFeatureBuildState.historical_view_mode == request.mode)
                .where(CeriFeatureBuildState.config_hash == service.config.config_hash)
                .where(
                    CeriFeatureBuildState.calculation_version
                    == service.config.engine.calculation_version
                )
                .where(CeriFeatureBuildState.ownership_mode == request.ownership_mode)
                .where(
                    CeriFeatureBuildState.calculation_context_id.is_not_distinct_from(
                        request.calculation_context_id
                    )
                )
            ).all()
            report["feature_evidence"] = {
                str(company_id): {
                    "input_evidence_hash": input_hash,
                    "output_evidence_hash": output_hash,
                    "output_feature_count": output_count,
                }
                for company_id, input_hash, output_hash, output_count in evidence_rows
            }
            report["source_integrity_telemetry"] = bundle.telemetry_snapshot()
            if args.skip_cprofile:
                report["profile_top_cumulative"] = "not collected (--skip-cprofile)"
                report["profile_top_self"] = "not collected (--skip-cprofile)"
            else:
                stream = io.StringIO()
                stats = pstats.Stats(profiler, stream=stream).strip_dirs()
                stats.sort_stats("cumulative").print_stats(args.profile_lines)
                report["profile_top_cumulative"] = stream.getvalue()
                stream = io.StringIO()
                stats.stream = stream
                stats.sort_stats("tottime").print_stats(args.profile_lines)
                report["profile_top_self"] = stream.getvalue()
        finally:
            db.rollback()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
