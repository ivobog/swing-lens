"""Guard discovery against omissions and distinguish proof from candidates."""

import hashlib
import json
import subprocess
from functools import lru_cache

import pytest

from scripts.qa.t14a_mutation_inventory import ROOT, discover
from scripts.qa.t14a_reviewed_inventory import export_inventory, handler_registry, route_registry


@lru_cache(maxsize=1)
def current_source_review_deltas():
    """Independently compare immutable review inputs with current Git-filtered bytes.

    This discovers stale identities; it never approves or rewrites a source pin.
    A fixed list from an older release cannot be the oracle for later source.
    """
    from scripts.qa.reconcile_phase5_release import CURRENT_DERIVATIVE, RELEASE
    from scripts.qa.t14a_semantic_families import CURRENT_REVIEW_FILE, REVIEW_FILE

    derivative = json.loads((RELEASE / CURRENT_DERIVATIVE).read_text())
    pins = json.loads((REVIEW_FILE.parent / "T14D_semantic_review_source_pins.json").read_text())
    recorded = {row["path"]: row for row in derivative["source_pin_reconciliation"]}
    assert set(recorded) == set(pins["sources"])

    def blob(relative):
        return subprocess.check_output(
            ["git", "hash-object", "--path=" + relative, relative],
            cwd=ROOT,
            text=True,
        ).strip()

    current = {relative: blob(relative) for relative in recorded}
    missing_or_stale = sorted(
        relative
        for relative, row in recorded.items()
        if row["historical_pin_sha256"] != pins["sources"][relative]
        or row["release_worktree_blob_id"] != current[relative]
    )
    historical = json.loads(REVIEW_FILE.read_text())["source_pins"]
    stale = {
        relative
        for relative in historical
        if relative not in recorded
        or recorded[relative]["release_worktree_blob_id"] != current[relative]
    }
    current_review = json.loads(CURRENT_REVIEW_FILE.read_text())
    stale.update(
        relative
        for relative, expected in current_review["source_pins"].items()
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected
    )
    assert missing_or_stale and stale  # Current historical derivative is not recertified.
    return missing_or_stale, sorted(stale)


def write_surface(root, path, source):
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")


@pytest.fixture
def source_tree(tmp_path):
    write_surface(
        tmp_path,
        "app/models/tables.py",
        """
class Score:
    __tablename__ = "scores"
class CeriPurgeAudit:
    __tablename__ = "ceri_purge_audits"
""",
    )
    return tmp_path


def test_scalar_dml_and_dirty_fields_are_discovered(source_tree):
    write_surface(
        source_tree,
        "app/services/writer.py",
        """
def persist(db):
    statement = pg_insert(Score).values(value=10).returning(Score.id)
    return list(db.scalars(statement))
def flag(score: Score):
    score.current = False
""",
    )
    functions = {row["symbol"]: row for row in discover(source_tree)["functions"]}
    assert functions["persist"]["write_models"] == ["Score"]
    assert functions["persist"]["orm_sites"][0]["mechanism"] == "scalars"
    assert functions["flag"]["write_models"] == ["Score"]
    assert functions["flag"]["field_sites"]


def test_session_execute_does_not_inherit_unrelated_method_return(source_tree):
    write_surface(
        source_tree,
        "app/services/writer.py",
        """
def execute() -> CeriPurgeAudit:
    return CeriPurgeAudit()
def persist(db):
    db.execute(update(Score).values(value=10))
""",
    )
    row = next(row for row in discover(source_tree)["functions"] if row["symbol"] == "persist")
    assert row["write_models"] == ["Score"]


def test_qa_name_does_not_suppress_production_writer(source_tree):
    write_surface(
        source_tree,
        "scripts/qa/certify_real.py",
        """
def main(db):
    db.add(Score(value=10))
    db.commit()
""",
    )
    row = discover(source_tree)["functions"][0]
    assert row["scope"] == "PRODUCTION"
    assert row["write_models"] == ["Score"]


def test_module_level_hook_is_visible(source_tree):
    write_surface(
        source_tree,
        "app/models/hooks.py",
        """
event.listen(Score, "before_update", protect)
""",
    )
    assert discover(source_tree)["module_sites"][0]["expression"].startswith("event.listen")


