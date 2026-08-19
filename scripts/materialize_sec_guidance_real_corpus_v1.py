from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from app.services.ceri.sec.guidance_extractor import _visible_text

CANDIDATES = Path("output/sec_guidance_real_corpus_work/sec_guidance_real_candidates.jsonl")
PERIODIC_CANDIDATES = Path(
    "output/sec_guidance_real_periodic_work/sec_guidance_real_candidates.jsonl"
)
TEN_K_CANDIDATES = Path("output/sec_guidance_real_10k_work/sec_guidance_real_candidates.jsonl")
PERIODIC_NEGATIVE_IDS = Path(
    "tests/ceri/fixtures/sec_guidance_periodic_negative_case_ids_v1.txt"
)
CORPUS = Path("tests/ceri/fixtures/sec_guidance_real_corpus_v1.jsonl")
MANIFEST = Path("tests/ceri/fixtures/sec_guidance_real_corpus_manifest_v1.json")
CERTIFICATION_REFERENCE = {
    "processor_signature": "sec-guidance:eed017654682a0c9",
    "output_fingerprint_sha256": (
        "e7165fccfbdc6e731bcc26230ac31981f1c5fbeafad6604dcb995e8f04c2d806"
    ),
    "certified": True,
}
DOCUMENT_DIR = Path("tests/ceri/fixtures/sec_guidance_real_documents_v1")

FY = "CURRENT_FISCAL_YEAR"
QUARTER = "CURRENT_QUARTER"


def expected(
    metric: str,
    period: str,
    *,
    low: str | None = None,
    high: str | None = None,
    point: str | None = None,
    unit: str | None = None,
    currency: str | None = None,
    claim: str | None = None,
    comparator: str | None = None,
) -> dict[str, Any]:
    row = {
        "metric": metric,
        "period_label": period,
        "low_value": low,
        "high_value": high,
        "point_value": point,
        "unit": unit,
        "currency": currency,
        "management_claim": claim,
    }
    if comparator:
        row["numeric_comparator"] = comparator
    return row


