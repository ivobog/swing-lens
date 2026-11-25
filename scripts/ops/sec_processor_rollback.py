"""Owner-signed Windows operator entry point for one exact SEC promotion rollback."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
from base64 import b64encode
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db import engine
from app.services.ceri.sec.processor_rollback import RollbackApproval, rollback_committed_promotion
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.runtime_mutation_authority import RuntimeMutationAuthority
from app.settings import RuntimeMode, get_settings
from scripts.ops.ceri_prospective_revenue import (
    OWNER,
    _database,
    _principal,
    _trust,
    _unique_fields,
    _windows_system_dir,
)

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "swinglens-sec-processor-rollback"
QUALIFICATION_DATABASE = "swinglens_pytest_c06_r2_exactpair"
QUALIFICATION_SERVICE = "SwingLensC06R1Qualification"
QUALIFICATION_PORT = 55492
QUALIFICATION_OWNER = "ivobog-c06-r2-qualification"


def _assert_windows_trust_acl(executable: Path, signers: Path) -> None:
    if os.name != "nt":
        return
    if signers != signers.resolve():
        raise ValueError("Rollback signer trust path is redirected.")
    system = _windows_system_dir()
    command = """
$ErrorActionPreference = 'Stop'
$trusted = @('S-1-5-18', 'S-1-5-32-544')
$principalType = [System.Security.Principal.SecurityIdentifier]
$dangerous = 0x000D0156
foreach ($path in @($env:SWINGLENS_ROLLBACK_TRUST,
                    (Split-Path -Parent $env:SWINGLENS_ROLLBACK_TRUST))) {
    $acl = Get-Acl -LiteralPath $path
    if ($acl.GetOwner($principalType).Value -notin $trusted) { exit 1 }
    foreach ($ace in $acl.GetAccessRules($true, $true, $principalType)) {
        if ($ace.AccessControlType -eq 'Allow' -and
            ($ace.FileSystemRights -band $dangerous) -ne 0 -and
            $ace.IdentityReference.Value -notin $trusted) { exit 1 }
    }
}
"""
    powershell = system / "WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            b64encode(command.encode("utf-16-le")).decode("ascii"),
        ],
        env={**os.environ, "SWINGLENS_ROLLBACK_TRUST": str(signers)},
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode or executable != system / "OpenSSH/ssh-keygen.exe":
        raise ValueError("Rollback signer trust or verifier is not protected.")


def _operator_signer(qualification: bool) -> tuple[Path, Path, str]:
    if not qualification:
        executable, signers = _trust()
        return executable, signers, OWNER

    settings = get_settings()
    target = make_url(settings.database_url)
    if (
        os.name != "nt"
        or os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT") != "AUTHORITATIVE_LOCAL"
        or not target.drivername.startswith("postgresql")
        or (target.host, target.port, target.database)
        != ("127.0.0.1", QUALIFICATION_PORT, QUALIFICATION_DATABASE)
        or target.query
        or settings.swinglens_postgres_service != QUALIFICATION_SERVICE
        or settings.swinglens_postgres_data_dir.resolve()
        != Path("D:/SwingLens-C06-R2-ExactNative-55492/pg")
    ):
        raise ValueError("Qualification trust requires the exact isolated PostgreSQL profile.")

    from scripts.ops.lifecycle_probe import _provenance_report

    report = _provenance_report()
    if not report.get("verified") or (
        report.get("service"), report.get("port"), report.get("database")
    ) != (QUALIFICATION_SERVICE, QUALIFICATION_PORT, QUALIFICATION_DATABASE):
        raise ValueError("Qualification PostgreSQL service ownership was not verified.")
    system = _windows_system_dir()
    signers = Path(system.anchor) / "ProgramData/SwingLensC06R1Qualification-55492/allowed-signers"
    if not signers.is_file() or signers != signers.resolve():
        raise ValueError("Qualification signer trust is unavailable.")
    return system / "OpenSSH/ssh-keygen.exe", signers, QUALIFICATION_OWNER


def _verify_approval(
    path: Path, signature: Path, *, qualification: bool = False,
) -> tuple[RollbackApproval, str]:
    with path.open("rb") as stream:
        content = stream.read(16385)
    if len(content) > 16384:
        raise ValueError("Rollback approval is too large.")
    executable, signers, owner = _operator_signer(qualification)
    _assert_windows_trust_acl(executable, signers)
    result = subprocess.run(
        [
            str(executable),
            "-Y",
            "verify",
            "-f",
            str(signers),
            "-I",
            owner,
            "-n",
            NAMESPACE,
            "-s",
            str(signature),
        ],
        input=content,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Owner rollback signature is invalid.")
    json.loads(content, object_pairs_hook=_unique_fields)
    return RollbackApproval.model_validate_json(content), hashlib.sha256(content).hexdigest()


@contextmanager
def _hold_launcher_lock(root: Path):
    # Match the launcher FileShare.None lock, including across worktrees.
    path = root / "data/cache/swinglens-lifecycle.lock"
    if path.parent.resolve() != path.parent or path.is_symlink():
        raise ValueError("Runtime launcher lock path is redirected.")
    win = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = win.CreateFileW
    open_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    open_file.restype = wintypes.HANDLE
    close_file = win.CloseHandle
    close_file.argtypes = (wintypes.HANDLE,)
    handle = open_file(str(path), 0xC0000000, 0, None, 4, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ValueError("Cannot hold the native runtime launcher lock.")
    try:
        yield
    finally:
        close_file(handle)


def _checkout_identity(root: Path) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git = ["git", "-C", str(root)]
    if subprocess.check_output(
        [*git, "status", "--porcelain", "--untracked-files=normal"],
        env=env,
        timeout=15,
    ).strip():
        raise ValueError("Rollback requires clean release checkouts.")
    return subprocess.check_output([*git, "rev-parse", "HEAD"], env=env, timeout=5).decode().strip()


def _quiescent_runtime(root: Path) -> tuple[str, str]:
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from scripts.ops.lifecycle_probe import (
    RUNTIME_STATE, _assert_physically_quiescent, _runtime_generation_report, _runtime_state_report,
)
if RUNTIME_STATE.exists():
    if _runtime_state_report(None)["classification"] != "DEAD_STALE":
        raise SystemExit("Runtime state is not physically quiescent.")
else:
    _assert_physically_quiescent({})
print(sec_guidance_processor_signature())
print(_runtime_generation_report()["fingerprint"])
"""
    executable = root / ".venv/Scripts/python.exe"
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "PROGRAMDATA",
            "COMPUTERNAME",
            "USERNAME",
            "LOCALAPPDATA",
            "APPDATA",
            "PROGRAMFILES",
        }
    }
    signature, fingerprint = (
        subprocess.check_output(
            [str(executable), "-I", "-c", script, str(root)],
            cwd=root,
            env=env,
            timeout=15,
        )
        .decode()
        .splitlines()
    )
    return signature, fingerprint


