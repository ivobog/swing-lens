# Real SEC Guidance Corpus Manifest

## Provenance

- Corpus: `tests/ceri/fixtures/sec_guidance_real_corpus_v1.jsonl`
- Corpus SHA-256: `31b4e721f7146e1b53935836a41dc38f05203cc8791b4b8dbd36624286cc1fc8`
- Source policy: real SEC filing and exhibit passages with accession, CIK, form, document name, source URL, source-document hash, and visible-text locator retained per case.
- Review policy: independent Q1-Q9 semantic annotation; extractor output was not used as gold.
- Scoring policy: `REVIEW_REQUIRED` cases are preserved but excluded from scored metrics.

## Coverage

| Metric | Value |
|---|---:|
| Issuers | 28 |
| Filings | 117 |
| Passages | 232 |
| Positive | 33 |
| Negative | 194 |
| Review required | 5 |
| Forms represented | 10-K, 10-Q, 8-K |
| Filing-date range | 2025-10-31 to 2026-08-10 |

## Versioned certification boundary

- Git SHA: `2438e6e17979dbfec4b41189fcba2e7a14570b87`
- Processor signature: `sec-guidance:eed017654682a0c9`
- Parser: `sec-html-text-v1`
- Extractor: `guidance-regex-visible-text-v3`
- Evidence locator: `paragraph-locator-v1`
- Filing selection: `guidance-forms-v1`
- Output fingerprint: `e7165fccfbdc6e731bcc26230ac31981f1c5fbeafad6604dcb995e8f04c2d806`

## Issuers

| Ticker | CIK | Company |
|---|---|---|
| AAPL | 0000320193 | Apple Inc. |
| ADBE | 0000796343 | ADOBE INC. |
| AMD | 0000002488 | ADVANCED MICRO DEVICES INC |
| BA | 0000012927 | BOEING CO |
| BAC | 0000070858 | BANK OF AMERICA CORP /DE/ |
| CAT | 0000018230 | CATERPILLAR INC |
| COST | 0000909832 | COSTCO WHOLESALE CORP /NEW |
| CRM | 0001108524 | Salesforce, Inc. |
| CVX | 0000093410 | CHEVRON CORP |
| DAL | 0000027904 | DELTA AIR LINES, INC. |
| FDX | 0001048911 | FEDEX CORP |
| GE | 0000040545 | GENERAL ELECTRIC CO |
| GS | 0000886982 | GOLDMAN SACHS GROUP INC |
| JNJ | 0000200406 | JOHNSON & JOHNSON |
| JPM | 0000019617 | JPMORGAN CHASE & CO |
| KO | 0000021344 | COCA COLA CO |
| META | 0001326801 | Meta Platforms, Inc. |
| MSFT | 0000789019 | MICROSOFT CORP |
| NKE | 0000320187 | NIKE, Inc. |
| NVDA | 0001045810 | NVIDIA CORP |
| ORCL | 0001341439 | ORACLE CORP |
| SBUX | 0000829224 | STARBUCKS CORP |
| T | 0000732717 | AT&T INC. |
| TGT | 0000027419 | TARGET CORP |
| UNH | 0000731766 | UNITEDHEALTH GROUP INC |
| VZ | 0000732712 | VERIZON COMMUNICATIONS INC |
| WMT | 0000104169 | Walmart Inc. |
| XOM | 0002115436 | ExxonMobil Holdings Corp |