POSITIVE_ANNOTATIONS: dict[str, list[dict[str, Any]]] = {
    "AMD-2026-08-04-8-K-0000002488-26-000121-q22026991.htm-p222": [
        expected("REVENUE", QUARTER, low="12.7", high="13.3", unit="BILLION", currency="USD")
    ],
    "AMD-2026-05-05-8-K-0000002488-26-000072-q12026991.htm-p206": [
        expected("REVENUE", QUARTER, low="10.9", high="11.5", unit="BILLION", currency="USD")
    ],
    "AMD-2026-02-03-8-K-0000002488-26-000014-q42025991.htm-p258": [
        expected("REVENUE", QUARTER, low="9.5", high="10.1", unit="BILLION", currency="USD")
    ],
    "ORCL-2026-06-10-8-K-0001193125-26-265848-orcl-ex99_1.htm-p65": [
        expected("REVENUE", FY, point="90", unit="BILLION", currency="USD", claim="REAFFIRMED"),
        expected("EPS_DILUTED", FY, point="8.05", unit="PER_SHARE", currency="USD", claim="RAISED"),
    ],
    "ORCL-2026-03-10-8-K-0001193125-26-100148-orcl-ex99_1.htm-p56": [
        expected("REVENUE", FY, point="67", unit="BILLION", currency="USD", claim="MAINTAINED")
    ],
    "ORCL-2026-03-10-8-K-0001193125-26-100148-orcl-ex99_1.htm-p57": [
        expected("REVENUE", FY, point="90", unit="BILLION", currency="USD", claim="RAISED")
    ],
    "CRM-2026-05-27-8-K-0001108524-26-000125-crm-q1fy27xexhibit991.htm-p54": [
        expected(
            "REVENUE",
            QUARTER,
            low="11.27",
            high="11.35",
            unit="BILLION",
            currency="USD",
            claim="INITIATED",
        )
    ],
    "CRM-2026-05-27-8-K-0001108524-26-000125-crm-q1fy27xexhibit991.htm-p56": [
        expected(
            "REVENUE",
            FY,
            low="45.9",
            high="46.2",
            unit="BILLION",
            currency="USD",
            claim="RAISED",
        )
    ],
    "CRM-2026-02-25-8-K-0001108524-26-000056-crm-q4fy26xexhibit991.htm-p58": [
        expected(
            "REVENUE",
            FY,
            low="45.8",
            high="46.2",
            unit="BILLION",
            currency="USD",
            claim="INITIATED",
        )
    ],
    "CRM-2025-12-03-8-K-0001108524-25-000234-crm-q3fy26xexhibit991.htm-p26": [
        expected(
            "REVENUE",
            FY,
            low="41.45",
            high="41.55",
            unit="BILLION",
            currency="USD",
            claim="RAISED",
        )
    ],
    "CRM-2025-12-03-8-K-0001108524-25-000234-crm-q3fy26xexhibit991.htm-p31": [
        expected(
            "REVENUE",
            FY,
            low="41.45",
            high="41.55",
            unit="BILLION",
            currency="USD",
            claim="RAISED",
        )
    ],
    "GE-2026-07-16-8-K-0000040545-26-000047-ge2q2026earningsrelease.htm-p324": [
        expected("REVENUE", FY, point="20", unit="%", claim="RAISED")
    ],
    "JNJ-2026-07-15-8-K-0000200406-26-000146-a2026q2exhibit991.htm-p17": [
        expected("REVENUE", FY, point="101.1", unit="BILLION", currency="USD", claim="RAISED"),
        expected(
            "EPS_DILUTED", FY, point="11.68", unit="PER_SHARE", currency="USD", claim="RAISED"
        ),
    ],
    "UNH-2026-07-16-8-K-0000731766-26-000191-earningsrelease2q26_7152.htm-p6": [
        expected("EPS_DILUTED", FY, low="18.45", high="18.95", unit="PER_SHARE", currency="USD"),
        expected("EPS_DILUTED", FY, low="19.50", high="20.00", unit="PER_SHARE", currency="USD"),
    ],
    "UNH-2026-07-16-8-K-0000731766-26-000191-earningsrelease2q26_7152.htm-p11": [
        expected("EPS_DILUTED", FY, low="18.45", high="18.95", unit="PER_SHARE", currency="USD"),
        expected("EPS_DILUTED", FY, low="19.50", high="20.00", unit="PER_SHARE", currency="USD"),
    ],
    "UNH-2026-07-16-8-K-0000731766-26-000191-uhgearnings_q22026vpower.htm-p5": [
        expected(
            "EPS_DILUTED",
            FY,
            low="18.45",
            high="18.95",
            unit="PER_SHARE",
            currency="USD",
            claim="RAISED",
        ),
        expected(
            "EPS_DILUTED",
            FY,
            low="19.50",
            high="20.00",
            unit="PER_SHARE",
            currency="USD",
            claim="RAISED",
        ),
    ],
    "UNH-2026-07-16-8-K-0000731766-26-000191-uhgearnings_q22026vpower.htm-p10": [
        expected("EPS_DILUTED", FY, low="18.45", high="18.95", unit="PER_SHARE", currency="USD"),
        expected("EPS_DILUTED", FY, low="19.50", high="20.00", unit="PER_SHARE", currency="USD"),
    ],
    "UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p10": [
        expected(
            "REVENUE",
            FY,
            point="439.0",
            unit="BILLION",
            currency="USD",
            comparator="GREATER_THAN",
        ),
        expected(
            "EPS_DILUTED",
            FY,
            point="17.10",
            unit="PER_SHARE",
            currency="USD",
            comparator="GREATER_THAN",
        ),
        expected(
            "EPS_DILUTED",
            FY,
            point="17.75",
            unit="PER_SHARE",
            currency="USD",
            comparator="GREATER_THAN",
        ),
    ],
    "UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p11": [
        expected(
            "REVENUE", FY, point="335000", unit="MILLION", currency="USD", comparator="GREATER_THAN"
        ),
        expected(
            "REVENUE", FY, point="257500", unit="MILLION", currency="USD", comparator="GREATER_THAN"
        ),
        expected(
            "REVENUE", FY, point="439000", unit="MILLION", currency="USD", comparator="GREATER_THAN"
        ),
        expected(
            "EPS_DILUTED",
            FY,
            point="17.10",
            unit="PER_SHARE",
            currency="USD",
            comparator="GREATER_THAN",
        ),
        expected(
            "EPS_DILUTED",
            FY,
            point="17.75",
            unit="PER_SHARE",
            currency="USD",
            comparator="GREATER_THAN",
        ),
    ],
    "UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p12": [
        *[
            expected(
                "REVENUE",
                FY,
                point=value,
                unit="MILLION",
                currency="USD",
                comparator="GREATER_THAN",
            )
            for value in ("75000", "165000", "95000", "335000")
        ]
    ],
    "UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p13": [
        *[
            expected(
                "REVENUE",
                FY,
                point=value,
                unit="MILLION",
                currency="USD",
                comparator="GREATER_THAN",
            )
            for value in ("91000", "21000", "150500", "257500")
        ]
    ],
    "T-2026-01-28-8-K-0000732717-26-000047-t-4q2025exhibit991.htm-p212": [
        expected("REVENUE", FY, point="20", unit="%", comparator="GREATER_THAN_OR_EQUAL")
    ],
    "WMT-2026-05-21-8-K-0000104169-26-000095-earningspresentationfy27.htm-p6": [
        expected("REVENUE", QUARTER, low="4.0", high="5.0", unit="%"),
        expected("EPS_DILUTED", QUARTER, low="0.72", high="0.74", unit="PER_SHARE", currency="USD"),
        expected("REVENUE", FY, low="3.5", high="4.5", unit="%", claim="MAINTAINED"),
        expected(
            "EPS_DILUTED",
            FY,
            low="2.75",
            high="2.85",
            unit="PER_SHARE",
            currency="USD",
            claim="MAINTAINED",
        ),
    ],
    "WMT-2026-05-21-8-K-0000104169-26-000095-earningsreleasefy27q1.htm-p25": [
        expected("REVENUE", QUARTER, low="4", high="5", unit="%", claim="INITIATED")
    ],
    "WMT-2026-02-19-8-K-0000104169-26-000032-earningspresentationfy26.htm-p6": [
        expected("REVENUE", QUARTER, low="3.5", high="4.5", unit="%"),
        expected("EPS_DILUTED", QUARTER, low="0.63", high="0.65", unit="PER_SHARE", currency="USD"),
        expected("REVENUE", FY, low="3.5", high="4.5", unit="%"),
        expected("EPS_DILUTED", FY, low="2.75", high="2.85", unit="PER_SHARE", currency="USD"),
    ],
    "WMT-2025-11-20-8-K-0000104169-25-000177-earningspresentationfy26.htm-p6": [
        expected("REVENUE", FY, low="4.8", high="5.1", unit="%"),
        expected("EPS_DILUTED", FY, low="2.58", high="2.63", unit="PER_SHARE", currency="USD"),
    ],
    "WMT-2025-11-20-8-K-0000104169-25-000177-earningsreleasefy26q3.htm-p25": [
        expected("REVENUE", FY, low="4.8", high="5.1", unit="%", claim="RAISED")
    ],
}


