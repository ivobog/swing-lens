from __future__ import annotations

from pathlib import Path

from scripts.qa.scan_tracked_secrets import scan_repository, scan_text


def test_secret_scanner_detects_credential_shapes_without_echoing_values() -> None:
    secret_value = "sk-" + "A" * 32

    findings = scan_text(Path("fixture.env"), f"OPENAI_API_KEY={secret_value}\n")

    assert [(finding.kind, finding.line_number) for finding in findings] == [
        ("openai-key", 1)
    ]
    assert secret_value not in repr(findings)


def test_repository_contains_no_tracked_credential_shapes() -> None:
    assert scan_repository() == []