@pytest.fixture(scope="module")
def inventory_pair(tmp_path_factory):
    before = tmp_path_factory.mktemp("t14a_inventory_before")
    after = tmp_path_factory.mktemp("t14a_inventory_after")
    inventory = export_inventory(before)
    export_inventory(after)
    return inventory, before, after


def test_exports_are_byte_deterministic(inventory_pair):
    _, before, after = inventory_pair
    assert {path.name for path in before.iterdir()} == {path.name for path in after.iterdir()}
    for path in before.iterdir():
        assert path.read_bytes() == (after / path.name).read_bytes(), path.name


def test_route_and_registered_job_census(inventory_pair):
    inventory, _, _ = inventory_pair
    assert inventory["handlers"] == handler_registry()
    assert len(inventory["handlers"]) == 35
    entries = {row["entrypoint"] for row in inventory["entrypoints"]}
    assert set(inventory["handlers"].values()) <= entries
    actual = {(row["path"], tuple(row["methods"]), row["entrypoint"]) for row in route_registry()}
    recorded = {
        (row["path"], tuple(row["methods"]), row["entrypoint"]) for row in inventory["routes"]
    }
    assert recorded == actual
    get_writes = {
        row["entrypoint"]
        for row in inventory["routes"]
        if row["classification"] == "BUSINESS_MUTATING" and "GET" in row["methods"]
    }
    assert "app/routers/market_regime_routes.py:market_regime_page" not in get_writes
    assert "app/routers/sector_rotation_routes.py:api_sector_rotation" not in get_writes


def test_writer_table_reverse_coverage(inventory_pair):
    inventory, _, _ = inventory_pair
    assert not inventory["unknown_writers"]
    for writer in inventory["writers"]:
        assert writer["tables"], writer["writer"]
        for table in writer["tables"]:
            assert writer["writer"] in inventory["reverse_index"][table]["writers"]
    assert all(
        row["status"] in {"KNOWN_WRITERS", "READ_ONLY_LEGACY"}
        for row in inventory["reverse_index"].values()
    )


@pytest.mark.parametrize(
    "function,model,table",
    [
        (
            "persist_consumer_decision",
            "ConsumerEligibilityDecisionRecord",
            "consumer_eligibility_decisions",
        ),
        (
            "persist_winner_acquisition_decision",
            "WinnerAcquisitionDecisionRecord",
            "winner_acquisition_decisions",
        ),
        (
            "persist_shadow_evaluation",
            "EligibilityShadowEvaluationRecord",
            "eligibility_shadow_evaluations",
        ),
    ],
)
def test_model_bound_ledger_writers_match_actual_production_arguments(
    inventory_pair,
    function,
    model,
    table,
):
    import ast

    from scripts.qa.t14a_reviewed_inventory import OVERRIDES

    relative = "app/services/consumer_eligibility_ledger.py"
    tree = ast.parse((ROOT / relative).read_text())
    owner = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    bound_models = [
        keyword.value.id
        for n in ast.walk(owner)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_insert_once"
        for keyword in n.keywords
        if keyword.arg == "model"
    ]
    address = relative + ":" + function
    assert OVERRIDES[address] == bound_models == [model]
    inventory, _, _ = inventory_pair
    assert address in inventory["reverse_index"][table]["writers"]
    assert inventory["reverse_index"][table]["status"] == "KNOWN_WRITERS"
    assert inventory["reverse_index"][table]["canonical_writer"] == address


@pytest.mark.parametrize(
    "site",
    [
        "app/services/market_sector_intelligence_rotation_repository.py:MarketSectorRotationRepository.capture",
        "app/services/consumer_eligibility_ledger.py:persist_consumer_decision",
        "app/services/consumer_eligibility_ledger.py:persist_winner_acquisition_decision",
        "app/services/consumer_eligibility_ledger.py:persist_shadow_evaluation",
    ],
)
def test_removing_current_writer_review_is_a_hard_failure(inventory_pair, site):
    from scripts.qa.t14a_semantic_families import load_review, normalize

    inventory, _, _ = inventory_pair
    review = load_review()
    assert site in review["sink_reviews"]
    del review["sink_reviews"][site]
    result = normalize(inventory, review)
    assert site in result["blockers"]["unknown_writer_sinks"]
    assert result["verdict"] == "FAIL"