WINDOW_ANNOTATIONS: dict[str, tuple[int, int, list[dict[str, Any]]]] = {
    "ADBE-2026-06-11-8-K-0000796343-26-000109-adbeex991q226.htm-p48": (
        48,
        58,
        [
            expected("REVENUE", QUARTER, low="6.67", high="6.72", unit="BILLION", currency="USD"),
            expected("REVENUE", QUARTER, low="1.87", high="1.89", unit="BILLION", currency="USD"),
            expected("REVENUE", QUARTER, low="4.61", high="4.64", unit="BILLION", currency="USD"),
            expected(
                "EPS_DILUTED", QUARTER, low="4.40", high="4.45", unit="PER_SHARE", currency="USD"
            ),
            expected(
                "EPS_DILUTED", QUARTER, low="6.05", high="6.10", unit="PER_SHARE", currency="USD"
            ),
        ],
    ),
    "ADBE-2026-03-12-8-K-0000796343-26-000048-adbeex991q126.htm-p48": (
        48,
        58,
        [
            expected("REVENUE", QUARTER, low="6.43", high="6.48", unit="BILLION", currency="USD"),
            expected("REVENUE", QUARTER, low="1.80", high="1.82", unit="BILLION", currency="USD"),
            expected("REVENUE", QUARTER, low="4.41", high="4.44", unit="BILLION", currency="USD"),
            expected(
                "EPS_DILUTED", QUARTER, low="4.35", high="4.40", unit="PER_SHARE", currency="USD"
            ),
            expected(
                "EPS_DILUTED", QUARTER, low="5.80", high="5.85", unit="PER_SHARE", currency="USD"
            ),
        ],
    ),
    "ADBE-2025-12-10-8-K-0000796343-25-000135-adbeex991q425.htm-p75": (
        75,
        88,
        [
            expected("REVENUE", FY, low="25.90", high="26.10", unit="BILLION", currency="USD"),
            expected("REVENUE", FY, low="7.35", high="7.40", unit="BILLION", currency="USD"),
            expected("REVENUE", FY, low="17.75", high="17.90", unit="BILLION", currency="USD"),
            expected(
                "EPS_DILUTED", FY, low="17.90", high="18.10", unit="PER_SHARE", currency="USD"
            ),
            expected(
                "EPS_DILUTED", FY, low="23.30", high="23.50", unit="PER_SHARE", currency="USD"
            ),
        ],
    ),
    "SBUX-2026-07-29-8-K-0000829224-26-000129-sbux-06282026xearningsrele.htm-p155": (
        154,
        169,
        [expected("EPS_DILUTED", FY, low="2.55", high="2.65", unit="PER_SHARE", currency="USD")],
    ),
    "SBUX-2026-04-28-8-K-0000829224-26-000078-sbux-03292026xearningsrele.htm-p162": (
        161,
        172,
        [expected("EPS_DILUTED", FY, low="2.25", high="2.45", unit="PER_SHARE", currency="USD")],
    ),
    "SBUX-2026-01-28-8-K-0000829224-26-000010-sbux-12282025xearningsrele.htm-p158": (
        157,
        166,
        [
            expected(
                "REVENUE",
                FY,
                point="3",
                unit="%",
                claim="INITIATED",
                comparator="GREATER_THAN_OR_EQUAL",
            ),
            expected(
                "EPS_DILUTED",
                FY,
                low="2.15",
                high="2.40",
                unit="PER_SHARE",
                currency="USD",
                claim="INITIATED",
            ),
        ],
    ),
}


