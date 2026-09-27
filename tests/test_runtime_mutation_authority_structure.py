from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (REPO_ROOT / "app", REPO_ROOT / "scripts")
RECOVERY_PRIMITIVES = {
    "fence_jobs_for_worker",
    "fence_stalled_jobs",
    "reconcile_jobs_for_worker_loss",
    "recover_abandoned_jobs_for_worker",
    "recover_stale_jobs",
    "requeue_stalled_jobs",
}


def test_every_production_recovery_call_supplies_typed_authority() -> None:
    calls = _calls_named(RECOVERY_PRIMITIVES)

    assert len(calls) == 6
    assert [call for call in calls if "authority" not in call.keyword_names] == []
    assert {(call.path, call.function_name) for call in calls} == {
        ("app/services/background_job_service.py", "reconcile_jobs_for_worker_loss"),
        ("app/services/background_worker.py", "recover_abandoned_jobs_for_worker"),
        ("app/services/background_worker.py", "recover_stale_jobs"),
        ("app/worker_supervisor.py", "fence_stalled_jobs"),
        ("app/worker_supervisor.py", "reconcile_jobs_for_worker_loss"),
        ("app/worker_supervisor.py", "requeue_stalled_jobs"),
    }


def test_every_production_sec_registration_call_supplies_runtime_authority() -> None:
    calls = _calls_named(
        {"establish_worker_processor_identity", "register_deployed_processor"}
    )

    assert len(calls) == 4
    assert [call for call in calls if "authority" not in call.keyword_names] == []
    assert {(call.path, call.function_name) for call in calls} == {
        (
            "app/services/background_worker.py",
            "establish_worker_processor_identity",
        ),
        (
            "app/services/ceri/sec/processor_lifecycle.py",
            "register_deployed_processor",
        ),
        ("scripts/manage_sec_processor.py", "register_deployed_processor"),
        (
            "scripts/ops/certify_sec_processor_contract.py",
            "register_deployed_processor",
        ),
    }


class ProductionCall:
    def __init__(
        self,
        *,
        path: str,
        function_name: str,
        keyword_names: frozenset[str | None],
    ) -> None:
        self.path = path
        self.function_name = function_name
        self.keyword_names = keyword_names


def _calls_named(names: set[str]) -> list[ProductionCall]:
    calls: list[ProductionCall] = []
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function_name = _call_name(node)
                if function_name not in names:
                    continue
                calls.append(
                    ProductionCall(
                        path=path.relative_to(REPO_ROOT).as_posix(),
                        function_name=function_name,
                        keyword_names=frozenset(keyword.arg for keyword in node.keywords),
                    )
                )
    return calls


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None
