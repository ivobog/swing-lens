from __future__ import annotations

from pathlib import Path

from scripts.docs.check_route_inventory import replace_inventory_blocks


def test_route_inventory_is_current() -> None:
    path = Path("docs/routes_exports.md")
    content = path.read_text(encoding="utf-8")

    assert replace_inventory_blocks(content) == content


def test_governance_docs_exist_and_mark_xlsx_deferred() -> None:
    required_paths = [
        Path("CONTRIBUTING.md"),
        Path("CHANGELOG.md"),
        Path("CODEOWNERS"),
        Path(".github/pull_request_template.md"),
        Path("docs/adr/0000-template.md"),
        Path("docs/adr/README.md"),
        Path("docs/versioning.md"),
        Path("docs/glossary.md"),
        Path("docs/governance/ownership.md"),
        Path("docs/governance/release_checklist.md"),
        Path("docs/governance/migration_checklist.md"),
        Path("docs/governance/model_change_checklist.md"),
        Path("docs/operations/maintainer_handbook.md"),
        Path("docs/fundamental_scoring.md"),
        Path("docs/technical_scoring_pine_parity.md"),
        Path("docs/combined_decisions_ranking_profiles.md"),
        Path("docs/winner_probability_engine.md"),
        Path("docs/routes_exports.md"),
    ]

    missing = [str(path) for path in required_paths if not path.exists()]
    assert missing == []
    assert "XLSX export is deferred" in Path("docs/routes_exports.md").read_text(
        encoding="utf-8"
    )
