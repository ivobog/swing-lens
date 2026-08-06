from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    "openai-key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    kind: str


def tracked_files(root: Path = REPO_ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / Path(raw.decode()) for raw in result.stdout.split(b"\0") if raw]


def scan_text(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                findings.append(Finding(path=path, line_number=line_number, kind=kind))
    return findings


def scan_repository(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in tracked_files(root):
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env.example":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(path.relative_to(root), text))
    return findings


def main() -> int:
    findings = scan_repository()
    if not findings:
        print("Tracked secret scan passed: no credential-shaped values found.")
        return 0
    for finding in findings:
        print(f"{finding.path}:{finding.line_number}: {finding.kind}")
    print(f"Tracked secret scan failed with {len(findings)} finding(s).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