def test_current_review_does_not_approve_changed_source_identity(inventory_pair):
    from scripts.qa.t14a_semantic_families import load_review, normalize

    inventory, _, _ = inventory_pair
    review = load_review()
    relative = "app/services/market_sector_intelligence_rotation_repository.py"
    assert (
        review["current_source_pins"][relative]
        == hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    )
    review["current_source_pins"][relative] = "0" * 64
    result = normalize(inventory, review)
    assert relative in result["blockers"]["stale_source_reviews"]
    assert result["verdict"] == "FAIL"


def test_run41_non_edge_is_exact_and_keeps_unreviewed_calls_visible(source_tree):
    from scripts.qa.t14a_reviewed_inventory import graph

    write_surface(source_tree, "app/services/writer.py", "def run(db):\n    db.add(Score())\n")
    write_surface(
        source_tree,
        "scripts/forensics/verify_run41_recovery.py",
        "import subprocess\ndef prepare():\n    subprocess.run(['pg_dump'])\n"
        "def unreviewed():\n    subprocess.run(['unreviewed'])\n",
    )
    edges, unresolved, _, _ = graph(discover(source_tree), source_tree)
    rows = {r["caller"]: r for r in unresolved if r["call"] == "subprocess.run"}
    reviewed = "scripts/forensics/verify_run41_recovery.py:prepare"
    other = "scripts/forensics/verify_run41_recovery.py:unreviewed"
    assert rows[reviewed]["review_status"] == "FALSE_POSITIVE"
    assert rows[reviewed]["candidates"] == ["app/services/writer.py:run"]
    assert "review_status" not in rows[other]
    assert not edges[reviewed] and not edges[other]


def test_scope_and_transient_review_are_not_filename_suppression(inventory_pair):
    inventory, _, _ = inventory_pair
    entries = {row["entrypoint"]: row for row in inventory["entrypoints"]}
    for path in (
        "scripts/qa/certify_slse_natural.py:main",
        "scripts/qa/rebuild_slse_dev_history.py:main",
        "scripts/certify_sec_incremental_ingestion.py:main",
    ):
        assert entries[path]["business_mutating"]
    assert not entries["app/services/background_worker.py:run_worker"]["business_mutating"]
    assert "scripts/forensics/run153_tbla_readonly_equivalence.py:main" not in entries
    rows = {row["writer"]: row for row in inventory["source_census"]}
    assert rows["scripts/verify_owpe_pre11_activation.py:main"]["classification"] == (
        "ROLLBACK_ONLY_SQL_PROBE"
    )
    assert rows["app/services/ceri/feature_rebuild_service.py:_execute_upsert"]["tables"]
    assert discover(ROOT)["module_sites"]


def test_foundation_does_not_certify_caller_authority(inventory_pair):
    inventory, before, _ = inventory_pair
    # The historical derivative must fail closed when current release source
    # changes. Its original reviewed pins are not rewritten by this release.
    assert inventory["certification"]["verdict"] == "FAIL"
    assert (
        inventory["semantic_completeness"]["blockers"]["missing_or_stale_source_review"]
        == (current_source_review_deltas()[0])
    )
    assert inventory["semantic_completeness"]["caller_authority_adoption_is_pass_gate"] is False
    assert inventory["semantic_completeness"]["raw_path_or_edge_count_is_pass_gate"] is False
    assert "not complete Python runtime reachability" in inventory["proof_boundary"]
    assert any(
        row["mode_proof"] == "NAME_CANDIDATE_REQUIRES_SEMANTIC_REVIEW"
        for row in inventory["writers"]
    )
    assert "UNREVIEWED" in (before / "T14A_entrypoint_writer_inventory.csv").read_text()
    payload = json.loads((before / "T14A_mutation_source_census.json").read_text())
    assert payload["proof_boundary"] == inventory["proof_boundary"]


