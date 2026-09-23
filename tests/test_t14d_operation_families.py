"""Exact coverage and conservative family inheritance, independent of verdict."""

import copy
import json
from pathlib import Path

import pytest

from scripts.qa import t14d_operation_families as families

D = Path(__file__).resolve().parents[1] / "docs/remediation/calculation-lineage"


def inputs():
    callers = json.loads(
        (D / "T14D_caller_unification_certification.json").read_text(encoding="utf-8")
    )["callers"]
    review = json.loads((D / "T14D_semantic_family_review.json").read_text(encoding="utf-8"))
    return callers, review


def test_exact_252_callers_have_one_operation_family_and_no_lost_ids():
    callers, review = inputs()
    normalized = families.normalize(callers, review)
    members = [i for family in normalized for i in family.member_initiator_ids]
    assert len(members) == len(set(members)) == 252
    assert set(members) == {c["initiator_id"] for c in callers}
    assert normalized == families.normalize(copy.deepcopy(callers), copy.deepcopy(review))
    assert all(
        f.semantic_mode and f.authority_adapter and f.transaction_boundary for f in normalized
    )
    assert len(normalized) < 234


def test_distinct_authority_branches_cannot_inherit_an_unrelated_family():
    callers, review = inputs()
    normalized = families.normalize(callers, review)
    for family in normalized:
        for proof in family.inheritance_proofs:
            if proof["convergence_kind"] == "DISTINCT_AUTHORITY_OPERATION":
                assert proof["caller_specific_branch"] is True
                assert proof["authority_delivery_boundary"] == family.semantic_service
                assert proof["boundary_certificate"] in families.BOUNDARY_CERTIFICATES
            elif proof["convergence_kind"] == "EXACT_PROTECTED_TRANSACTION":
                address = proof["source"]["address"]
                if address != family.semantic_service:
                    assert proof["source"]["guard_owners"] == [family.semantic_service]


def test_removing_member_guard_breaks_certification_inheritance(monkeypatch):
    callers, review = inputs()
    original = families.source_proof
    address = (
        "app/services/setup_lifecycle/repository.py:"
        "SetupLifecycleRepository.apply_evaluation_counts"
    )

    def changed(site, root=families.ROOT):
        proof = original(site, root)
        if site == address:
            proof["guard_owners"] = []
        return proof

    monkeypatch.setattr(families, "source_proof", changed)
    member_id = next(c["initiator_id"] for c in callers if c["source_address"] == address)
    containing = next(
        f for f in families.normalize(callers, review) if member_id in f.member_initiator_ids
    )
    assert containing.semantic_service == address
    assert containing.member_initiator_ids == (member_id,)
    assert containing.final_status == "INCOMPLETE"


@pytest.mark.parametrize("group", list(families.READ_ONLY_GROUPS))
def test_nonmutating_groups_do_not_contain_write_or_enqueue_calls(group):
    for address in families.READ_ONLY_GROUPS[group]:
        proof = families.source_proof(address)
        assert not any(
            call in {"db.add", "db.execute", "db.flush", "db.commit", "enqueue_job"}
            for call in proof["calls"]
        )


def test_unrecognized_source_proof_does_not_grant_semantic_pass(monkeypatch):
    callers, review = inputs()
    original = families.source_proof
    caller = next(
        c
        for c in callers
        if c["final_disposition"]
        not in {"READ_ONLY", "RETIRED", "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE"}
        and c["source_address"] not in families.REVIEWED_NATIVE_TRANSPORTS
        and c["source_address"] not in families.REVIEWED_TOOLING_TRANSPORTS
        and not original(c["source_address"])["guard_owners"]
    )
    address = caller["source_address"]

    def unreviewed(site, root=families.ROOT):
        proof = original(site, root)
        if site == address:
            proof.update(decorators=[], calls=[], guard_owners=[])
        return proof

    monkeypatch.setattr(families, "source_proof", unreviewed)
    changed_review = copy.deepcopy(review)
    for entry in changed_review["entry_families"].values():
        if address in entry.get("entrypoints", []):
            entry["semantic_review_complete"] = False
    containing = next(
        family
        for family in families.normalize(callers, changed_review)
        if caller["initiator_id"] in family.member_initiator_ids
    )
    assert containing.final_status == "INCOMPLETE"


def test_every_mutating_family_has_shared_positive_and_negative_boundary_evidence():
    callers, review = inputs()
    normalized = families.normalize(callers, review)
    for family in normalized:
        if family.final_status not in {
            "READ_ONLY",
            "RETIRED",
            "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE",
            "INCOMPLETE",
        }:
            assert family.positive_tests
            assert family.negative_tests


def test_all_callers_have_only_final_allowed_dispositions():
    callers, review = inputs()
    normalized = families.normalize(callers, review)
    assert not [family for family in normalized if family.final_status == "INCOMPLETE"]
    assert {family.final_status for family in normalized} <= families.ALLOWED_FINAL_STATUSES


def test_historical_membership_and_current_derivative_fail_on_unreviewed_change():
    from scripts.qa.reconcile_phase5_release import RELEASE, build

    recorded = json.loads((RELEASE / "RELEASE_phase5_current_authority.json").read_text())
    current, changed = build()
    assert current == recorded
    assert len(current["family_mappings"]) == 220
    assert sum(len(row["member_initiator_ids"]) for row in current["family_mappings"]) == 252
    assert current["semantic_equivalent_evolution"] == 61
    assert current["identity_only_drift"] == 1
    assert len(changed) == 62
    assert current["unknown"] == current["confirmed_bypass"] == 0


@pytest.mark.parametrize("dimension", list(families.AuthorityEquivalenceKey.__dataclass_fields__))
def test_every_material_equivalence_dimension_separates_certificates(dimension):
    callers, review = inputs()
    family = families.normalize(callers, review)[0]
    key = copy.deepcopy(family.authority_equivalence_key)
    changed = copy.deepcopy(key)
    changed[dimension] = ["ALTERED_AUTHORITY_DIMENSION"]
    assert families.digest(key) != families.digest(changed)
