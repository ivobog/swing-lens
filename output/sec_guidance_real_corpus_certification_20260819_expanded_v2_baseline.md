# Real SEC Guidance Corpus Certification

## Corpus summary

| Metric | Value |
|---|---:|
| Issuers | 28 |
| Filings | 117 |
| Passages | 232 |
| Positive | 33 |
| Negative | 194 |
| Review required | 5 |

## Classification

| Metric | Result |
|---|---:|
| TP | 17 |
| FP | 0 |
| FN | 16 |
| TN | 194 |
| precision | 1.000000 |
| recall | 0.515152 |
| specificity | 1.000000 |
| f1 | 0.680000 |

## Structured extraction

| Field | Exact-match |
|---|---:|
| Metric | 0.211268 |
| Period | 0.225352 |
| Numeric values | 0.154930 |
| Unit | 0.126761 |
| Currency | 0.056338 |
| Management claim | 0.014085 |
| Evidence locator | 0.239437 |
| All fields | 0.000000 |

## Failure severity

| Severity | Count |
|---|---:|
| CRITICAL | 0 |
| HIGH | 31 |
| MEDIUM | 2 |
| LOW | 0 |

## Version

| Item | Value |
|---|---|
| Git SHA | 2438e6e17979dbfec4b41189fcba2e7a14570b87 |
| Processor signature | sec-guidance:948beb114caa8da9 |
| Parser version | sec-html-text-v1 |
| Extractor version | guidance-regex-visible-text-v2 |
| Locator version | paragraph-locator-v1 |
| Filing-selection version | guidance-forms-v1 |

## Acceptance gates

| Gate | Actual | Requirement | Result |
|---|---:|---:|---|
| reviewed_cases | 227 | >= 100 | PASS |
| positive_cases | 33 | >= 30 | PASS |
| negative_cases | 194 | >= 50 | PASS |
| precision | 1.0 | >= 0.98 | PASS |
| false_positives | 0 | <= 1 | PASS |
| recall | 0.5151515151515151 | >= 0.85 | FAIL |
| numeric_values | 0.15492957746478872 | >= 0.98 | FAIL |
| metric | 0.2112676056338028 | >= 0.98 | FAIL |
| period_label | 0.22535211267605634 | >= 0.95 | FAIL |
| unit | 0.1267605633802817 | >= 0.98 | FAIL |
| currency | 0.056338028169014086 | >= 0.98 | FAIL |
| no_critical_false_positive | True | == True | PASS |

## Failures

### ADBE-2025-12-10-8-K-0000796343-25-000135-adbeex991q425.htm-pp75-88 — FALSE_NEGATIVE (HIGH)

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-25-000135
Form: 8-K
Document: adbeex991q425.htm
Locator: paragraphs-75-88
Probable root cause: unsupported keyword or action synonym

INPUT:

The following table summarizes Adobe’s FY2026 targets

1

:

Total revenue

$25.90 billion to $26.10 billion

Business Professionals & Consumers subscription revenue

$7.35 billion to $7.40 billion

Creative & Marketing Professionals subscription revenue

$17.75 billion to $17.90 billion

Total Adobe ending ARR growth

10.2% year over year

Earnings per share

GAAP: $17.90 to $18.10

Non-GAAP: $23.30 to $23.50

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=25.90, high_value=26.10, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=7.35, high_value=7.40, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=17.75, high_value=17.90, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=17.90, high_value=18.10, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=23.30, high_value=23.50, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### ADBE-2026-03-12-8-K-0000796343-26-000048-adbeex991q126.htm-pp48-58 — FALSE_NEGATIVE (HIGH)

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000048
Form: 8-K
Document: adbeex991q126.htm
Locator: paragraphs-48-58
Probable root cause: unsupported keyword or action synonym

INPUT:

The following table summarizes Adobe’s second quarter FY2026 targets:

Total revenue

$6.43 billion to $6.48 billion

Business Professionals & Consumers subscription revenue

$1.80 billion to $1.82 billion

Creative & Marketing Professionals subscription revenue

$4.41 billion to $4.44 billion

Earnings per share

1

GAAP: $4.35 to $4.40

Non-GAAP: $5.80 to $5.85

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=6.43, high_value=6.48, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_QUARTER, low_value=1.80, high_value=1.82, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_QUARTER, low_value=4.41, high_value=4.44, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_QUARTER, low_value=4.35, high_value=4.40, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_QUARTER, low_value=5.80, high_value=5.85, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### ADBE-2026-06-11-8-K-0000796343-26-000109-adbeex991q226.htm-pp48-58 — FALSE_NEGATIVE (HIGH)

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000109
Form: 8-K
Document: adbeex991q226.htm
Locator: paragraphs-48-58
Probable root cause: unsupported keyword or action synonym

INPUT:

The following table summarizes Adobe’s third quarter FY2026 targets:

Total revenue

$6.67 billion to $6.72 billion

Business Professionals & Consumers subscription revenue

$1.87 billion to $1.89 billion

Creative & Marketing Professionals subscription revenue

$4.61 billion to $4.64 billion

Earnings per share

1

GAAP: $4.40 to $4.45

Non-GAAP: $6.05 to $6.10

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=6.67, high_value=6.72, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_QUARTER, low_value=1.87, high_value=1.89, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_QUARTER, low_value=4.61, high_value=4.64, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_QUARTER, low_value=4.40, high_value=4.45, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_QUARTER, low_value=6.05, high_value=6.10, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### AMD-2026-02-03-8-K-0000002488-26-000014-q42025991.htm-p258 — FIELD_MISMATCH (HIGH)

Ticker: AMD
CIK: 0000002488
Accession: 0000002488-26-000014
Form: 8-K
Document: q42025991.htm
Locator: paragraph-258
Probable root cause: numeric extraction mismatch

INPUT:

For the first quarter of 2026, AMD expects revenue to be approximately $9.8 billion, plus or minus $300 million, including approximately $100 million of AMD Instinct MI308 sales to China. The mid-point of the revenue range represents year-over-year growth of approximately 32% and a sequential decline of approximately 5%. Non-GAAP gross margin is expected to be approximately 55%.

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=9.5, high_value=10.1, point_value=None, unit=BILLION, currency=USD, management_claim=None

ACTUAL:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=None, high_value=None, point_value=2026, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### AMD-2026-05-05-8-K-0000002488-26-000072-q12026991.htm-p206 — FIELD_MISMATCH (HIGH)

Ticker: AMD
CIK: 0000002488
Accession: 0000002488-26-000072
Form: 8-K
Document: q12026991.htm
Locator: paragraph-206
Probable root cause: numeric extraction mismatch

INPUT:

For the second quarter of 2026, AMD expects revenue to be approximately $11.2 billion, plus or minus $300 million. The mid-point of the revenue range represents year-over-year growth of approximately 46% and a sequential increase of approximately 9%. Non-GAAP gross margin is expected to be approximately 56%.

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=10.9, high_value=11.5, point_value=None, unit=BILLION, currency=USD, management_claim=None

ACTUAL:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=None, high_value=None, point_value=2026, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### AMD-2026-08-04-8-K-0000002488-26-000121-q22026991.htm-p222 — FIELD_MISMATCH (HIGH)

Ticker: AMD
CIK: 0000002488
Accession: 0000002488-26-000121
Form: 8-K
Document: q22026991.htm
Locator: paragraph-222
Probable root cause: numeric extraction mismatch

INPUT:

For the third quarter of 2026, AMD expects revenue to be approximately $13 billion, plus or minus $300 million. The mid-point of the revenue range represents year-over-year growth of approximately 41% and a sequential increase of approximately 13%. Non-GAAP gross margin is expected to be approximately 56%.

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=12.7, high_value=13.3, point_value=None, unit=BILLION, currency=USD, management_claim=None

ACTUAL:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=None, high_value=None, point_value=2026, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### CRM-2025-12-03-8-K-0001108524-25-000234-crm-q3fy26xexhibit991.htm-p26 — FIELD_MISMATCH (HIGH)

Ticker: CRM
CIK: 0001108524
Accession: 0001108524-25-000234
Form: 8-K
Document: crm-q3fy26xexhibit991.htm
Locator: paragraph-26
Probable root cause: management action mismatch

INPUT:

Raises full year FY26 revenue guidance to $41.45 billion to $41.55 billion, up 9% - 10% Y/Y and approximately 9% in CC, including approximately 80bps Informatica contribution

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=41.45, high_value=41.55, point_value=None, unit=BILLION, currency=USD, management_claim=RAISED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=41.45, high_value=41.55, point_value=None, unit=BILLION, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### CRM-2025-12-03-8-K-0001108524-25-000234-crm-q3fy26xexhibit991.htm-p31 — FIELD_MISMATCH (HIGH)

Ticker: CRM
CIK: 0001108524
Accession: 0001108524-25-000234
Form: 8-K
Document: crm-q3fy26xexhibit991.htm
Locator: paragraph-31
Probable root cause: management action mismatch

INPUT:

"We are raising fiscal year 2026 revenue guidance to $41.45 billion to $41.55 billion, and Q3 cRPO was exceptional, up 11% year-over-year at $29.4 billion, signaling a powerful pipeline of future revenue," said Marc Benioff, Chair and CEO, Salesforce. “Our Agentforce and Data 360 products are the momentum drivers, hitting nearly $1.4 billion in ARR—an explosive 114% year-over-year gain. We now have over 9,500 paid Agentforce deals and 3.2 trillion tokens processed, underscoring our leadership in building the Agentic Enterprise and driving real outcomes."

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=41.45, high_value=41.55, point_value=None, unit=BILLION, currency=USD, management_claim=RAISED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=41.45, high_value=41.55, point_value=None, unit=BILLION, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### CRM-2026-02-25-8-K-0001108524-26-000056-crm-q4fy26xexhibit991.htm-p58 — FIELD_MISMATCH (HIGH)

Ticker: CRM
CIK: 0001108524
Accession: 0001108524-26-000056
Form: 8-K
Document: crm-q4fy26xexhibit991.htm
Locator: paragraph-58
Probable root cause: management action mismatch

INPUT:

Initiates full year FY27 revenue guidance of $45.8 billion to $46.2 billion, up 10% - 11% Y/Y and in CC, including approximately 3pts Informatica contribution

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=45.8, high_value=46.2, point_value=None, unit=BILLION, currency=USD, management_claim=INITIATED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=45.8, high_value=46.2, point_value=None, unit=BILLION, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### CRM-2026-05-27-8-K-0001108524-26-000125-crm-q1fy27xexhibit991.htm-p54 — FIELD_MISMATCH (HIGH)

Ticker: CRM
CIK: 0001108524
Accession: 0001108524-26-000125
Form: 8-K
Document: crm-q1fy27xexhibit991.htm
Locator: paragraph-54
Probable root cause: management action mismatch

INPUT:

Initiates second quarter FY27 revenue guidance of $11.27 billion to $11.35 billion, up 10% - 11% Y/Y and 10% in CC, including slightly above 4pts Informatica contribution

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=11.27, high_value=11.35, point_value=None, unit=BILLION, currency=USD, management_claim=INITIATED

ACTUAL:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=11.27, high_value=11.35, point_value=None, unit=BILLION, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### CRM-2026-05-27-8-K-0001108524-26-000125-crm-q1fy27xexhibit991.htm-p56 — FIELD_MISMATCH (HIGH)

Ticker: CRM
CIK: 0001108524
Accession: 0001108524-26-000125
Form: 8-K
Document: crm-q1fy27xexhibit991.htm
Locator: paragraph-56
Probable root cause: management action mismatch

INPUT:

Raises midpoint of full year FY27 revenue guidance, now expects full year FY27 revenue of $45.9 billion to $46.2 billion, up 11% Y/Y and 10% - 11%

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=45.9, high_value=46.2, point_value=None, unit=BILLION, currency=USD, management_claim=RAISED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=45.9, high_value=46.2, point_value=None, unit=BILLION, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### GE-2026-07-16-8-K-0000040545-26-000047-ge2q2026earningsrelease.htm-p324 — FALSE_NEGATIVE (HIGH)

Ticker: GE
CIK: 0000040545
Accession: 0000040545-26-000047
Form: 8-K
Document: ge2q2026earningsrelease.htm
Locator: paragraph-324
Probable root cause: metric, period, or numeric wording unsupported

INPUT:

In 2026, CES now expects revenue growth of ~20%, up from our prior outlook of mid-teens, driven by higher services revenue, which we now expect to grow low 20s, up from mid-teens, and equipment revenue growth of ~20%, up from mid-to-high teens. Operating profit is expected to be in the range of $10.25-$10.35 billion, up from our prior guide of $9.6-$9.9 billion.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=20, unit=%, currency=None, management_claim=RAISED

ACTUAL:

<none>

RESULT: FAIL

### JNJ-2026-07-15-8-K-0000200406-26-000146-a2026q2exhibit991.htm-p17 — FALSE_NEGATIVE (HIGH)

Ticker: JNJ
CIK: 0000200406
Accession: 0000200406-26-000146
Form: 8-K
Document: a2026q2exhibit991.htm
Locator: paragraph-17
Probable root cause: metric, period, or numeric wording unsupported

INPUT:

Strong operational performance results in the Company increasing 2026 guidance with estimated reported sales of $101.1 Billion or 7.3% at the midpoint, and increasing adjusted EPS* guidance by $0.13 to $11.68 or 8.2% at the midpoint. The adjusted operational EPS*

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=101.1, unit=BILLION, currency=USD, management_claim=RAISED; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=11.68, unit=PER_SHARE, currency=USD, management_claim=RAISED

ACTUAL:

<none>

RESULT: FAIL

### ORCL-2026-03-10-8-K-0001193125-26-100148-orcl-ex99_1.htm-p56 — FIELD_MISMATCH (HIGH)

Ticker: ORCL
CIK: 0001341439
Accession: 0001193125-26-100148
Form: 8-K
Document: orcl-ex99_1.htm
Locator: paragraph-56
Probable root cause: management action mismatch

INPUT:

For fiscal year 2026, we expect revenue of $67 billion and capital expenditures of $50 billion. This is unchanged from our most recent previous guidance.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=67, unit=BILLION, currency=USD, management_claim=MAINTAINED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=67, unit=None, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### ORCL-2026-03-10-8-K-0001193125-26-100148-orcl-ex99_1.htm-p57 — FALSE_NEGATIVE (HIGH)

Ticker: ORCL
CIK: 0001341439
Accession: 0001193125-26-100148
Form: 8-K
Document: orcl-ex99_1.htm
Locator: paragraph-57
Probable root cause: metric, period, or numeric wording unsupported

INPUT:

For fiscal year 2027, we are raising total revenue guidance to $90 billion.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=90, unit=BILLION, currency=USD, management_claim=RAISED

ACTUAL:

<none>

RESULT: FAIL

### ORCL-2026-06-10-8-K-0001193125-26-265848-orcl-ex99_1.htm-p65 — FIELD_MISMATCH (HIGH)

Ticker: ORCL
CIK: 0001341439
Accession: 0001193125-26-265848
Form: 8-K
Document: orcl-ex99_1.htm
Locator: paragraph-65
Probable root cause: numeric extraction mismatch

INPUT:

For fiscal year 2027, we confirm our prior revenue guidance of $90 billion total revenue and raise our non-GAAP EPS guidance to $8.05, which is growth of 18%

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=90, unit=BILLION, currency=USD, management_claim=REAFFIRMED; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=8.05, unit=PER_SHARE, currency=USD, management_claim=RAISED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=90, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### SBUX-2026-01-28-8-K-0000829224-26-000010-sbux-12282025xearningsrele.htm-pp157-166 — FALSE_NEGATIVE (HIGH)

Ticker: SBUX
CIK: 0000829224
Accession: 0000829224-26-000010
Form: 8-K
Document: sbux-12282025xearningsrele.htm
Locator: paragraphs-157-166
Probable root cause: unsupported keyword or action synonym

INPUT:

Fiscal Year 2026 Guidance

The company introduces the following fiscal year 2026 guidance (all growth targets are relative to fiscal year 2025 non-GAAP measures unless specified):

•

Global and U.S. comparable store sales growth of 3% or greater, with consolidated net revenues growing at a similar rate;

•

Non-GAAP consolidated operating margin to slightly improve year over year;

•

Non-GAAP earnings per share in the range of $2.15 to $2.40; and

•

Approximately 600 to 650 net new coffeehouses globally across company-operated and licensed businesses.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=3, unit=%, currency=None, management_claim=INITIATED; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=2.15, high_value=2.40, point_value=None, unit=PER_SHARE, currency=USD, management_claim=INITIATED

ACTUAL:

<none>

RESULT: FAIL

### SBUX-2026-04-28-8-K-0000829224-26-000078-sbux-03292026xearningsrele.htm-pp161-172 — FALSE_NEGATIVE (HIGH)

Ticker: SBUX
CIK: 0000829224
Accession: 0000829224-26-000078
Form: 8-K
Document: sbux-03292026xearningsrele.htm
Locator: paragraphs-161-172
Probable root cause: unsupported keyword or action synonym

INPUT:

Fiscal Year 2026 Guidance

The company updates its fiscal year 2026 guidance (all growth targets are relative to fiscal year 2025 non-GAAP measures unless specified):

•

Global and U.S. comparable store sales growth of 5.0% or greater;

•

Consolidated net revenues roughly flat year over year;

•

Non-GAAP consolidated operating margin to slightly improve year over year;

•

Non-GAAP earnings per share in the range of $2.25 to $2.45; and

•

Approximately 600 to 650 net new coffeehouses globally across company-operated and licensed businesses.

EXPECTED:

metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=2.25, high_value=2.45, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### SBUX-2026-07-29-8-K-0000829224-26-000129-sbux-06282026xearningsrele.htm-pp154-169 — FALSE_NEGATIVE (HIGH)

Ticker: SBUX
CIK: 0000829224
Accession: 0000829224-26-000129
Form: 8-K
Document: sbux-06282026xearningsrele.htm
Locator: paragraphs-154-169
Probable root cause: unsupported keyword or action synonym

INPUT:

Fiscal Year 2026 Guidance

The company updates its fiscal year 2026 guidance (all growth targets are relative to fiscal year 2025 non-GAAP measures unless specified):