NEGATIVE_CASE_IDS = {
    "AAPL-2026-07-30-8-K-0000320193-26-000018-a8-kex991q3202606272026.htm-p17",
    "AAPL-2026-04-30-8-K-0000320193-26-000011-a8-kex991q2202603282026.htm-p17",
    "AAPL-2026-01-29-8-K-0000320193-26-000005-a8-kex991q1202612272025.htm-p17",
    "ADBE-2026-03-12-8-K-0000796343-26-000048-adbeex991q126.htm-p69",
    "ADBE-2025-12-10-8-K-0000796343-25-000135-adbeex991q425.htm-p116",
    "ADBE-2026-06-11-8-K-0000796343-26-000109-adbeex991q226.htm-p85",
    "AMD-2026-02-03-8-K-0000002488-26-000014-q42025991.htm-p732",
    "AMD-2026-08-04-8-K-0000002488-26-000121-amdq22026earningsslidesf.htm-p6",
    "AMD-2026-08-04-8-K-0000002488-26-000121-q22026991.htm-p481",
    "CAT-2026-08-04-8-K-0000018230-26-000040-ex991toformcat2q2026earnin.htm-p956",
    "CAT-2026-08-04-8-K-0000018230-26-000040-ex992toformcat2q2026retail.htm-p128",
    "CAT-2026-04-30-8-K-0000018230-26-000017-ex991toformcat1q2026earnin.htm-p953",
    "COST-2026-05-28-8-K-0000909832-26-000046-costex9918-k52826.htm-p48",
    "COST-2026-05-28-8-K-0000909832-26-000046-costex9928-k52826.htm-p13",
    "COST-2026-03-05-8-K-0000909832-26-000025-costex9918-k3526.htm-p83",
    "CRM-2026-05-27-8-K-0001108524-26-000125-crm-q1fy27xexhibit991.htm-p226",
    "CRM-2026-02-25-8-K-0001108524-26-000056-crm-q4fy26xexhibit991.htm-p236",
    "CRM-2025-12-03-8-K-0001108524-25-000234-crm-q3fy26xexhibit991.htm-p210",
    "CVX-2026-07-31-8-K-0000093410-26-000162-a06302026ex9918-k.htm-p598",
    "CVX-2026-05-01-8-K-0000093410-26-000110-a03312026ex9918-k.htm-p446",
    "CVX-2026-04-09-8-K-0000093410-26-000108-cvx-20260409.htm-p106",
    "DAL-2026-01-13-8-K-0000027904-26-000008-dal-20260112.htm-p74",
    "DAL-2026-01-13-8-K-0000027904-26-000008-dal-20260112.htm-p75",
    "FDX-2026-07-21-8-K-0001048911-26-000108-fedexrecastexhibit991.htm-p21",
    "FDX-2026-06-23-8-K-0001048911-26-000050-fdx-earningsreleasefy2026q4.htm-p195",
    "FDX-2026-03-19-8-K-0001048911-26-000010-fdx-earningsreleasefy2026q3.htm-p104",
    "GE-2026-07-16-8-K-0000040545-26-000047-ge2q2026earningsrelease.htm-p1055",
    "GE-2026-04-21-8-K-0000040545-26-000026-ge1q2026earningsrelease.htm-p670",
    "GE-2026-01-22-8-K-0000040545-26-000005-ge4q2025earningsrelease.htm-p1289",
    "GS-2026-07-14-8-K-0000886982-26-000294-a2q26gsearningsresultspr.htm-p17",
    "GS-2026-04-13-8-K-0000886982-26-000096-a1q26gsearningsresultspr.htm-p17",
    "GS-2026-01-15-8-K-0000886982-26-000008-a4q25gsearningsresultspr.htm-p32",
    "JNJ-2026-07-15-8-K-0000200406-26-000146-a2026q2exhibit991.htm-p330",
    "JNJ-2026-04-14-8-K-0000200406-26-000076-a2026q1exhibit991.htm-p313",
    "JNJ-2026-01-21-8-K-0000200406-26-000002-a2025q4exhibit991.htm-p399",
    "JPM-2026-07-14-8-K-0001628280-26-048078-jpm-20260714.htm-p81",
    "JPM-2026-07-14-8-K-0001628280-26-048078-a2q26erfexhibit991narrative.htm-p625",
    "JPM-2026-04-14-8-K-0001628280-26-024990-jpm-20260414.htm-p81",
    "KO-2026-07-28-8-K-0001628280-26-049922-0001628280-26-049922.txt-p3106",
    "KO-2026-07-28-8-K-0001628280-26-049922-a2026q2earningsreleaseex-9.htm-p2964",
    "KO-2026-04-28-8-K-0001628280-26-027723-a2026q1earningsreleaseex-9.htm-p1667",
    "MSFT-2026-07-29-8-K-0001193125-26-323632-msft-ex99_1.htm-p355",
    "MSFT-2026-04-29-8-K-0001193125-26-191457-msft-ex99_1.htm-p243",
    "MSFT-2026-01-28-8-K-0001193125-26-027198-msft-ex99_1.htm-p236",
    "NKE-2026-06-30-8-K-0000320187-26-000076-q4fy26exhibit991er.htm-p115",
    "NKE-2026-03-31-8-K-0000320187-26-000026-q3fy26exhibit991er.htm-p77",
    "NKE-2026-06-23-8-K-0000320187-26-000070-pressrelease062326.htm-p14",
    "NVDA-2026-05-20-8-K-0001045810-26-000051-q1fy27cfocommentary.htm-p244",
    "NVDA-2026-02-25-8-K-0001045810-26-000019-q4fy26cfocommentary.htm-p405",
    "NVDA-2025-11-19-8-K-0001045810-25-000228-q3fy26cfocommentary.htm-p268",
    "ORCL-2026-06-10-8-K-0001193125-26-265848-orcl-ex99_1.htm-p92",
    "ORCL-2026-03-10-8-K-0001193125-26-100148-orcl-ex99_1.htm-p74",
    "ORCL-2025-12-10-8-K-0001193125-25-314207-orcl-ex99_1.htm-p55",
    "SBUX-2026-07-29-8-K-0000829224-26-000129-sbux-06282026xearningsrele.htm-p257",
    "SBUX-2026-04-28-8-K-0000829224-26-000078-sbux-03292026xearningsrele.htm-p251",
    "SBUX-2026-01-28-8-K-0000829224-26-000010-sbux-12282025xearningsrele.htm-p245",
    "T-2026-07-22-8-K-0000732717-26-000294-t-2q2026exhibit991.htm-p402",
    "T-2026-04-22-8-K-0000732717-26-000203-t-1q2026exhibit991.htm-p396",
    "T-2026-07-22-8-K-0000732717-26-000294-t-2q2026exhibit991.htm-p274",
    "TGT-2026-05-20-8-K-0000027419-26-000020-tgt-20260520.htm-p53",
    "TGT-2026-03-03-8-K-0000027419-26-000012-tgt-20260303.htm-p53",
    "TGT-2026-02-11-8-K-0000027419-26-000009-tgt-20260210.htm-p52",
    "UNH-2026-07-16-8-K-0000731766-26-000191-earningsrelease2q26_7152.htm-p12",
    "UNH-2026-07-16-8-K-0000731766-26-000191-uhgearnings_q22026vpower.htm-p12",
    "UNH-2026-04-21-8-K-0000731766-26-000121-earningsrelease1q26press.htm-p10",
    "VZ-2026-07-24-8-K-0000732712-26-000040-vz-20260724.htm-p182",
    "VZ-2026-07-24-8-K-0000732712-26-000040-vz-20260724.htm-p183",
    "VZ-2026-07-24-8-K-0000732712-26-000040-vz-20260724.htm-p188",
    "WMT-2026-05-21-8-K-0000104169-26-000095-earningsreleasefy27q1.htm-p336",
    "WMT-2026-02-19-8-K-0000104169-26-000032-earningsreleasefy26q4.htm-p443",
    "WMT-2025-11-20-8-K-0000104169-25-000177-earningsreleasefy26q3.htm-p323",
    "XOM-2026-07-31-8-K-0002115436-26-000006-livef8k2q26991.htm-p153",
    "XOM-2026-07-31-8-K-0002115436-26-000006-livef8k2q26991.htm-p382",
    "XOM-2026-07-31-8-K-0002115436-26-000006-livef8k2q26991.htm-p384",
}