def _verified_inputs(approval: RollbackApproval, qualification: Path) -> tuple[Path, Path]:
    candidate, predecessor = (Path(approval.candidate_root), Path(approval.predecessor_root))
    if (
        any(not path.is_absolute() or path != path.resolve() for path in (candidate, predecessor))
        or candidate != ROOT
        or candidate == predecessor
        or _principal() != approval.operator
        or sec_guidance_processor_signature() != approval.candidate
    ):
        raise ValueError(
            "Rollback operator or exact runtime roots do not match the signed approval."
        )
    with qualification.open("rb") as stream:
        contents = stream.read(4_194_305)
    if (
        len(contents) > 4_194_304
        or hashlib.sha256(contents).hexdigest() != approval.qualification_sha256
    ):
        raise ValueError("Predecessor launcher qualification does not match the signed receipt.")
    receipt = json.loads(contents, object_pairs_hook=_unique_fields)
    if receipt.get("status") != "PASS" or receipt.get("git_commit") != approval.predecessor_git:
        raise ValueError("A passing launcher qualification for the exact predecessor is required.")
    return candidate, predecessor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--qualification-trust", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("The supported rollback operator requires the native Windows launcher.")
    if get_settings().runtime_mode is not RuntimeMode.NORMAL:
        raise SystemExit("SEC processor rollback requires the NORMAL runtime profile.")
    approval, approval_sha256 = _verify_approval(
        args.approval, args.signature, qualification=args.qualification_trust,
    )
    candidate, predecessor = _verified_inputs(approval, args.qualification)
    with ExitStack() as locks:
        for root in sorted((candidate, predecessor)):
            locks.enter_context(_hold_launcher_lock(root))
        for root, expected_git, expected_runtime in (
            (candidate, approval.candidate_git, (approval.candidate, approval.candidate_config)),
            (
                predecessor,
                approval.predecessor_git,
                (approval.predecessor, approval.predecessor_config),
            ),
        ):
            if (
                _checkout_identity(root) != expected_git
                or _quiescent_runtime(root) != expected_runtime
            ):
                raise ValueError("Runtime SHA, processor, profile or physical quiescence changed.")
        with Session(engine) as db, db.begin():
            if _database(db) != (
                approval.database_fingerprint,
                approval.database_principal,
                approval.alembic_head,
            ):
                raise ValueError("Rollback database identity or principal changed.")
            receipt = rollback_committed_promotion(
                db,
                approval=approval,
                approval_sha256=approval_sha256,
                actor=approval.operator,
                authority=RuntimeMutationAuthority.normal("cli.sec_processor.rollback"),
            )
    print(json.dumps({"status": "ROLLBACK_COMMITTED", "promotion": receipt}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
