from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CeriTickerCapability:
    revision_slots: set[tuple[str, str]]
    earnings_surprise: bool
    guidance: bool
    catalysts: bool
    unavailable_reasons: dict[str, str]


class CeriCapabilityMatrixService:
    """Creates a sparse eligibility matrix from already-bulk-loaded inputs."""

    def build(
        self,
        *,
        company_ids: list[int],
        estimates_by_company: dict[int, set[tuple[str, str]]],
        earnings_company_ids: set[int],
        guidance_company_ids: set[int],
        catalyst_company_ids: set[int],
    ) -> dict[int, CeriTickerCapability]:
        result: dict[int, CeriTickerCapability] = {}
        for company_id in company_ids:
            slots = set(estimates_by_company.get(company_id, set()))
            unavailable: dict[str, str] = {}
            if not slots:
                unavailable["revisions"] = "NO_ELIGIBLE_ESTIMATE_INPUT"
            if company_id not in earnings_company_ids:
                unavailable["earnings_surprise"] = "NO_REPORTED_EARNINGS"
            if company_id not in guidance_company_ids:
                unavailable["guidance"] = "NO_ACCEPTED_GUIDANCE"
            if company_id not in catalyst_company_ids:
                unavailable["catalysts"] = "NO_ELIGIBLE_CATALYST"
            result[company_id] = CeriTickerCapability(
                revision_slots=slots,
                earnings_surprise=company_id in earnings_company_ids,
                guidance=company_id in guidance_company_ids,
                catalysts=company_id in catalyst_company_ids,
                unavailable_reasons=unavailable,
            )
        return result
