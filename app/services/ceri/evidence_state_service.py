from __future__ import annotations

from collections import Counter
from copy import deepcopy
from enum import StrEnum
from typing import Any


class CeriEvidenceState(StrEnum):
    PERSISTED = "PERSISTED"
    CONSIDERED = "CONSIDERED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    SELECTED_FOR_COMPONENT = "SELECTED_FOR_COMPONENT"
    SCORED = "SCORED"


class CeriEvidenceLedgerService:
    """Builds a typed, monotonic state ledger without conflating row counts and score use."""

    def enrich(
        self,
        lineage: dict[str, Any],
        *,
        source_ids: list[int],
        opportunity_selected_ids: list[int],
        risk_selected_ids: list[int],
    ) -> dict[str, Any]:
        result = deepcopy(lineage)
        rows: list[dict[str, Any]] = []
        selected_opportunity = set(opportunity_selected_ids)
        selected_risk = set(risk_selected_ids)
        for source_id in sorted(set(source_ids)):
            rows.append(self._row("SOURCE_RECORD", source_id, considered=False))

        for pair in lineage.get("revision_pairs") or []:
            evidence_id = pair.get("feature_id")
            if evidence_id is None:
                continue
            selected = evidence_id in selected_opportunity
            rows.append(
                self._row(
                    "REVISION_FEATURE",
                    evidence_id,
                    # A feature can be unavailable for magnitude while its
                    # dimensionless breadth is selected by another component.
                    accepted=bool(pair.get("available")) or selected,
                    selected=selected,
                    reason=pair.get("unavailable_reason"),
                )
            )

        guidance_rejections = {
            row.get("id"): row.get("reason")
            for row in lineage.get("guidance_rejected") or []
        }
        guidance_selected = set(lineage.get("guidance_selected_ids") or [])
        for evidence_id in lineage.get("guidance_ids") or []:
            rejected = evidence_id in guidance_rejections
            rows.append(
                self._row(
                    "GUIDANCE",
                    evidence_id,
                    accepted=not rejected,
                    selected=evidence_id in guidance_selected,
                    scored=evidence_id in selected_opportunity,
                    reason=guidance_rejections.get(evidence_id),
                )
            )

        catalyst_rejections = {
            row.get("event_id", row.get("id")): row.get("reason")
            for row in lineage.get("catalyst_rejected") or []
        }
        catalyst_selected = set(lineage.get("catalyst_selected_event_ids") or [])
        for evidence_id in lineage.get("catalyst_event_ids") or []:
            rejected = evidence_id in catalyst_rejections
            rows.append(
                self._row(
                    "CATALYST_EVENT",
                    evidence_id,
                    accepted=not rejected,
                    selected=evidence_id in catalyst_selected,
                    scored=evidence_id in selected_risk,
                    reason=catalyst_rejections.get(evidence_id),
                )
            )

        for evidence_id in lineage.get("earnings_ids") or []:
            rows.append(
                self._row(
                    "EARNINGS_ACTUAL",
                    evidence_id,
                    accepted=True,
                    selected=evidence_id in selected_opportunity,
                )
            )

        represented_selected = {
            row["evidence_id"]
            for row in rows
            if CeriEvidenceState.SELECTED_FOR_COMPONENT.value in row["states"]
        }
        for evidence_id in sorted(selected_opportunity - represented_selected):
            rows.append(
                self._row(
                    "COMPONENT_EVIDENCE",
                    evidence_id,
                    accepted=True,
                    selected=True,
                    scored=True,
                    reason="selected_by_opportunity_component_ledger",
                )
            )

        counts = Counter(state for row in rows for state in row["states"])
        result["evidence_states"] = rows
        result["evidence_counts"] = {
            state.value: counts[state.value] for state in CeriEvidenceState
        }
        return result

    @staticmethod
    def _row(
        evidence_type: str,
        evidence_id: int,
        *,
        considered: bool = True,
        accepted: bool = False,
        selected: bool = False,
        scored: bool | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        states = [CeriEvidenceState.PERSISTED.value]
        if considered:
            states.append(CeriEvidenceState.CONSIDERED.value)
            if accepted:
                states.append(CeriEvidenceState.ACCEPTED.value)
                if selected:
                    states.append(CeriEvidenceState.SELECTED_FOR_COMPONENT.value)
                    if scored is not False:
                        states.append(CeriEvidenceState.SCORED.value)
            else:
                states.append(CeriEvidenceState.REJECTED.value)
        return {
            "evidence_type": evidence_type,
            "evidence_id": evidence_id,
            "states": states,
            "reason": reason,
        }
