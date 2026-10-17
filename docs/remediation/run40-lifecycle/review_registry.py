"""Record architecture census drift against the unmodified source baseline."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts import check_architecture_registry as registry

root = Path(__file__).resolve().parents[3]
evidence = Path(__file__).parent / "evidence"
with tempfile.TemporaryDirectory(prefix="run40-registry-baseline-") as directory:
    baseline = Path(directory)
    shutil.copytree(root / "app", baseline / "app", ignore=shutil.ignore_patterns("__pycache__"))
    paths = (
        "background_job_service.py", "pipeline_service.py", "pipeline_executor.py",
        "setup_lifecycle/change_authority.py", "setup_lifecycle/change_detector.py",
        "setup_lifecycle/evaluation_service.py", "setup_lifecycle/repository.py",
        "setup_lifecycle/query_service.py", "setup_lifecycle/maintenance_service.py",
    )
    for path in paths:
        relative = "app/services/" + path
        (baseline / relative).write_bytes(subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=root))
    registered = json.loads((root / "config/architecture_registry.json").read_text())
    output = {}
    for key, discover, fields in (
        ("writer_candidates", registry.discover_writer_candidates, ("path", "function", "kind")),
        ("transaction_sites", registry.discover_transactions, ("path", "function", "primitive")),
        ("state_writer_sites", lambda p: registry.discover_background_statuses(p)[1], ("path", "function")),
    ):
        identities = lambda rows: {tuple(row[field] for field in fields) for row in rows}
        original = identities(discover(baseline))
        current = identities(discover(root))
        retained = identities(registered[key])
        output[key] = {
            "pre_existing_unregistered": sorted(original - retained),
            "remediation_added_sites": sorted(current - original),
            "remediation_unregistered": sorted((current - original) - retained),
        }
    (evidence / "architecture-baseline-comparison.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