def test_production_adoption_is_source_qualified_and_preserves_foundation_history():
    # T14B adds explicit writer adapters; foundation history remains separately pinned.
    imports = []
    for path in (ROOT / "app").rglob("*.py"):
        if path.name != "domain_mutation.py" and "domain_mutation" in path.read_text(
            encoding="utf-8"
        ):
            imports.append(path.relative_to(ROOT).as_posix())
    adoption = json.loads(
        (
            ROOT / "docs/remediation/calculation-lineage/T14B_semantic_review_source_pins.json"
        ).read_text()
    )
    decision_adoption = (
        ROOT / "docs/remediation/calculation-lineage/T14C_semantic_review_source_pins.json"
    )
    if decision_adoption.exists():
        adoption = json.loads(decision_adoption.read_text())
    caller_adoption = (
        ROOT / "docs/remediation/calculation-lineage/T14D_semantic_review_source_pins.json"
    )
    if caller_adoption.exists():
        adoption = json.loads(caller_adoption.read_text())
    reviewed_sources = set(adoption["sources"]) | set(adoption.get("post_release_sources", {}))
    assert set(imports) <= reviewed_sources
    assert {
        "app/services/core_mutation_authority.py",
        "app/services/source_mutation_authority.py",
    } <= set(imports)
    assert adoption["foundation_baseline"] == "5eef0d08d783de132fecb14c5dedd66821f0949f"


def test_auxiliary_ownership_review_is_explicit_and_existing():
    import ast

    from scripts.qa.t14a_semantic_review import TABLE_REVIEWS

    raw = json.loads(
        (ROOT / "docs/remediation/calculation-lineage/T14A_raw_discovery.json").read_text()
    )
    auxiliary = {
        table
        for table, row in raw["reverse_index"].items()
        if row["canonical_writer"] == "UNREVIEWED" and row["model"] != "EngineParameters"
    }
    assert len(auxiliary) == 62
    assert auxiliary <= TABLE_REVIEWS.keys()
    for table in auxiliary:
        review = TABLE_REVIEWS[table]
        assert review.proof
        assert review.disposition in {"T14B", "T14C", "T14D"}
        path, symbol = review.owner.split(":", 1)
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == symbol.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
        ), review.owner


def test_source_change_invalidates_review_pin(tmp_path):
    import hashlib

    from scripts.qa.t14a_semantic_completeness import review_source_status

    source = tmp_path / "app/source.py"
    source.parent.mkdir(parents=True)
    source.write_text("before", encoding="utf-8")
    pins = tmp_path / "docs/remediation/calculation-lineage/T14A_semantic_review_source_pins.json"
    pins.parent.mkdir(parents=True)
    pins.write_text(
        json.dumps({"sources": {"app/source.py": hashlib.sha256(source.read_bytes()).hexdigest()}})
    )
    assert review_source_status(tmp_path) == []
    source.write_text("after", encoding="utf-8")
    assert review_source_status(tmp_path) == ["app/source.py"]


def test_incomplete_writer_review_remains_a_hard_foundation_gate(inventory_pair):
    from copy import deepcopy

    from scripts.qa.t14a_semantic_families import load_review, normalize

    inventory, _, _ = inventory_pair
    assert not inventory["unknown_writers"]
    completeness = inventory["semantic_completeness"]
    assert completeness["verdict"] == "FAIL"
    assert completeness["blockers"]["stale_source_reviews"] == (current_source_review_deltas()[1])
    review = deepcopy(load_review())
    review["writer_families"]["WF_FUNDAMENTAL_RECALCULATION"]["semantic_review_complete"] = False
    result = normalize(inventory, review)
    assert result["verdict"] == "FAIL"
    assert result["blockers"]["writer_families_without_completed_semantic_review"] == [
        "WF_FUNDAMENTAL_RECALCULATION"
    ]
    assert not completeness["blockers"]["unknown_writer_sinks"]
    assert not completeness["blockers"]["unowned_business_artifacts"]
    assert (
        completeness["blockers"]["missing_or_stale_source_review"]
        == (current_source_review_deltas()[0])
    )


