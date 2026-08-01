from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.services.ceri.config import CeriConfigError, load_ceri_taxonomy
from app.services.ceri.enums import CatalystCategory, CatalystStatus


def test_default_taxonomy_loads_all_required_categories() -> None:
    taxonomy = load_ceri_taxonomy()

    assert set(taxonomy.categories) == set(CatalystCategory)
    assert taxonomy.categories[CatalystCategory.REGULATORY].binary_risk is True
    assert taxonomy.categories[CatalystCategory.CONTRACT].examples
    assert len(taxonomy.config_hash) == 64


def test_status_transitions_keep_cancelled_and_outcome_known_terminal() -> None:
    taxonomy = load_ceri_taxonomy()

    assert taxonomy.status_transitions[CatalystStatus.CANCELLED] == ()
    assert taxonomy.status_transitions[CatalystStatus.OUTCOME_KNOWN] == ()
    assert CatalystStatus.ANNOUNCED in taxonomy.status_transitions[CatalystStatus.SCHEDULED]


def test_missing_required_taxonomy_category_fails(tmp_path: Path) -> None:
    path = _taxonomy_with_mutation(
        tmp_path,
        lambda taxonomy: taxonomy["categories"].pop(),
    )

    with pytest.raises(CeriConfigError, match="taxonomy missing categories"):
        load_ceri_taxonomy(path)


def test_terminal_status_transition_fails(tmp_path: Path) -> None:
    path = _taxonomy_with_mutation(
        tmp_path,
        lambda taxonomy: taxonomy["status_transitions"]["CANCELLED"].append("ANNOUNCED"),
    )

    with pytest.raises(CeriConfigError, match="CANCELLED must be terminal"):
        load_ceri_taxonomy(path)


def _taxonomy_with_mutation(tmp_path: Path, mutate: Any) -> Path:
    taxonomy = yaml.safe_load(
        Path("config/ceri_catalyst_taxonomy.yaml").read_text(encoding="utf-8")
    )
    mutate(taxonomy)
    path = tmp_path / "ceri_catalyst_taxonomy.yaml"
    path.write_text(yaml.safe_dump(taxonomy, sort_keys=False), encoding="utf-8")
    return path
