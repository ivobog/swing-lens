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
| TP | 33 |
| FP | 0 |
| FN | 0 |
| TN | 194 |
| precision | 1.000000 |
| recall | 1.000000 |
| specificity | 1.000000 |
| f1 | 1.000000 |

## Structured extraction

| Field | Exact-match |
|---|---:|
| Metric | 1.000000 |
| Period | 1.000000 |
| Numeric values | 1.000000 |
| Unit | 1.000000 |
| Currency | 1.000000 |
| Management claim | 1.000000 |
| Evidence locator | 1.000000 |
| All fields | 1.000000 |

## Failure severity

| Severity | Count |
|---|---:|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | 0 |

## Version

| Item | Value |
|---|---|
| Git SHA | 2438e6e17979dbfec4b41189fcba2e7a14570b87 |
| Processor signature | sec-guidance:eed017654682a0c9 |
| Parser version | sec-html-text-v1 |
| Extractor version | guidance-regex-visible-text-v3 |
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
| recall | 1.0 | >= 0.85 | PASS |
| numeric_values | 1.0 | >= 0.98 | PASS |
| metric | 1.0 | >= 0.98 | PASS |
| period_label | 1.0 | >= 0.95 | PASS |
| unit | 1.0 | >= 0.98 | PASS |
| currency | 1.0 | >= 0.98 | PASS |
| no_critical_false_positive | True | == True | PASS |

## Baseline versus candidate

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| Precision | 1.000000 | 1.000000 | +0.000000 |
| Recall | 0.515152 | 1.000000 | +0.484848 |
| False positives | 0 | 0 | +0 |
| False negatives | 16 | 0 | -16 |
| Structured exact match | 0.000000 | 1.000000 | +1.000000 |
| Metric accuracy | 0.211268 | 1.000000 | +0.788732 |
| Period accuracy | 0.225352 | 1.000000 | +0.774648 |
| Numeric accuracy | 0.154930 | 1.000000 | +0.845070 |
| Unit accuracy | 0.126761 | 1.000000 | +0.873239 |
| Currency accuracy | 0.056338 | 1.000000 | +0.943662 |
| Management-claim accuracy | 0.014085 | 1.000000 | +0.985915 |

## Failures

No scored failures.
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

metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=25.9, high_value=26.1, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=7.35, high_value=7.4, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=REVENUE, period_label=CURRENT_FISCAL_YEAR, low_value=17.75, high_value=17.9, point_value=None, unit=BILLION, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=17.9, high_value=18.1, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None; metric=EPS_DILUTED, period_label=CURRENT_FISCAL_YEAR, low_value=23.3, high_value=23.5, point_value=None, unit=PER_SHARE, currency=USD, management_claim=None

RESULT: PASS

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

**REAL SEC GUIDANCE EXTRACTOR CERTIFIED**