•

Fourth quarter U.S. comparable store sales growth of 6.5% or greater, leading to:

◦

Full fiscal year 2026 U.S. comparable store sales growth of slightly greater than 6.0%; and

◦

Full fiscal year 2026 global comparable store sales growth nearing 6.0%.

•

Consolidated net revenues flat to slight growth year over year;

•

Non-GAAP consolidated operating margin greater than 11.0%;

•

Non-GAAP earnings per share in the range of $2.55 to $2.65; and

•

Approximately 600 to 650 net new coffeehouses globally across company-operated and licensed businesses.

EXPECTED:

metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=2.55, high_value=2.65, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### T-2026-01-28-8-K-0000732717-26-000047-t-4q2025exhibit991.htm-p212 — FALSE_NEGATIVE (HIGH)

Ticker: T
CIK: 0000732717
Accession: 0000732717-26-000047
Form: 8-K
Document: t-4q2025exhibit991.htm
Locator: paragraph-212
Probable root cause: metric, period, or numeric wording unsupported

INPUT:

The Company’s consolidated financial outlook assumes sustained declines in service revenues within its Legacy segment as it makes progress against its objective of powering-down its energy-intensive copper-based network across the large majority of its footprint by the end of 2029 and upgrading customers to advanced connectivity services powered by 5G and fiber. AT&T expects Legacy service revenue to decline 20%+ in 2026 and to be immaterial by the end of 2029 with negative EBITDA* from this segment expected after 2027 until it has substantially eliminated direct costs associated with operating its copper-based network.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=20, unit=%, currency=None, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p10 — FIELD_MISMATCH (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000025
Form: 8-K
Document: a991unherq42025.htm
Locator: paragraph-10
Probable root cause: numeric extraction mismatch

INPUT:

Page 6 of 21    2026 Guidance  • Full Year 2026 Revenue Outlook Greater Than $439.0 Billion  • Earnings from Operations Greater Than $24.0 Billion; Operating Margin of 5.5%  • Earnings Outlook Greater Than $17.10 Per Share; Adjusted Earnings Greater Than $17.75 Per Share  • Cash Flows from Operations Expected to be Greater Than $18.0 Billion  UnitedHealth Group’s 2026 outlook is rooted in extensive actions it has taken in the past six months, including renewed  operating disciplines and deeper commitment to its mission of helping people live healthier lives and helping the health system  work better for everybody.  “UnitedHealth Group’s 2026 outlook reflects a business delivering durable performance improvement and margin expansion  through greater operating discipline and precise execution,” said Wayne DeVeydt, chief financial officer of UnitedHealth  Group.  The outlook reflects margin stability and growth across all four operating segments as the company continues to execute its  long-term strategy.   Among the expectations:  • 2026 revenues are projected to exceed $439.0 billion, a 2% year-over-year decline reflecting planned right-sizing across the  enterprise.  • Earnings from operations greater than $24.0 billion and a net margin of ~3.6%, improving from 2025 net margin of 2.7%.  • Consolidated medical care ratio is expected to be 88.8% +/- 50 basis points, improving 30 basis points from the 2025  medical care ratio of 89.1% and reflective of repricing efforts across the enterprise.  • Operating cost ratio is expected to be 12.8% +/- 50 basis points, reflecting a 10 basis point improvement from the 2025  adjusted operating cost ratio, supported by disciplined cost management and benefits from ongoing productivity initiatives.  • Adjusted earnings per share expected to be greater than $17.75.  The company will continue to embrace new technologies and artificial intelligence to help make high-quality care easier to find,  simpler to navigate, and most importantly, more accessible and affordable.  ($ in millions, except per share data) Revenue  Operating Earnings   UnitedHealthcare    > $335,000  > $10,800   Optum    > $257,500  > $13,200 (a)   Eliminations    ~($153,500)  -   Total UnitedHealth Group    > $439,000  > $24,000               Diluted   Adjusted (b)   Net Earnings per Share    > $17.10  > $17.75   (a) Optum Earnings includes $623 million of operating earnings in Optum Health related to the amortization of loss contracts recognized in 2025.   (b) Refer to page 18 of this release for a reconciliation of the non-GAAP measure.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=439.0, unit=BILLION, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=17.10, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=17.75, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=21, unit=PER_SHARE, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p11 — FALSE_NEGATIVE (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000025
Form: 8-K
Document: a991unherq42025.htm
Locator: paragraph-11
Probable root cause: number format unsupported

INPUT:

Page 7 of 21  2026 Key Performance Expectations  • UnitedHealthcare revenues of more than $335.0 billion reflect fewer consumers served, with membership expected to  range between 46.9 million to 47.5 million.  • Optum revenues of more than $257.5 billion reflect the corresponding membership attrition in Optum Rx and the strategic  right-sizing of Optum Health.  • UnitedHealthcare earnings from operations of greater than $10.8 billion reflect a ~3.2% margin, or ~40 basis points of  margin improvement compared to 2025 adjusted earnings margin of 2.8%.  • Optum earnings from operations are expected to be greater than $13.2 billion, or a margin of ~5.1% compared to 2025  margin of 3.5%. Excluding the impact of loss contracts in Optum Health, Optum’s 2026 adjusted operating earnings  margin is ~4.9%, or an increase of ~40 basis points year-over-year.  Data Elements 2026 Outlook  ($ and weighted-average shares in millions; except per share data)       Revenue         UnitedHealthcare      > $335,000   Optum       > $257,500   Eliminations      ~$(153,500)   UnitedHealth Group      > $439,000                     Operating Earnings         UnitedHealthcare      > $10,800   Optum       > $13,200   UnitedHealth Group      > $24,000            Investment and Other Income      ~$3,900   Interest Expense      ~$3,700   Depreciation and Amortization      ~$4,400   Net Earnings to UNH Shareholders      > $15,600                   Diluted Weighted-Average Shares      910 – 915   Diluted Net Earnings per Share to UNH Shareholders    > $17.10   Adjusted Earnings per Share (1)      > $17.75                   Medical Care Ratio      88.8% ± 50 bps   Operating Cost Ratio      12.8% ± 50 bps   Operating Margin      ~5.5%   Tax Rate      ~19.25%                   Cash Flows from Operations      > $18,000   Dividends Paid (at current rate)      ~$8,000   Share Repurchase      ~$2,500   Capital Expenditures      ~$3,800             (1) Refer to page 18 of this release for a reconciliation of non-GAAP measures.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=335000, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=257500, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=439000, unit=MILLION, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=17.10, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=17.75, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p12 — FALSE_NEGATIVE (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000025
Form: 8-K
Document: a991unherq42025.htm
Locator: paragraph-12
Probable root cause: number format unsupported

INPUT:

Page 8 of 21    UnitedHealthcare 2026 Outlook  ($ in millions)       Revenues:       Employer & Individual    > $75,000    Medicare & Retirement    > $165,000   Community & State    > $95,000   Total UnitedHealthcare Revenue    > $335,000   Operating Earnings    > $10,800   Operating Margin    ~3.2%                People Served (in thousands)  Growth (Contraction) in People Served  Total People Served   Commercial Risk  (1,400) – (1,300)  6,765 – 6,865   Commercial Fee  550 – 750  22,035 – 22,235   Total Commercial   (850) – (550)  28,800 – 29,100   Medicare Advantage  (1,200) – (1,150) (1)  7,245 – 7,295   Standardized Medicare Supplement  (50) – 0  4,235 – 4,285   Medicaid  (715) – (565)  6,665 – 6,815   Total Medical  (2,815) – (2,265)  46,945 – 47,495          Stand-Alone Part D Prescription Drug Plans  (200) – (100)  2,570 – 2,670           (1) Total 2026 contraction for people served through Medicare Advantage, including programs serving complex populations included in Medicaid, is expected to be (1,400,000) to  (1,300,000), consistent with historical presentation.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=75000, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=165000, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=95000, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=335000, unit=MILLION, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### UNH-2026-01-27-8-K-0000731766-26-000025-a991unherq42025.htm-p13 — FALSE_NEGATIVE (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000025
Form: 8-K
Document: a991unherq42025.htm
Locator: paragraph-13
Probable root cause: number format unsupported

INPUT:

Page 9 of 21    Optum 2026 Outlook  ($ in millions)  Revenues  Operating Earnings  Operating Margin   Optum Health (1)  > $91,000  > $2,200  ~2.4%   Optum Insight  > $21,000  > $4,750  ~22.6%   Optum Rx  > $150,500  > $6,250  ~4.2%   Eliminations  ~$(5,000)       Total Optum  > $257,500  > $13,200  ~5.1%                   Growth Metrics         Optum Health Consumers Served     ~84 million   Optum Health Fully Accountable Patients      ~4.1 million   Optum Rx Adjusted Scripts      > 1.52 billion              Below outlines the 2025 Reported and Adjusted Earnings from Operations, as well as the recast earnings for the reclassification  of Optum Financial Services from Optum Health into Optum Insight. 2026 Earnings Guidance assumes Optum Financial  Services is classified in Optum Insight and removed from Optum Health guidance.  Optum 2025 and 2026 Reported to Adjusted Recast Earnings Bridge  ($ in millions)    Optum Health  Optum Insight  Optum Rx  Total Optum             2025 Reported Earnings from Operations  $(278)  $2,624  $7,193  $9,539  Direct Response Costs – Cyberattack  -  $799  -  $799  Net Portfolio Divestitures, Restructuring and Other  $1,941  $304  $(1,068)  $1,177  Provision for Third Party Loss Contracts  $623  -  -  $623  2025 Adjusted Earnings from Operations     $2,286  $3,727  $6,125  $12,138           Optum Financial Services Reclassification (2)  $(837)  $837  -  -  2025 Adjusted Recast Earnings from Operations  $1,449  $4,564  $6,125  $12,138  2025 Adjusted Recast Earnings Margin  1.4%  21.7%  4.0%  4.5%             2026 Reported Operating Earnings Guidance    > $2,200  (1)   > $4,750  > $6,250  > $13,200  Amortization of Third Party Loss Contracts     $(623)  -  -  $(623)  2026 Adjusted Operating Earnings    > $1,577  > $4,750  > $6,250  > $12,577  2026 Adjusted Operating Earnings Margin    ~1.7%  ~22.6%  ~4.2%  ~4.9%            (1) Optum Health includes $623 million of 2026 operating earnings related to the amortization of loss contracts associated with 2025 restructuring and other activities, which will be  excluded from adjusted operating earnings and adjusted earnings per share.  (2) The reclassification of Optum Financial Services from Optum Health into Optum Insight represented $1,906 million in revenue in 2025, inclusive of $289 million of intersegment  Optum eliminations.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=91000, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=21000, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=150500, unit=MILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=None, high_value=None, point_value=257500, unit=MILLION, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### UNH-2026-07-16-8-K-0000731766-26-000191-earningsrelease2q26_7152.htm-p11 — FALSE_NEGATIVE (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000191
Form: 8-K
Document: earningsrelease2q26_7152.htm
Locator: paragraph-11
Probable root cause: number format unsupported

INPUT:

Page 7 of 17    UnitedHealth Group 2026 Outlook  ($ and weighted-average shares in millions; except per share data)    As of   January 27, 2026    As of   July 16, 2026   Operating Earnings         UnitedHealthcare    > $10,800  > $12,000   Optum Health    > $2,200  > $2,275   Optum Insight    > $4,750  > $4,925   Optum Rx    > $6,250  > $6,250   Optum     > $13,200  > $13,450   UnitedHealth Group    > $24,000  > $25,450   Net Earnings to UNH Shareholders    > $15,600  > $16,750   Diluted Net Earnings per Share to UNH Shareholders  > $17.10  $18.45 - $18.95   Adjusted Earnings per Share  (1)     > $17.75  $19.50 - $20.00   Medical Care Ratio    88.8% ± 50 bps  88.1% ± 25 bps   Tax Rate    ~19.25%  ~18.5%   Cash Flows from Operations    > $18,000  ~$24,000   Share Repurchase    ~$2,500  At Least $5,000     (1) Refer to page 16 of this release for a reconciliation of non-GAAP measures.     Below outlines the 2026 Reported to Adjusted Earnings Bridge for Optum as of July 16, 2026.  Optum 2026 Reported to Adjusted Earnings Bridge  ($ in millions)    Optum Health  Optum Insight  Optum Rx  Total Optum  2026 Reported Operating Earnings Guidance    > $2,275  (1)   > $4,925  > $6,250  > $13,450  Net Portfolio Divestitures, Restructuring and Other    $345  $(175)  -  $170  Net Change in Third Party Loss Contracts     $(405)  -  -  $(405)  2026 Adjusted Operating Earnings    > $2,215  > $4,750  > $6,250  > $13,215  Adjusted Operating Earnings as of January 27, 2026    > $1,577  > $4,750  > $6,250  > $12,577            (1) Optum Health includes $405 million of 2026 operating earnings related to the net change in loss contracts reserve, which will be excluded from adjusted operating earnings and  adjusted earnings per share.

EXPECTED:

metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=18.45, high_value=18.95, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=19.50, high_value=20.00, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### UNH-2026-07-16-8-K-0000731766-26-000191-earningsrelease2q26_7152.htm-p6 — FIELD_MISMATCH (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000191
Form: 8-K
Document: earningsrelease2q26_7152.htm
Locator: paragraph-6
Probable root cause: numeric extraction mismatch

INPUT:

Page 2 of 17  • Optum supported more than 120 million consumers and generated revenues of $65.7 billion and earnings of $4.0 billion,  representing 160 basis points of margin expansion year-over-year.     UnitedHealth Group Updated 2026 Full Year Guidance  ($ in millions, except per share data)        Reported Operating  Earnings   Adjusted Operating  Earnings    UnitedHealthcare     > $12,000  > $12,000   Optum Health     > $2,275  > $2,215   Optum Insight     > $4,925  > $4,750   Optum Rx     > $6,250  > $6,250   Optum (a)    > $13,450  > $13,215   UnitedHealth Group     > $25,450  > $25,215   Medical Care Ratio      88.1% ± 25 bps   Tax Rate      ~18.5%   Cash Flows from Operations      ~$24,000   Share Repurchase      At Least $5,000   Net Earnings to UNH Shareholders      > $16,750       Diluted   Adjusted (b)   Net Earnings per Share    $18.45 - $18.95  $19.50 - $20.00     (a) Refer to page 7 of this release for a bridge of Optum Reported to Adjusted Operating Earnings.  (b) Refer to page 16 of this release for a reconciliation of the non-GAAP measure.

EXPECTED:

metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=18.45, high_value=18.95, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=19.50, high_value=20.00, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=18.45, high_value=18.95, point_value=None, unit=None, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### UNH-2026-07-16-8-K-0000731766-26-000191-uhgearnings_q22026vpower.htm-p10 — FALSE_NEGATIVE (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000191
Form: 8-K
Document: uhgearnings_q22026vpower.htm
Locator: paragraph-10
Probable root cause: number format unsupported

INPUT:

© 2026 UnitedHealth Group, Inc. All rights reserved. 6 UnitedHealth Group 2026 Outlook (1) Optum Health includes $405 million of 2026 operating earnings related to the net change in loss contracts reserve, which will be excluded from  adjusted operating earnings and adjusted earnings per share. As of  July 16, 2026 As of  January 27, 2026 ($ and weighted-average shares in millions, except per share data) Operating Earnings > $12,000 > $10,800 UnitedHealthcare > $2,275 > $2,200Optum Health > $4,925 > $4,750Optum Insight > $6,250 > $6,250Optum Rx > $13,450 > $13,200 Optum > $25,450 > $24,000 UnitedHealth Group > $16,750> $15,600Net Earnings to UNH Shareholders $18.45 - $18.95> $17.10Diluted Net Earnings per Share to UNH Shareholders $19.50 - $20.00> $17.75Adjusted Earnings per Share (1) 88.1% ± 25 bps88.8% ± 50 bpsMedical Care Ratio ~18.5%~19.25%Tax Rate ~$24,000> $18,000Cash Flows from Operations At Least $5,000~$2,500Share Repurchase Optum 2026 Reported to Adjusted Earnings Bridge Total  Optum Optum  Rx Optum  Insight Optum  Health ($ in millions) > $13,450> $6,250> $4,925> $2,275 (1)2026 Reported Operating Earnings Guidance $170-$(175)$345Net Portfolio Divestitures, Restructuring and Other $(405)--$(405)Net Change in Third Party Loss Contracts > $13,215> $6,250> $4,750> $2,2152026 Adjusted Operating Earnings  > $12,577> $6,250> $4,750> $1,577Adjusted Operating Earnings as of January 27, 2026 Below outlines the 2026 Reported to Adjusted Earnings Bridge for Optum as of July 16, 2026. (1) Refer to page 16 of this release for a reconciliation of non-GAAP measures.

EXPECTED:

metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=18.45, high_value=18.95, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=19.50, high_value=20.00, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### UNH-2026-07-16-8-K-0000731766-26-000191-uhgearnings_q22026vpower.htm-p5 — FIELD_MISMATCH (HIGH)

Ticker: UNH
CIK: 0000731766
Accession: 0000731766-26-000191
Form: 8-K
Document: uhgearnings_q22026vpower.htm
Locator: paragraph-5
Probable root cause: numeric extraction mismatch

INPUT:

© 2026 UnitedHealth Group, Inc. All rights reserved. 1 Second Quarter 2026 Revenues of $112.0 Billion;  Earnings from Operations of $8.0 Billion  Earnings of $6.04 Per Share and Adjusted Earnings  of $6.38 Per Share Our results and outlook reflect the  continuing progress in our work to  simplify how we operate, improve  both affordability and the health  care experience for patients and  care providers and apply modern  technology to create real  improvement for people.” Stephen Hemsley Chief Executive Officer, UnitedHealth Group July 16, 2026 UnitedHealth Group (NYSE: UNH) today reported second quarter 2026  results and raised guidance for full year 2026. The company now expects full year 2026 adjusted net earnings between  $19.50 to $20.00 per share resulting from performance year-to-date and  an improved outlook for the remainder of the year. A table outlining the  company’s updated outlook is below, with additional detail on page 6 of this  release.  Consolidated revenues for the second quarter 2026 were $112.0 billion and  earnings from operations were $8.0 billion, with a net margin of 4.9%. Cash  flows from operations were $11.1 billion, or 1.9x net income, and the debt- to-capital ratio was 41.2% as of June 30, 2026. UnitedHealth Group’s medical cost ratio was 86.7% for the second quarter  2026, reflecting cost and pricing discipline, as well as mix changes across  all benefit offerings. The operating cost ratio of 12.7% in the second quarter  2026 compared to 12.3% in the second quarter 2025, reflecting targeted  investments in technology, operations and the community.  Over the last year, the company has advanced a broad set of reforms and  commitments to improve affordability, transparency and simplicity for care  providers and consumers. These actions reflect the company’s deep  commitment to helping people live healthier lives and helping make the  health system work better for everyone. These actions are outlined in more  detail on page 2 of this release. UnitedHealth Group Reports Second Quarter 2026 Results Second Quarter 2026  Key Performance Metrics • Second quarter 2026 adjusted net  earnings were $6.38 per share. • The medical care ratio was 86.7%  and reflected product design changes,  improved medical management and  better aligned pricing. MCR was  affected by $860 million of net  favorable prior period development,  with the majority related to 2026 dates  of service.  • The operating cost ratio of 12.7%  included targeted investments in  infrastructure, artificial intelligence,  care delivery enhancements, consumer  experience and community support.  • UnitedHealthcare served 48.5 million  consumers and reported revenues  of $86.0 billion and earnings of  $3.9 billion, with operating margins  of 4.6%. • Optum supported more than 120  million consumers and generated  revenues of $65.7 billion and  earnings of $4.0 billion, representing  160 basis points of margin expansion  year-over-year.   Updated Full Year 2026 Earnings Outlook Range  to $18.45 to $18.95 Per Share Adjusted Earnings Range of $19.50 to  $20.00 Per Share (a) Refer to page 6 of this release for a bridge of Optum Reported to Adjusted Operating Earnings. (b) Refer to page 16 of this release for a reconciliation of the non-GAAP measure. UnitedHealth Group Updated 2026 Full Year Guidance  Adjusted  Operating Earnings Reported  Operating Earnings($ in millions, except per share data) > $12,000> $12,000 UnitedHealthcare > $2,215 > $2,275 Optum Health > $4,750 > $4,925 Optum Insight > $6,250 > $6,250 Optum Rx > $13,215> $13,450 Optum (a) > $25,215> $25,450UnitedHealth Group 88.1% ± 25 bpsMedical Care Ratio ~18.5%Tax Rate ~$24,000Cash Flows from Operations At Least $5,000Share Repurchase > $16,750Net Earnings to UNH Shareholders Adjusted (b)Diluted  $19.50 - $20.00$18.45 - $18.95Net Earnings per Share

EXPECTED:

metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=18.45, high_value=18.95, point_value=None, unit=PER_SHARE, currency=USD, management_claim=RAISED; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=19.50, high_value=20.00, point_value=None, unit=PER_SHARE, currency=USD, management_claim=RAISED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=19.5, high_value=20, point_value=None, unit=None, currency=None, management_claim=RAISED

RESULT: FAIL

### WMT-2025-11-20-8-K-0000104169-25-000177-earningspresentationfy26.htm-p6 — FIELD_MISMATCH (HIGH)

Ticker: WMT
CIK: 0000104169
Accession: 0000104169-25-000177
Form: 8-K
Document: earningspresentationfy26.htm
Locator: paragraph-6
Probable root cause: numeric extraction mismatch

INPUT:

Fiscal year 2026 The Company’s fiscal year guidance is based on the following FY25 figures: Net sales: $674.5 billion, adjusted  operating income1: $29.5 billion, and adjusted EPS1: $2.51. Consolidated metric Original from 2.20.2025 As of 8.21.2025 As of 11.20.25 Net sales (cc) Increase 3.0% to 4.0% • Including approximately 20 bps  headwind from lapping leap year • Including approximately 20 bps  tailwind from acquisition of VIZIO Increase 3.75% to 4.75% Increase 4.8% to 5.1% Adj. operating income (cc) Increase 3.5% to 5.5% • Including approximately 70 bps  headwind from lapping leap year • Including approximately 80 bps  headwind from acquisition of VIZIO Unchanged Increase 4.8% to 5.5% Interest, net Increase approximately $100M to $200M Unchanged Unchanged Effective tax rate Approximately 23.5% to 24.5% Unchanged Mid to low-end of prior range Non-controlling interest Relatively flat Unchanged Unchanged Adjusted EPS $2.50 to $2.60, including approximately  $0.05 headwind from currency $2.52 to $2.62, including $0.02  to $0.03 headwind from  currency $2.58 to $2.63, including  $0.01 to $0.02 headwind  from currency Capital expenditures Approximately 3.0% to 3.5% of net sales Unchanged Approximately 3.5% 1 For relevant non-GAAP reconciliations, see Q4 FY25 earnings release furnished on Form 8-K on February 20, 2025. cc = constant currency Guidance 2 The following forward-looking statements reflect  the Company’s expectations as of November 20,  2025, and are subject to substantial uncertainty.  The Company’s results may be materially affected  by many factors, such as fluctuations in foreign  currency exchange rates, changes in global  economic and geopolitical conditions, tariff and  trade policies, customer demand and spending,  inflation, interest rates, world events, expenses  pertaining to general liability claims, for which we  self-insure, and the various other factors detailed  in this presentation. Additionally, guidance is  provided on a non-GAAP basis as the Company  cannot predict certain elements that are included  in reported GAAP results, such as the changes in  fair value of the Company’s equity and other  investments. Growth rates reflect an adjusted  basis for prior year results.

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=4.8, high_value=5.1, point_value=None, unit=%, currency=None, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=2.58, high_value=2.63, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=3, high_value=4, point_value=None, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### WMT-2025-11-20-8-K-0000104169-25-000177-earningsreleasefy26q3.htm-p25 — FIELD_MISMATCH (MEDIUM)

Ticker: WMT
CIK: 0000104169
Accession: 0000104169-25-000177
Form: 8-K
Document: earningsreleasefy26q3.htm
Locator: paragraph-25
Probable root cause: management action mismatch

INPUT:

up 4.5%, with strength across categories. For fiscal year 2026, the Company raises outlook for growth in net sales to 4.8% to 5.1% and adjusted operating income to 4.8% to 5.5%, both in constant currency (“cc”)

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=4.8, high_value=5.1, point_value=None, unit=%, currency=None, management_claim=RAISED

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=4.8, high_value=5.1, point_value=None, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### WMT-2026-02-19-8-K-0000104169-26-000032-earningspresentationfy26.htm-p6 — FIELD_MISMATCH (HIGH)

Ticker: WMT
CIK: 0000104169
Accession: 0000104169-26-000032
Form: 8-K
Document: earningspresentationfy26.htm
Locator: paragraph-6
Probable root cause: numeric extraction mismatch

INPUT:

First quarter The Company’s first quarter fiscal 2027 guidance is based on the following Q1 FY26 figures: Net sales: $164.0  billion, operating income: $7.1 billion, and adjusted EPS1: $0.61. Consolidated metric Q1 FY27 Net sales (cc) Increase 3.5% to 4.5% Operating income (cc) Increase 4.0% to 6.0% Adjusted EPS $0.63 to $0.65 Fiscal year 2027 The Company’s fiscal year guidance is based on the following FY26 figures: Net sales: $706.4 billion, adjusted  operating income2: $31.0 billion, and adjusted EPS2: $2.64. Consolidated metric FY27 Net sales (cc) Increase 3.5% to 4.5% Adj. operating income (cc) Increase 6.0% to 8.0% Interest, net Increase approximately $200M to $300M Effective tax rate Approximately 23.5% to 24.5% Adjusted EPS $2.75 to $2.85 Capital expenditures Approximately 3.5% of net sales 1 For relevant non-GAAP reconciliations, see Q1 FY26 earnings release furnished on Form 8-K on May 15, 2025. 2See additional information at the end of this presentation regarding non-GAAP financial measures. cc = constant currency Guidance 2 The following forward-looking statements reflect  the Company’s expectations as of February 19,  2026, and are subject to substantial uncertainty.  The Company’s results may be materially affected  by many factors, such as fluctuations in foreign  currency exchange rates, changes in global  economic and geopolitical conditions, tariff and  trade policies, customer demand and spending,  inflation, interest rates, world events,  and the  various other factors detailed in this presentation.  Additionally, guidance is provided on a non-GAAP  basis as the Company cannot predict certain  elements that are included in reported GAAP  results, such as the changes in fair value of the  Company’s equity and other investments. Growth  rates reflect an adjusted basis for prior year  results.

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=3.5, high_value=4.5, point_value=None, unit=%, currency=None, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_QUARTER, low_value=0.63, high_value=0.65, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=3.5, high_value=4.5, point_value=None, unit=%, currency=None, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=2.75, high_value=2.85, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=3.5, high_value=4.5, point_value=None, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

### WMT-2026-05-21-8-K-0000104169-26-000095-earningspresentationfy27.htm-p6 — FALSE_NEGATIVE (HIGH)

Ticker: WMT
CIK: 0000104169
Accession: 0000104169-26-000095
Form: 8-K
Document: earningspresentationfy27.htm
Locator: paragraph-6
Probable root cause: unsupported keyword or action synonym

INPUT:

Second quarter The Company’s second quarter fiscal 2027 guidance is based on the following Q2 FY26 figures: Net sales:  $175.8 billion, adjusted operating income1: $7.9 billion, and adjusted EPS1: $0.68. Consolidated metric Q2 FY27 Net sales (cc) Increase 4.0% to 5.0% Operating income (cc) Increase 7.0% to 10.0% Adjusted EPS $0.72 to $0.74 Fiscal year 2027 The Company’s fiscal year guidance is based on the following FY26 figures: Net sales: $706.4 billion,  adjusted operating income1: $31.0 billion, and adjusted EPS1: $2.64. Consolidated metric Original from 2.19.2026 As of 5.21.2026 Net sales (cc) Increase 3.5% to 4.5% Unchanged Adj. operating income (cc) Increase 6.0% to 8.0% Unchanged Interest, net Increase approximately $200M to $300M Unchanged Effective tax rate Approximately 23.5% to 24.5% Unchanged Adjusted EPS $2.75 to $2.85 Unchanged Capital expenditures Approximately 3.5% of net sales Unchanged 1 For relevant non-GAAP reconciliations, see Q2 FY26 and Q4 FY26 earnings releases furnished on Form 8-K on August 21, 2025 and February 19, 2026, respectively. cc = constant currency Guidance 2 The following guidance reflects the Company’s  expectations as of May 21, 2026, and does not  assume any impact from IEEPA tariff refunds. This  guidance is subject to substantial risk and  uncertainty that could cause actual results to  differ materially from these expectations. These  risks and uncertainties include, but are not limited  to, the factors set forth below under the heading  Forward-looking statements. Additionally,  guidance is provided on a non-GAAP basis as the  Company cannot predict certain elements that are  included in reported GAAP results, such as the  changes in fair value of the Company’s equity and  other investments. Growth rates reflect an  adjusted basis for prior year results.

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=4.0, high_value=5.0, point_value=None, unit=%, currency=None, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_QUARTER, low_value=0.72, high_value=0.74, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=3.5, high_value=4.5, point_value=None, unit=%, currency=None, management_claim=MAINTAINED; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=2.75, high_value=2.85, point_value=None, unit=PER_SHARE, currency=USD, management_claim=MAINTAINED

ACTUAL:

<none>

RESULT: FAIL

### WMT-2026-05-21-8-K-0000104169-26-000095-earningsreleasefy27q1.htm-p25 — FIELD_MISMATCH (MEDIUM)

Ticker: WMT
CIK: 0000104169
Accession: 0000104169-26-000095
Form: 8-K
Document: earningsreleasefy27q1.htm
Locator: paragraph-25
Probable root cause: management action mismatch

INPUT:

grew 4.1%. Operating income up 5.0%, negatively affected by 250 bps from higher fuel costs in distribution and fulfillment. Looking ahead, the Company issues guidance for the second quarter with net sales expected to grow 4% to 5% and adjusted operating income to grow 7% to 10%, both in constant currency (“cc”)

EXPECTED:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=4, high_value=5, point_value=None, unit=%, currency=None, management_claim=INITIATED

ACTUAL:

metric=REVENUE, period_label=CURRENT_QUARTER, low_value=4, high_value=5, point_value=None, unit=%, currency=None, management_claim=UNKNOWN

RESULT: FAIL

## Human-readable proof samples

### AAPL-2025-10-31-10-K-0000320193-25-000079-aapl-20250927.htm-p199

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-25-000079
Form: 10-K
Document: aapl-20250927.htm
Locator: paragraph-199

INPUT:

This Annual Report on Form 10-K (“Form 10-K”) contains forward-looking statements, within the meaning of the Private Securities Litigation Reform Act of 1995, that involve risks and uncertainties. Many of the forward-looking statements are located in Part I, Item 1 of this Form 10-K under the heading “Business” and Part II, Item 7 of this Form 10-K under the heading “Management’s Discussion and Analysis of Financial Condition and Results of Operations.” Forward-looking statements provide current expectations of future events based on certain assumptions and include any statement that does not directly relate to any historical or current fact. For example, statements in this Form 10-K regarding the potential future impact of macroeconomic conditions and tariffs and other measures on the Company’s business and results of operations are forward-looking statements. Forward-looking statements can also be identified by words such as “future,” “anticipates,” “believes,” “estimates,” “expects,” “intends,” “plans,” “predicts,” “will,” “would,” “could,” “can,” “may,” and similar terms. Forward-looking statements are not guarantees of future performance and the Company’s actual results may differ significantly from the results discussed in the forward-looking statements. Factors that might cause such differences include, but are not limited to, those discussed in Part I, Item 1A of this Form 10-K under the heading “Risk Factors.” The Company assumes no obligation to revise or update any forward-looking statements for any reason, except as required by law.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2025-10-31-10-K-0000320193-25-000079-aapl-20250927.htm-p310

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-25-000079
Form: 10-K
Document: aapl-20250927.htm
Locator: paragraph-310

INPUT:

The Company is focused on expanding its market opportunities related to smartphones, personal computers, tablets, wearables and accessories, and services. The Company’s products and services face substantial competition from companies that have significant technical, marketing, distribution and other resources, as well as established hardware, software, and service offerings with large customer bases. In addition, the Company faces significant competition as competitors imitate the Company’s product features and applications within their products to offer more competitive solutions. The Company also expects competition to intensify as competitors imitate the Company’s approach to providing components seamlessly within their offerings or work collaboratively to offer integrated solutions. Some of the Company’s competitors have broad product lines, low-priced products, large installed bases of active devices, and large customer bases. Competition has been particularly intense as competitors have aggressively cut prices and lowered product margins. Certain competitors have the resources, experience or cost structures to provide products and services at little or no profit or even at a loss. The Company has a minority market share in the global smartphone, personal computer, tablet and wearables markets, and some of the markets in which the Company competes have from time to time experienced little to no growth or contracted overall.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2025-10-31-10-K-0000320193-25-000079-aapl-20250927.htm-p355

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-25-000079
Form: 10-K
Document: aapl-20250927.htm
Locator: paragraph-355

INPUT:

The Company’s products and services face substantial competition from companies that have significant technical, marketing, distribution and other resources, as well as established hardware, software and service offerings. In addition, the Company faces significant competition as competitors imitate the Company’s product features and applications within their products to offer more competitive solutions. The Company also expects competition to intensify as competitors imitate the Company’s approach to providing components seamlessly within their offerings or work collaboratively to offer integrated solutions. Some of the Company’s competitors have broad product lines, low-priced products, large installed bases of active devices, and large customer bases. Competition has been particularly intense as competitors have aggressively cut prices and lowered product margins. Certain competitors have the resources, experience or cost structures to provide products and services at little or no profit or even at a loss. The Company has a minority market share in the global smartphone, personal computer, tablet and wearables markets, and some of the markets in which the Company competes have from time to time experienced little to no growth or contracted overall.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2025-10-31-10-K-0000320193-25-000079-aapl-20250927.htm-p360

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-25-000079
Form: 10-K
Document: aapl-20250927.htm
Locator: paragraph-360

INPUT:

Due to the highly volatile and competitive nature of the markets and industries in which the Company competes, the Company must continually introduce new products, services and technologies, enhance existing products and services, effectively stimulate customer demand for new and upgraded products and services, navigate global regulatory requirements and barriers to market access, and successfully manage the transition to these new and upgraded products and services. The success of new product and service introductions depends on a number of factors, including the Company’s ability to recruit and retain highly skilled personnel to execute on its strategic initiatives, and the timely and successful development and market acceptance of new products, services and technologies. Success also relies on the Company’s ability to manage the risks associated with new technologies and production ramp-up issues, the effective integration of third-party services and technologies into the Company’s products and services, the availability, delivery and performance of application software or other third-party support for the Company’s products and services, the effective management of manufacturing and other purchase commitments and the management of inventory levels in line with anticipated product demand, and the availability of products in appropriate quantities and at expected costs to meet anticipated demand. Additionally, quality issues or other defects or deficiencies can adversely affect the success of new product and service introductions and market acceptance. New products, services and technologies may replace or supersede existing offerings and may produce lower revenues and lower profit margins. The Company may not be able to successfully manage future introductions and transitions of products and services, which can materially adversely affect the Company’s business, reputation, results of operations, financial condition and stock price.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2025-10-31-10-K-0000320193-25-000079-aapl-20250927.htm-p399

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-25-000079
Form: 10-K
Document: aapl-20250927.htm
Locator: paragraph-399

INPUT:

The Company experiences malicious attacks and other attempts to gain unauthorized access to its systems on a regular basis. These attacks target the confidentiality, integrity or availability of confidential information and may disrupt normal business operations. Attacks can impair the Company’s ability to attract and retain customers for its products and services, affect its stock price, damage commercial relationships, and expose the Company to litigation or government investigations, potentially resulting in penalties, fines or judgments. Globally, attacks are expected to continue accelerating in both frequency and sophistication with increasing use by actors of tools and techniques that are designed to circumvent controls, avoid detection, and remove or obfuscate forensic evidence, all of which hinders the Company’s ability to identify, investigate and recover from incidents. In addition, attacks against the Company and its customers can escalate during periods of geopolitical tensions or conflict.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2026-01-29-8-K-0000320193-26-000005-a8-kex991q1202612272025.htm-p17

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-26-000005
Form: 8-K
Document: a8-kex991q1202612272025.htm
Locator: paragraph-17

INPUT:

This press release contains forward-looking statements, within the meaning of the Private Securities Litigation Reform Act of 1995. These forward-looking statements include without limitation those about payment of the Company’s quarterly dividend and future business plans. These statements involve risks and uncertainties, and actual results may differ materially from any future results expressed or implied by the forward-looking statements. Risks and uncertainties include without limitation: effects of global and regional economic conditions, including as a result of government policies, trade and other international disputes, geopolitical tensions, conflict, terrorism, natural disasters, and public health issues; risks relating to the design, manufacture, introduction, and transition of products and services in highly competitive and rapidly changing markets, including from reliance on third parties for components, technology, manufacturing, applications, services, support, and content; risks relating to information technology system failures, network disruptions, and failure to protect, loss of, or unauthorized access to, or release of, data; and effects of unfavorable legal proceedings, government investigations, and complex and changing laws and regulations. More information on these risks and other potential factors that could affect the Company’s business, reputation, results of operations, financial condition, and stock price is included in the Company’s filings with the SEC, including in the “Risk Factors” and “Management’s Discussion and Analysis of Financial Condition and Results of Operations” sections of the Company’s most recently filed periodic reports on Form 10-K and Form 10-Q and subsequent filings. The Company assumes no obligation to update any forward-looking statements, which speak only as of the date they are made.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2026-04-30-8-K-0000320193-26-000011-a8-kex991q2202603282026.htm-p17

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-26-000011
Form: 8-K
Document: a8-kex991q2202603282026.htm
Locator: paragraph-17

INPUT:

This press release contains forward-looking statements, within the meaning of the Private Securities Litigation Reform Act of 1995. These forward-looking statements include without limitation those about the Company’s plan for return of capital, payment of the Company’s quarterly dividend and future business plans. These statements involve risks and uncertainties, and actual results may differ materially from any future results expressed or implied by the forward-looking statements. Risks and uncertainties include without limitation: effects of global and regional economic conditions, including as a result of government policies, trade and other international disputes, geopolitical tensions, conflict, terrorism, natural disasters, and public health issues; risks relating to the design, manufacture, introduction, and transition of products and services in highly competitive and rapidly changing markets, including from reliance on third parties for components, technology, manufacturing, applications, services, support, and content; risks relating to information technology system failures, network disruptions, and failure to protect, loss of, or unauthorized access to, or release of, data; and effects of unfavorable legal proceedings, government investigations, and complex and changing laws and regulations. More information on these risks and other potential factors that could affect the Company’s business, reputation, results of operations, financial condition, and stock price is included in the Company’s filings with the SEC, including in the “Risk Factors” and “Management’s Discussion and Analysis of Financial Condition and Results of Operations” sections of the Company’s most recently filed periodic reports on Form 10-K and Form 10-Q and subsequent filings. The Company assumes no obligation to update any forward-looking statements, which speak only as of the date they are made.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2026-05-01-10-Q-0000320193-26-000013-aapl-20260328.htm-p2069

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-26-000013
Form: 10-Q
Document: aapl-20260328.htm
Locator: paragraph-2069

INPUT:

This Item and other sections of this Quarterly Report on Form 10-Q (“Form 10-Q”) contain forward-looking statements, within the meaning of the Private Securities Litigation Reform Act of 1995, that involve risks and uncertainties. Forward-looking statements provide current expectations of future events based on certain assumptions and include any statement that does not directly relate to any historical or current fact. For example, statements in this Form 10-Q regarding the potential future impact of macroeconomic conditions and tariffs and other measures on the Company’s business and results of operations are forward-looking statements

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2026-05-01-10-Q-0000320193-26-000013-aapl-20260328.htm-p2071

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-26-000013
Form: 10-Q
Document: aapl-20260328.htm
Locator: paragraph-2071

INPUT:

Forward-looking statements can also be identified by words such as “future,” “anticipates,” “believes,” “estimates,” “expects,” “intends,” “plans,” “predicts,” “will,” “would,” “could,” “can,” “may,” and similar terms. Forward-looking statements are not guarantees of future performance and the Company’s actual results may differ significantly from the results discussed in the forward-looking statements. Factors that might cause such differences include, but are not limited to, those discussed in Part I, Item 1A of the 2025 Form 10-K and Part II, Item 1A of this Form 10-Q, in each case under the heading “Risk Factors.” The Company assumes no obligation to revise or update any forward-looking statements for any reason, except as required by law.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2026-07-30-8-K-0000320193-26-000018-a8-kex991q3202606272026.htm-p17

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-26-000018
Form: 8-K
Document: a8-kex991q3202606272026.htm
Locator: paragraph-17

INPUT:

This press release contains forward-looking statements, within the meaning of the Private Securities Litigation Reform Act of 1995. These forward-looking statements include without limitation those about payment of the Company’s quarterly dividend and future business plans. These statements involve risks and uncertainties, and actual results may differ materially from any future results expressed or implied by the forward-looking statements. Risks and uncertainties include without limitation: effects of global and regional economic conditions, including as a result of government policies, trade and other international disputes, geopolitical tensions, conflict, terrorism, natural disasters, and public health issues; risks relating to the design, manufacture, introduction, and transition of products and services in highly competitive and rapidly changing markets, including from reliance on third parties for components, technology, manufacturing, applications, services, support, and content; risks relating to information technology system failures, network disruptions, and failure to protect, loss of, or unauthorized access to, or release of, data; and effects of unfavorable legal proceedings, government investigations, and complex and changing laws and regulations. More information on these risks and other potential factors that could affect the Company’s business, reputation, results of operations, financial condition, and stock price is included in the Company’s filings with the SEC, including in the “Risk Factors” and “Management’s Discussion and Analysis of Financial Condition and Results of Operations” sections of the Company’s most recently filed periodic reports on Form 10-K and Form 10-Q and subsequent filings. The Company assumes no obligation to update any forward-looking statements, which speak only as of the date they are made.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2026-07-31-10-Q-0000320193-26-000020-aapl-20260627.htm-p2076

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-26-000020
Form: 10-Q
Document: aapl-20260627.htm
Locator: paragraph-2076

INPUT:

This Item and other sections of this Quarterly Report on Form 10-Q (“Form 10-Q”) contain forward-looking statements, within the meaning of the Private Securities Litigation Reform Act of 1995, that involve risks and uncertainties. Forward-looking statements provide current expectations of future events based on certain assumptions and include any statement that does not directly relate to any historical or current fact. For example, statements in this Form 10-Q regarding the potential future impact of macroeconomic conditions and tariffs and other measures on the Company’s business and results of operations are forward-looking statements

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### AAPL-2026-07-31-10-Q-0000320193-26-000020-aapl-20260627.htm-p2078

Ticker: AAPL
CIK: 0000320193
Accession: 0000320193-26-000020
Form: 10-Q
Document: aapl-20260627.htm
Locator: paragraph-2078

INPUT:

Forward-looking statements can also be identified by words such as “future,” “anticipates,” “believes,” “estimates,” “expects,” “intends,” “plans,” “predicts,” “will,” “would,” “could,” “can,” “may,” and similar terms. Forward-looking statements are not guarantees of future performance and the Company’s actual results may differ significantly from the results discussed in the forward-looking statements. Factors that might cause such differences include, but are not limited to, those discussed in Part I, Item 1A of the 2025 Form 10-K and Part II, Item 1A of this Form 10-Q, in each case under the heading “Risk Factors.” The Company assumes no obligation to revise or update any forward-looking statements for any reason, except as required by law.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### ADBE-2025-12-10-8-K-0000796343-25-000135-adbeex991q425.htm-p116

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-25-000135
Form: 8-K
Document: adbeex991q425.htm
Locator: paragraph-116

INPUT:

In addition to historical information, this press release contains “forward-looking statements” within the meaning of applicable securities laws, including statements related to our business, strategy, artificial intelligence (“AI”) and innovation momentum; our market and AI opportunity and future growth; market and AI trends; current macroeconomic conditions; fluctuations in foreign currency exchange rates; strategic investments; customer success and groups; expectations regarding acquisitions and other business transactions; and our financial targets and assumptions related thereto, including revenue, operating margin, operating efficiencies, annualized recurring revenue, tax rate, earnings per share and share count. Each of the forward-looking statements we make in this press release involves risks, uncertainties and assumptions based on information available to us as of the date of this press release. Such risks and uncertainties, many of which relate to matters beyond our control, could cause actual results to differ materially from these forward-looking statements. Factors that might cause or contribute to such differences include, but are not limited to: failure to innovate effectively and meet customer needs; issues relating to development and use of AI; failure to compete effectively; damage to our reputation or brands; failure to realize the anticipated benefits of investments or acquisitions; service interruptions or failures in information technology systems by us or third parties; security incidents; failure to effectively develop, manage and maintain critical third-party business relationships; risks associated with being a multinational corporation and adverse macroeconomic conditions; complex sales cycles; failure to recruit and retain key personnel; litigation, regulatory inquiries and intellectual property infringement claims; changes in, and compliance with, global laws and regulations, including those related to information security and privacy; failure to protect our intellectual property; changes in tax regulations; complex government procurement processes; risks related to fluctuations in or the timing of revenue recognition from our subscription offerings; fluctuations in foreign currency exchange rates; impairment charges; our existing and future debt obligations; catastrophic events; and fluctuations in our stock price. Further information on these and other factors are discussed in the section titled “Risk Factors” in Adobe’s most recently filed Annual Report on Form 10-K and Adobe's most recently filed Quarterly Reports on Form 10-Q. The risks described in this press release and in Adobe’s filings with the U.S. Securities and Exchange Commission should be carefully reviewed.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### ADBE-2025-12-10-8-K-0000796343-25-000135-adbeex991q425.htm-pp75-88

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-25-000135
Form: 8-K
Document: adbeex991q425.htm
Locator: paragraphs-75-88

INPUT:

The following table summarizes Adobe’s FY2026 targets

1

:

Total revenue

$25.90 billion to $26.10 billion

Business Professionals & Consumers subscription revenue

$7.35 billion to $7.40 billion

Creative & Marketing Professionals subscription revenue

$17.75 billion to $17.90 billion

Total Adobe ending ARR growth

10.2% year over year

Earnings per share

GAAP: $17.90 to $18.10

Non-GAAP: $23.30 to $23.50

EXPECTED:

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=25.90, high_value=26.10, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=7.35, high_value=7.40, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=17.75, high_value=17.90, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=17.90, high_value=18.10, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=23.30, high_value=23.50, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

ACTUAL:

<none>

RESULT: FAIL

### ADBE-2026-01-15-10-K-0000796343-26-000003-adbe-20251128.htm-p180

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000003
Form: 10-K
Document: adbe-20251128.htm
Locator: paragraph-180

INPUT:

In addition to historical information, this Annual Report on Form 10-K contains “forward-looking statements” within the meaning of applicable securities laws, including statements related to our product development plans and new or enhanced offerings; our business, strategy, artificial intelligence (“AI”) and innovation momentum; our market and AI opportunity and future growth; market and AI trends; macroeconomic conditions; fluctuations in foreign currency exchange rates; strategic investments; customer success and groups; and industry positioning. In addition, when used in this report, the words “will,” “expects,” “could,” “would,” “may,” “anticipates,” “intends,” “plans,” “believes,” “seeks,” “targets,” “estimates,” “looks for,” “looks to,” “continues” and similar expressions, as well as statements regarding our focus for the future, are generally intended to identify forward-looking statements. Each of the forward-looking statements we make in this report involves risks, uncertainties and assumptions based on information available to us as of the date of this report. Such risks and uncertainties, many of which relate to matters beyond our control, could cause actual results to differ materially and adversely from these forward-looking statements. Factors that might cause or contribute to such differences include, but are not limited to, those discussed in the section titled “Risk Factors” in Part I, Item 1A of this report and elsewhere herein. The risks described herein and in Adobe’s other filings with the U.S. Securities and Exchange Commission (the “SEC”), including our Quarterly Reports on Form 10-Q to be filed in fiscal 2026, should be carefully reviewed. Undue reliance should not be placed on the forward-looking financial information set forth in this report, which reflects estimates based on information available as of the date of this report. Adobe assumes no obligation to, and does not currently intend to, update these forward-looking statements.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### ADBE-2026-01-15-10-K-0000796343-26-000003-adbe-20251128.htm-p186

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000003
Form: 10-K
Document: adbe-20251128.htm
Locator: paragraph-186

INPUT:

Our focus revolves around serving our customer audiences: business professionals, consumers, creators, creative professionals and marketing professionals. The massive opportunity and evolving role of creativity across roles and industries have driven Adobe’s growth over the past four decades and are expected to continue to drive our growth going forward as we evolve our solutions and routes to market to anticipate the growing needs of our customers. In the artificial intelligence (“AI”) era, we are harnessing the power of AI across our solutions by bringing together our commercially safe first-party and leading partner AI models best suited for the job; deploying conversational and agentic capabilities across offerings; ensuring ubiquity on all surfaces; delivering trusted and secure solutions; and expanding our global presence.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### ADBE-2026-01-15-10-K-0000796343-26-000003-adbe-20251128.htm-p334

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000003
Form: 10-K
Document: adbe-20251128.htm
Locator: paragraph-334

INPUT:

Consulting services are made available to customers through subscription offerings, providing our customers with ongoing access to our trained service professionals over the contract term for the consulting subscription offering, or on a project basis through either time-and-materials or fixed-price services offerings.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### ADBE-2026-01-15-10-K-0000796343-26-000003-adbe-20251128.htm-p436

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000003
Form: 10-K
Document: adbe-20251128.htm
Locator: paragraph-436

INPUT:

As previously discussed, our actual results could differ materially from our forward-looking statements. Below we discuss some of the factors that could cause these differences. The occurrence of these and many other factors described in this report, and factors that we do not presently know or that we currently believe to be immaterial, could materially and adversely affect our operations, performance and financial condition. Many factors affect more than one category and the factors are not in order of significance or probability of occurrence because they have been grouped by categories.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### ADBE-2026-01-15-10-K-0000796343-26-000003-adbe-20251128.htm-p439

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000003
Form: 10-K
Document: adbe-20251128.htm
Locator: paragraph-439

INPUT:

We operate in rapidly evolving industries and expect the pace of innovation to continue to accelerate. We must continually introduce new and enhance existing solutions to retain customers and attract new customers. Developing new solutions is complex, requires significant investment and operational costs and may not be profitable, and our investments in new technologies are speculative and may not yield the expected business or financial benefits. The commercial success of new or enhanced solutions depends on a number of factors, including timely and successful development; effective distribution and marketing; market acceptance; compatibility with existing and emerging standards, platforms, software delivery methods and technologies; accurately predicting and anticipating customer needs and expectations and the direction of technological change; identifying and innovating in the right technologies; and differentiation from other solutions. If we fail to anticipate or identify technological, creative, productivity or marketing trends or fail to devote appropriate resources to adapt to such trends, our business could be harmed. For example, artificial intelligence (“AI”), including generative and agentic, enables users of all skill levels to create and provide new ways of marketing, creating and editing content and interacting with documents. While we continue to release new AI solutions and to focus on enhancing the AI capabilities of our solutions and incorporating AI across existing solutions, there can be no assurance that our new or enhanced solutions and AI innovations will be successful, adopted or monetizable or that we will innovate effectively to keep pace with the rapid evolution of AI across our solutions. If we do not successfully innovate, adapt to rapid technological or industry changes and meet customer needs, our business and our financial results may be materially harmed.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

### ADBE-2026-01-15-10-K-0000796343-26-000003-adbe-20251128.htm-p441

Ticker: ADBE
CIK: 0000796343
Accession: 0000796343-26-000003
Form: 10-K
Document: adbe-20251128.htm
Locator: paragraph-441

INPUT:

The markets for our solutions are rapidly evolving and intensely competitive. We expect competition to continue to intensify. Our numerous competitors include companies of various sizes and both public and private companies, including large, global companies and smaller companies with more specialized focuses, new entrants, and AI or cloud-native companies. Our competitors include companies with significant sales and research and development resources, broad brand awareness, long operating histories or access to large customer bases. Our competitors may deploy technical, marketing and financial resources more easily and effectively. Our competitors may develop or acquire additional products, services or solutions that are similar to ours or that achieve greater or faster acceptance. Our competitors may undertake faster and more far-reaching and successful development efforts or marketing campaigns, may adopt more aggressive pricing policies or may more effectively appeal to customers. As a result, current and potential customers may select the products, services or solutions of our competitors. New industry standards, evolving distribution and sales models, limited barriers to entry, short product life cycles, customer price sensitivity, global economic conditions and the frequent entry of new solutions or competitors may increase downward pressure on pricing and gross margins and adversely affect our renewal, upsell and cross-sell rates as well as our ability to attract new customers. In addition, we expect to face more competition as AI continues to advance and be integrated into the markets in which we compete and to change the software industry. Our competitors or other third parties may develop AI solutions more rapidly or successfully, including but not limited to different data training strategies or proprietary access to data and, as a result, other AI solutions may achieve greater and faster adoption. For example, we face increasing competition from companies offering generative and agentic AI solutions, including but not limited to prompt-based and multi-modal creation and editing, document productivity and understanding, ad distribution and creation, and purpose-built AI agents. Other companies have in the past, and may in the future prevent, limit or interfere with our ability to use third-party models in our solutions. If we are not able to provide solutions that compete effectively, we could experience reduced sales, which could materially and adversely impact our business and financial results.

EXPECTED:

<negative>

ACTUAL:

<none>

RESULT: PASS

## Final decision

**REAL SEC GUIDANCE EXTRACTOR NOT CERTIFIED**