def test_unique_method_name_does_not_supply_a_graph_edge(inventory_pair):
    inventory, _, _ = inventory_pair
    assert all(
        kind != "UNIQUE_SERVICE_SYMBOL"
        for targets in inventory["call_edges"].values()
        for kind in targets.values()
    )


def test_pipeline_callable_defaults_remain_functions_not_their_return_types(inventory_pair):
    inventory, _, _ = inventory_pair
    edges = inventory["call_edges"]["app/services/pipeline_executor.py:execute_full_pipeline"]
    assert "app/services/fundamental_score_service.py:recalculate_run_fundamentals" in edges
    assert "app/services/ib_fetch_executor.py:execute_fetch_plan" in edges
    assert not inventory["material_unresolved_calls"]


def test_finite_writer_sink_artifact_and_raw_edge_ownership_is_closed(inventory_pair):
    inventory, _, _ = inventory_pair
    blockers = inventory["semantic_completeness"]["blockers"]
    for key in (
        "unknown_writer_sinks",
        "sinks_with_unknown_writer_family",
        "invalid_sink_roles",
        "invalid_writer_families",
        "writer_families_without_completed_semantic_review",
        "invalid_entry_families",
        "business_initiators_without_writer_mapping",
        "business_initiators_without_domain_mapping",
        "business_initiators_without_disposition",
        "unowned_business_artifacts",
        "business_domains_without_policy",
        "missing_handler_assignments",
        "missing_http_assignments",
        "raw_edges_with_unidentified_sink",
        "raw_edges_with_unidentified_writer_family",
        "raw_edges_with_unidentified_entry_family",
    ):
        assert not blockers[key], (key, blockers[key])


def test_adoption_pending_callers_preserve_ownership_but_not_stale_source_pins(inventory_pair):
    from copy import deepcopy

    from scripts.qa.t14a_semantic_families import load_review, normalize

    inventory, _, _ = inventory_pair
    review = load_review()
    provisional = next(
        fid for fid, f in review["entry_families"].items() if not f["semantic_review_complete"]
    )
    changed = deepcopy(review)
    changed["entry_families"][provisional]["status"] = "POTENTIAL_BYPASS"
    result = normalize(inventory, changed)
    assert result["verdict"] == "FAIL"
    assert result["blockers"]["stale_source_reviews"] == current_source_review_deltas()[1]
    assert result["counts"]["caller_authority_reviews_pending"] > 0


def test_unknown_caller_disposition_is_a_foundation_failure(inventory_pair):
    from copy import deepcopy

    from scripts.qa.t14a_semantic_families import load_review, normalize

    inventory, _, _ = inventory_pair
    review = deepcopy(load_review())
    fid = next(iter(review["entry_families"]))
    review["entry_families"][fid]["disposition"] = "UNASSIGNED"
    result = normalize(inventory, review)
    assert result["blockers"]["business_initiators_without_disposition"] == [fid]
    assert result["verdict"] == "FAIL"


def test_orphan_writer_family_reference_is_a_closure_failure(inventory_pair):
    from copy import deepcopy

    from scripts.qa.t14a_semantic_families import load_review, normalize

    inventory, _, _ = inventory_pair
    review = deepcopy(load_review())
    sink = next(iter(review["sink_reviews"]))
    review["sink_reviews"][sink]["family_id"] = "UNKNOWN_WRITER_FAMILY"
    result = normalize(inventory, review)
    assert result["blockers"]["sinks_with_unknown_writer_family"] == [sink]
    assert result["verdict"] == "FAIL"


def test_incomplete_semantics_do_not_publish_certified_family_counts(inventory_pair):
    from copy import deepcopy

    from scripts.qa.t14a_semantic_completeness import semantic_completeness

    inventory, _, _ = inventory_pair
    changed = deepcopy(inventory)
    changed["writers"].append({"writer": "unidentified/new.py:write", "classification": "BUSINESS"})
    completeness = semantic_completeness(changed)
    assert completeness["verdict"] == "FAIL"
    assert completeness["actual_semantic_writer_count"] is None
    assert completeness["actual_production_entrypoint_count"] is None
    assert completeness["provisional_writer_family_count"] > 0
    assert completeness["provisional_entry_family_count"] > 0
