"""Finite, source-pinned semantic closure; raw graph edges remain evidence."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
from collections import Counter

from app.services.domain_mutation import MUTATION_AUTHORITY_POLICIES
from scripts.qa.t14a_mutation_inventory import ROOT

REVIEW_FILE = ROOT / "docs/remediation/calculation-lineage/T14A_semantic_family_review.json"
ADOPTION_REVIEW_FILE = (
    ROOT / "docs/remediation/calculation-lineage/T14B_semantic_family_review.json"
)
if ADOPTION_REVIEW_FILE.exists():
    REVIEW_FILE = ADOPTION_REVIEW_FILE
DECISION_REVIEW_FILE = (
    ROOT / "docs/remediation/calculation-lineage/T14C_semantic_family_review.json"
)
if DECISION_REVIEW_FILE.exists():
    REVIEW_FILE = DECISION_REVIEW_FILE
DISPOSITIONS = {
    "T14B",
    "T14C",
    "T14D",
    "SUPPORTED_DISTINCT_SEMANTICS_NO_CHANGE",
    "LEGACY_RETIRE",
    "DEAD_REMOVE",
    "READ_ONLY_NO_ACTION",
    "OPERATIONAL_NO_ACTION",
    "OUT_OF_SCOPE_WITH_PROOF",
}
ROLES = {
    "CANONICAL_SEMANTIC_WRITER",
    "SEMANTIC_WRITER_MEMBER",
    "CURRENT_PROJECTION_WRITER",
    "CURRENT_RULES_WRITER",
    "DOMAIN_SUPPORTING_STATE",
    "LEGACY_WRITER",
    "DIRECT_SQL_WRITER",
    "CONFIRMED_BYPASS",
    "POTENTIAL_BYPASS",
    "DEAD_OR_UNREACHABLE",
    "TEST_OR_MIGRATION_ONLY",
}
FAMILY_STATUSES = {
    "CANONICAL_WRITER",
    "SUPPORTED_DISTINCT_WRITER",
    "CURRENT_PROJECTION_WRITER",
    "CURRENT_RULES_WRITER",
    "LEGACY_WRITER",
    "DIRECT_SQL_LEGACY",
    "CONFIRMED_BYPASS",
    "POTENTIAL_BYPASS",
    "DOMAIN_SUPPORTING_STATE",
}
ENTRY_DISPOSITIONS = {
    "T14B",
    "T14C",
    "T14D",
    "READ_ONLY_NO_ACTION",
    "OPERATIONAL_NO_ACTION",
    "DEAD_OR_UNREACHABLE",
    "TEST_OR_MIGRATION_ONLY",
    "SUPPORTED_DISTINCT_SEMANTICS",
}


def source_definitions(path):
    definitions = set()

    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                definitions.add(name)
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(ast.parse(path.read_text(encoding="utf-8-sig")))
    return definitions


def normalize(inventory, review=None):
    if review is None:
        review = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    families = review["writer_families"]
    entries = review["entry_families"]
    sinks = review["sink_reviews"]
    writer_by_id = {row["writer"]: row for row in inventory["writers"]}
    business = {
        key for key, row in writer_by_id.items() if row["classification"] != "OPERATIONAL_WRITER"
    }
    entry_index = {}
    for fid, row in entries.items():
        for concrete in row["entrypoints"]:
            entry_index.setdefault(concrete, []).append(fid)
    sink_index = {key: row["family_id"] for key, row in sinks.items()}
    unknown_references = sorted(key for key, fid in sink_index.items() if fid not in families)
    table_index = {}
    for table, row in inventory["reverse_index"].items():
        known = sorted(
            {sink_index[w] for w in row["writers"] if w in sink_index and sink_index[w] in families}
        )
        table_index[table] = {
            "writer_families": known,
            "canonical_owner": row["canonical_writer"],
            "artifact_classification": row["artifact_classification"],
            "domain": row["domain"],
            "projection_families": [
                f for f in known if families[f]["status"] == "CURRENT_PROJECTION_WRITER"
            ],
            "legacy_families": [
                f for f in known if families[f]["status"] in {"LEGACY_WRITER", "DIRECT_SQL_LEGACY"}
            ],
            "dispositions": sorted({families[f]["disposition"] for f in known}),
        }
        owners = row["canonical_writer"].split(";")
        direct = [
            f
            for f in known
            if families[f]["owner"] in owners or set(families[f]["member_sites"]) & set(owners)
        ]
        native = [
            f
            for f in known
            if families[f]["status"] in {"CANONICAL_WRITER", "SUPPORTED_DISTINCT_WRITER"}
            and row["domain"] in families[f]["domains"]
        ]
        canonical = direct or native or known
        table_index[table]["canonical_writer_families"] = canonical
        table_index[table]["known_alternate_writer_families"] = sorted(set(known) - set(canonical))
        table_index[table]["ownership_resolution"] = (
            "NATIVE_OWNER_MEMBERSHIP"
            if direct
            else "REVIEWED_NATIVE_TABLE_OWNER_DELEGATES_TO_NAMED_PERSISTENCE_FAMILIES"
        )

    stale = []
    for path, expected in review["source_pins"].items():
        source = ROOT / path
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            stale.append(path)
    definitions = {}
    missing_owner_declarations = []
    for fid, family in families.items():
        relative, symbol = family["owner"].split(":", 1)
        path = ROOT / relative
        if relative not in definitions:
            definitions[relative] = source_definitions(path) if path.exists() else set()
        if symbol not in definitions[relative]:
            missing_owner_declarations.append(fid)
    declared = {p.domain.value for p in MUTATION_AUTHORITY_POLICIES.values()}
    missing_policies = sorted(
        {
            d
            for family in families.values()
            for d in family["domains"]
            if d not in declared and d != "SHARED_LEDGER"
        }
    )
    missing_entries = sorted(
        row["entrypoint"]
        for row in inventory["entrypoints"]
        if row["business_mutating"] and row["entrypoint"] not in entry_index
    )
    invalid_families = sorted(
        fid
        for fid, f in families.items()
        if (
            not f.get("owner")
            or not f.get("proof")
            or not f.get("member_sites")
            or f["status"] not in FAMILY_STATUSES
            or f["disposition"] not in DISPOSITIONS
            or any(sink_index.get(w) != fid for w in f["member_sites"])
        )
    )
    invalid_entries = sorted(
        fid
        for fid, f in entries.items()
        if (
            not f.get("entrypoints")
            or not f.get("kind")
            or not f.get("reachability")
            or not f.get("domains")
            or f.get("discovery_status") != "DISCOVERED"
            or f.get("disposition") not in ENTRY_DISPOSITIONS
            or f.get("adoption_status")
            not in {
                "ALREADY_CANONICAL",
                "T14B_ADOPTION",
                "T14C_ADOPTION",
                "T14D_ADOPTION",
                "SUPPORTED_DISTINCT_SEMANTICS",
                "NO_ACTION",
            }
            or not f.get("writer_families")
            or any(w not in families for w in f["writer_families"])
        )
    )
    unowned = sorted(
        table
        for table, row in inventory["reverse_index"].items()
        if (
            row["domain"] != "OPERATIONAL"
            and row["status"] != "READ_ONLY_LEGACY"
            and (
                not table_index[table]["writer_families"]
                or row["canonical_writer"] in {"UNREVIEWED", "UNKNOWN_WRITER"}
            )
        )
    )
    handler_index = {}
    for job, function in inventory["handlers"].items():
        assigned = [entries[f] for f in entry_index.get(function, [])]
        handler_index[job] = {
            "handler": function,
            "entry_families": entry_index.get(function, []),
            "classification": "BUSINESS" if assigned else "OPERATIONAL",
            "domains": sorted({d for f in assigned for d in f["domains"]})
            if assigned
            else ["OPERATIONAL"],
            "writer_families": sorted({w for f in assigned for w in f["writer_families"]})
            if assigned
            else ["OPERATIONAL_ONLY"],
            "dispositions": sorted({f["disposition"] for f in assigned})
            if assigned
            else ["OPERATIONAL_NO_ACTION"],
            "adoption_status": sorted({f["adoption_status"] for f in assigned})
            if assigned
            else ["NO_ACTION"],
        }
    entry_by_id = {e["entrypoint"]: e for e in inventory["entrypoints"]}
    missing_handlers = sorted(
        job for job, function in inventory["handlers"].items() if function not in entry_by_id
    )
    missing_http = sorted(
        {
            r["entrypoint"]
            for r in inventory["routes"]
            if r["classification"] == "BUSINESS_MUTATING" and r["entrypoint"] not in entry_index
        }
    )
    blockers = {
        "unknown_writer_sinks": sorted(business - sinks.keys()),
        "sinks_with_unknown_writer_family": unknown_references,
        "invalid_sink_roles": sorted(w for w, r in sinks.items() if r["role"] not in ROLES),
        "unknown_entry_points": missing_entries,
        "invalid_writer_families": invalid_families,
        "writer_owners_missing_source_declaration": missing_owner_declarations,
        "writer_families_without_completed_semantic_review": sorted(
            fid
            for fid, family in families.items()
            if not family.get("semantic_review_complete", False)
        ),
        "invalid_entry_families": invalid_entries,
        "business_initiators_without_writer_mapping": sorted(
            fid
            for fid, f in entries.items()
            if not f.get("writer_families") or any(w not in families for w in f["writer_families"])
        ),
        "business_initiators_without_disposition": sorted(
            fid for fid, f in entries.items() if f.get("disposition") not in ENTRY_DISPOSITIONS
        ),
        "business_initiators_without_domain_mapping": sorted(
            fid
            for fid, f in entries.items()
            if not f.get("domains") or any(d not in declared for d in f["domains"])
        ),
        "unowned_business_artifacts": unowned,
        "business_domains_without_policy": missing_policies,
        "missing_handler_assignments": missing_handlers,
        "missing_http_assignments": missing_http,
        "stale_source_reviews": stale,
        "unknown_persistence_mechanisms": inventory["unknown_writers"],
    }
    counts = Counter(f["status"] for f in families.values())
    result = {
        "writer_families": families,
        "entry_families": entries,
        "sink_reviews": sinks,
        "entry_index": entry_index,
        "table_index": table_index,
        "handler_index": handler_index,
        "reviewed_variants": review["reviewed_variants"],
        "blockers": blockers,
        "verdict": "FAIL" if any(blockers.values()) else "PASS",
        "counts": {
            "writer_families": len(families),
            "entry_families": len(entries),
            "business_persistence_sites": len(business),
            "writer_statuses": dict(counts),
            "entry_statuses": dict(Counter(f["status"] for f in entries.values())),
            "business_mutation_domains": len(declared - {"OPERATIONAL"}),
            "mutating_http_functions": len(
                {
                    r["entrypoint"]
                    for r in inventory["routes"]
                    if r["classification"] == "BUSINESS_MUTATING"
                }
            ),
            "durable_handlers": len(handler_index),
            "cli_admin_repair_entry_families": sum(f["kind"] == "CLI" for f in entries.values()),
            "unknown_writer_families": len(blockers["unknown_writer_sinks"])
            + sum(
                not family.get("semantic_review_complete", False) for family in families.values()
            ),
            "unknown_entry_families": len(set(missing_entries) | set(invalid_entries)),
            "initiators_by_disposition": dict(Counter(f["disposition"] for f in entries.values())),
            "initiators_by_adoption_status": dict(
                Counter(f["adoption_status"] for f in entries.values())
            ),
            "caller_authority_reviews_pending": sum(
                not f.get("semantic_review_complete", False) for f in entries.values()
            ),
            "unowned_business_artifacts": len(unowned),
        },
        "proof_boundary": review["conservative_entry_classification"],
    }
    return result


def edge_closure(inventory, normalized):
    """Keep name inference as a possibility, never as a runtime call proof.

    Reachability through the preserved graph supplies an overapproximate witness
    to already named sinks. It cannot certify dispatch, authority or mathematics.
    """
    raw = json.loads((REVIEW_FILE.parent / "T14A_raw_discovery.json").read_text())
    sinks = normalized["sink_reviews"]
    current = inventory["call_edges"]
    missing_sinks = {
        row["writer"]
        for row in raw["writers"]
        if row["classification"] != "OPERATIONAL_WRITER" and row["writer"] not in sinks
    }

    def witnesses(start):
        queue, seen, found = [start], set(), set()
        while queue:
            node = queue.pop()
            if node in seen:
                continue
            seen.add(node)
            if node in sinks or node in missing_sinks:
                found.add(node)
            queue.extend(raw["call_edges"].get(node, {}))
        return sorted(found)

    rows = []
    for caller, targets in sorted(raw["call_edges"].items()):
        for target, kind in sorted(targets.items()):
            if kind != "UNIQUE_SERVICE_SYMBOL":
                continue
            found = witnesses(target)
            family_ids = sorted({sinks[s]["family_id"] for s in found if s in sinks})
            rows.append(
                {
                    "caller": caller,
                    "target": target,
                    "status": "RESOLVED_TO_CERTIFIED_FAMILY"
                    if current.get(caller, {}).get(target)
                    and found
                    and all(
                        normalized["writer_families"][f].get("semantic_review_complete")
                        for f in family_ids
                    )
                    and all(
                        normalized["entry_families"][f].get("discovery_status") == "DISCOVERED"
                        for f in normalized["entry_index"].get(caller, [])
                    )
                    else "POTENTIAL_DISTINCT_SEMANTICS",
                    "persistence_sink_ids": found,
                    "semantic_writer_family_ids": family_ids,
                    "entry_point_family_ids": normalized["entry_index"].get(caller, []),
                    "proof": (
                        "Preserved name-inferred target; known concrete sink witnesses retained. "
                        "No runtime or authority proof is inferred from the name."
                    ),
                    "unidentified_sink": any(s in missing_sinks for s in found),
                    "unidentified_writer_family": any(s not in sinks for s in found),
                    "unidentified_entry_family": caller
                    in {e["entrypoint"] for e in inventory["entrypoints"] if e["business_mutating"]}
                    and caller not in normalized["entry_index"],
                }
            )
    return rows


def link_exports(destination, inventory):
    """Add stable family references without deleting raw paths or old proofs."""
    normalized = inventory["semantic_normalization"]
    for name, key in (
        ("T14A_entrypoint_writer_inventory.csv", "entrypoint"),
        ("T14A_writer_table_inventory.csv", "writer"),
    ):
        path = destination / name
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            row["persistence_sink_id"] = row["writer"]
            sink = normalized["sink_reviews"].get(row["writer"])
            row["semantic_writer_family_id"] = sink["family_id"] if sink else "OPERATIONAL_ONLY"
            if key == "entrypoint":
                assigned = [
                    normalized["entry_families"][fid]
                    for fid in normalized["entry_index"].get(row[key], [])
                ]
                row["entry_point_family_id"] = (
                    ";".join(normalized["entry_index"].get(row[key], [])) or "OPERATIONAL_ONLY"
                )
                row["raw_discovery_classification"] = row["classification"]
                row["discovery_status"] = "DISCOVERED" if assigned else "PROVEN_NON_BUSINESS"
                row["adoption_status"] = (
                    ";".join(sorted({f["adoption_status"] for f in assigned})) or "NO_ACTION"
                )
                row["remediation_disposition"] = (
                    ";".join(sorted({f["disposition"] for f in assigned}))
                    or "OPERATIONAL_NO_ACTION"
                )
                if assigned and row["classification"] == "UNKNOWN_ENTRY_POINT":
                    row["classification"] = "SEMANTIC_ADOPTION_PENDING"
            else:
                row["semantic_role"] = sink["role"] if sink else "OPERATIONAL_WRITER"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    handoff = {
        task: {
            "primary_writer_family_ids": sorted(
                fid for fid, f in normalized["writer_families"].items() if f["disposition"] == task
            ),
            "primary_initiator_ids": sorted(
                fid for fid, f in normalized["entry_families"].items() if f["disposition"] == task
            ),
            "initiator_writer_dependencies": {
                fid: f["writer_families"]
                for fid, f in sorted(normalized["entry_families"].items())
                if f["disposition"] == task
            },
        }
        for task in ("T14B", "T14C", "T14D")
    }
    handoff["supported_distinct_initiator_ids"] = sorted(
        fid
        for fid, f in normalized["entry_families"].items()
        if f["disposition"] == "SUPPORTED_DISTINCT_SEMANTICS"
    )
    handoff["boundary"] = (
        "Foundation ownership/discovery only; caller authority enforcement is later adoption. "
        "Primary ownership is exclusive; writer dependency IDs retain cross-task dependencies."
    )
    handoff["supported_distinct_writer_ids"] = sorted(
        fid
        for fid, f in normalized["writer_families"].items()
        if f["disposition"] == "SUPPORTED_DISTINCT_SEMANTICS_NO_CHANGE"
    )
    handoff["non_business_initiators"] = {
        e["entrypoint"]: {
            "source_address": e["entrypoint"],
            "entry_type": e["kind"],
            "discovery_status": "PROVEN_NON_BUSINESS",
            "adoption_status": "NO_ACTION",
            "known_persistence_addresses": sorted(e["paths"]),
            "proof": (
                "Reviewed isolation/value-only/operational boundary in the source census; "
                "enclosing worker/startup bookkeeping is separate from business job delivery."
            ),
        }
        for e in inventory["entrypoints"]
        if not e["business_mutating"]
    }
    (destination / "T14A_phase5_handoff.json").write_text(
        json.dumps(handoff, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (destination / "T14A_durable_handler_mapping.json").write_text(
        json.dumps(normalized["handler_index"], sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for kind, data in (
        ("writer", normalized["writer_families"]),
        ("entrypoint", normalized["entry_families"]),
        ("artifact", normalized["table_index"]),
        ("edge", {str(i): r for i, r in enumerate(inventory["normalized_inferred_edges"])}),
    ):
        rows = [
            {
                "family_or_record_id": fid,
                **{
                    k: json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v
                    for k, v in row.items()
                },
            }
            for fid, row in sorted(data.items())
        ]
        keys = list(dict.fromkeys(k for row in rows for k in row))
        with (destination / ("T14A_normalized_" + kind + "_families.csv")).open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=keys, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def certify_current_inventory():
    from scripts.qa.t14a_semantic_completeness import semantic_completeness

    inventory = json.loads(
        (REVIEW_FILE.parent / "T14A_mutation_source_census.json").read_text(encoding="utf-8")
    )
    completeness = semantic_completeness(inventory)
    result = inventory["semantic_normalization"]
    print(
        json.dumps(
            {
                "verdict": completeness["verdict"],
                "counts": result["counts"],
                "blockers": completeness["blockers"],
                "actual_semantic_writer_count": completeness["actual_semantic_writer_count"],
                "actual_production_entrypoint_count": completeness[
                    "actual_production_entrypoint_count"
                ],
                "raw_path_or_edge_count_is_pass_gate": False,
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0 if completeness["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(certify_current_inventory())