REVIEW_REQUIRED_CASE_IDS = {
    "NVDA-2026-05-20-8-K-0001045810-26-000051-q1fy27cfocommentary.htm-p226",
    "NVDA-2026-02-25-8-K-0001045810-26-000019-q4fy26cfocommentary.htm-p387",
    "JNJ-2026-07-15-8-K-0000200406-26-000146-a2026q2exhibit991.htm-p22",
    "FDX-2026-03-19-8-K-0001048911-26-000010-fdx-earningsreleasefy2026q3.htm-p78",
    "FDX-2026-03-19-8-K-0001048911-26-000010-fdx-earningsreleasefy2026q3.htm-p80",
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _answers(*, positive: bool, management_claim: bool = False) -> dict[str, bool | None]:
    if not positive:
        return {
            "q1_issuer_guidance": False,
            "q2_metric_supported": None,
            "q3_period_supported": None,
            "q4_numeric_supported": None,
            "q5_unit_supported": None,
            "q6_currency_supported": None,
            "q7_management_action_explicit": None,
            "q8_current_not_historical": False,
            "q9_issuer_specific": None,
        }
    return {
        "q1_issuer_guidance": True,
        "q2_metric_supported": True,
        "q3_period_supported": True,
        "q4_numeric_supported": True,
        "q5_unit_supported": True,
        "q6_currency_supported": True,
        "q7_management_action_explicit": management_claim,
        "q8_current_not_historical": True,
        "q9_issuer_specific": True,
    }


def _negative_note(text: str) -> str:
    lowered = text.lower()
    if "rystad energy" in lowered:
        return "Reviewed negative: third-party industry outlook, not issuer guidance."
    if "forward-looking" in lowered or "safe harbor" in lowered or "undue reliance" in lowered:
        return (
            "Reviewed negative: legal/forward-looking-statement boilerplate without "
            "numeric issuer guidance."
        )
    if "previously issued guidance" in lowered:
        return "Reviewed negative: guidance reference without a numeric guidance value."
    if any(token in lowered for token in ("was", "were", "results", "prior year", "year-ago")):
        return (
            "Reviewed negative: historical results or historical table, not current "
            "issuer guidance."
        )
    return (
        "Reviewed negative: non-actionable outlook/guidance reference without complete "
        "issuer guidance."
    )


def _load_candidates() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = [json.loads(line) for line in CANDIDATES.read_text(encoding="utf-8").splitlines()]
    periodic_rows = [
        json.loads(line)
        for line in PERIODIC_CANDIDATES.read_text(encoding="utf-8").splitlines()
    ]
    rows.extend(row for row in periodic_rows if row["form"] == "10-Q")
    rows.extend(
        json.loads(line)
        for line in TEN_K_CANDIDATES.read_text(encoding="utf-8").splitlines()
    )
    lookup = {row["case_id"]: row for row in rows}
    periodic_negative_ids = set(
        PERIODIC_NEGATIVE_IDS.read_text(encoding="utf-8").splitlines()
    )
    wanted = (
        set(POSITIVE_ANNOTATIONS)
        | set(WINDOW_ANNOTATIONS)
        | NEGATIVE_CASE_IDS
        | periodic_negative_ids
        | REVIEW_REQUIRED_CASE_IDS
    )
    missing = wanted - set(lookup)
    if missing:
        raise RuntimeError(f"Reviewed source candidates are missing: {sorted(missing)}")
    return rows, lookup


def _window_case(
    source: dict[str, Any], start: int, end: int, expected_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    raw_path = Path(source["source_document_path"])
    raw_text = raw_path.read_text(encoding="utf-8")
    paragraphs = re.split(r"\n\s*\n", _visible_text(raw_text))
    passage = "\n\n".join(paragraphs[start - 1 : end])
    row = dict(source)
    row["case_id"] = re.sub(r"-p\d+$", f"-pp{start}-{end}", row["case_id"])
    row["visible_text_locator"] = f"paragraphs-{start}-{end}"
    row["passage_text"] = passage
    row["passage_sha256"] = _hash(passage)
    row["label"] = "POSITIVE"
    row["review_state"] = "REVIEWED"
    row["expected"] = None
    row["expected_rows"] = expected_rows
    row["annotation_answers"] = _answers(
        positive=True,
        management_claim=any(item.get("management_claim") for item in expected_rows),
    )
    row["notes"] = (
        "Reviewed positive: contiguous production-visible table passage with local context."
    )
    row.pop("candidate_extractor_output_non_gold", None)
    return row


def materialize() -> dict[str, Any]:
    source_rows, lookup = _load_candidates()
    periodic_negative_ids = set(
        PERIODIC_NEGATIVE_IDS.read_text(encoding="utf-8").splitlines()
    )
    corpus_rows: list[dict[str, Any]] = []
    for case_id, expected_rows in POSITIVE_ANNOTATIONS.items():
        row = dict(lookup[case_id])
        row["label"] = "POSITIVE"
        row["review_state"] = "REVIEWED"
        row["expected"] = expected_rows[0] if len(expected_rows) == 1 else None
        if len(expected_rows) > 1:
            row["expected_rows"] = expected_rows
        row["annotation_answers"] = _answers(
            positive=True,
            management_claim=any(item.get("management_claim") for item in expected_rows),
        )
        row["notes"] = "Reviewed positive: current issuer revenue/EPS guidance with local context."
        row.pop("candidate_extractor_output_non_gold", None)
        corpus_rows.append(row)
    for case_id, (start, end, expected_rows) in WINDOW_ANNOTATIONS.items():
        corpus_rows.append(_window_case(lookup[case_id], start, end, expected_rows))
    for case_id in NEGATIVE_CASE_IDS:
        row = dict(lookup[case_id])
        row["label"] = "NEGATIVE"
        row["review_state"] = "REVIEWED"
        row["expected"] = None
        row["annotation_answers"] = _answers(positive=False)
        row["notes"] = _negative_note(row["passage_text"])
        row.pop("candidate_extractor_output_non_gold", None)
        corpus_rows.append(row)
    for case_id in periodic_negative_ids:
        row = dict(lookup[case_id])
        row["label"] = "NEGATIVE"
        row["review_state"] = "REVIEWED"
        row["expected"] = None
        row["annotation_answers"] = _answers(positive=False)
        row["notes"] = (
            "Reviewed periodic-filing negative: risk, historical, competitive, or legal "
            "forward-looking context without complete numeric issuer guidance."
        )
        row.pop("candidate_extractor_output_non_gold", None)
        corpus_rows.append(row)
    for case_id in REVIEW_REQUIRED_CASE_IDS:
        row = dict(lookup[case_id])
        row["label"] = "REVIEW_REQUIRED"
        row["review_state"] = "UNRESOLVED"
        row["expected"] = None
        row["annotation_answers"] = None
        row["notes"] = (
            "Unresolved: numeric guidance is present but the isolated production paragraph "
            "does not unambiguously preserve period, comparator, or metric context."
        )
        row.pop("candidate_extractor_output_non_gold", None)
        corpus_rows.append(row)
    corpus_rows.sort(key=lambda row: (row["ticker"], row["filing_date"], row["case_id"]))
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    with CORPUS.open("w", encoding="utf-8", newline="\n") as handle:
        for row in corpus_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    copied_documents: list[dict[str, Any]] = []
    for case_id in (
        "AMD-2026-08-04-8-K-0000002488-26-000121-q22026991.htm-p222",
        "WMT-2026-05-21-8-K-0000104169-26-000095-earningspresentationfy27.htm-p6",
    ):
        source = lookup[case_id]
        source_path = Path(source["source_document_path"])
        destination = (
            DOCUMENT_DIR / f"{source['accession'].replace('-', '')}_{source['document_name']}"
        )
        shutil.copyfile(source_path, destination)
        copied_documents.append(
            {
                "accession": source["accession"],
                "document_name": source["document_name"],
                "path": destination.as_posix(),
                "sha256": _hash(destination.read_text(encoding="utf-8")),
            }
        )
    labels = {
        label: sum(row["label"] == label for row in corpus_rows)
        for label in (
            "POSITIVE",
            "NEGATIVE",
            "REVIEW_REQUIRED",
        )
    }
    manifest = {
        "schema_version": 1,
        "reviewed_at": "2026-08-19",
        "review_method": (
            "Independent semantic review using Q1-Q9; extractor output was not used as gold."
        ),
        "source_candidate_path": CANDIDATES.as_posix(),
        "source_candidate_sha256": _hash(CANDIDATES.read_text(encoding="utf-8")),
        "source_candidate_files": [
            {
                "path": path.as_posix(),
                "sha256": _hash(path.read_text(encoding="utf-8")),
            }
            for path in (CANDIDATES, PERIODIC_CANDIDATES, TEN_K_CANDIDATES)
        ],
        "corpus_path": CORPUS.as_posix(),
        "corpus_sha256": _hash(CORPUS.read_text(encoding="utf-8")),
        "passages": len(corpus_rows),
        "issuers": len({row["cik"] for row in corpus_rows}),
        "filings": len({row["accession"] for row in corpus_rows}),
        "forms": sorted({row["form"] for row in corpus_rows}),
        "labels": labels,
        "source_candidates_available": len(source_rows),
        "offline_production_documents": copied_documents,
        "certification_reference": CERTIFICATION_REFERENCE,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(materialize(), indent=2))
