# RAG Corpus Manifest — Bilingual Voice Airline Arbitration Engine

Generated 2026-07-07. Updated 2026-07-08 (added FAA Final Rule narrative). This batch supplements the PDFs you already downloaded (PIA COC, Serene Air COC, AirBlue Terms & Conditions).

## Files in this batch (all real, scraped content — ready to chunk/embed)

| File | Category | Carrier/Regulator | Why it's here |
|---|---|---|---|
| `PIA_conditions_of_carry.md` | customer_refund, customer_baggage, operator_licensing | PIA (Pakistan) | Markdown transcription of the official PIA Conditions of Carriage PDF. Preserves the original legal text while restructuring it into Markdown headings suitable for semantic chunking and RAG ingestion. Ideal as the primary searchable version of the PIA contract while retaining the original PDF for verification. |
| `sereneair_baggage_policy.md` | customer_baggage | Serene Air (Pakistan) | Route-specific baggage tables (domestic, China, UAE, KSA) + claims procedure — your seed PDF doesn't cover baggage in this level of route detail |
| `airblue_travel_info.md` | customer_refund, customer_baggage | AirBlue (Pakistan) | Fare-tier (Value/Flexi/Xtra) baggage + refund/exchange matrix, liability caps |
| `pcaa_ano_001_atcp_air_passenger_rights.md` | customer_refund, regulatory | PCAA (Pakistan regulator) | **The single most important addition** — the actual binding Air Navigation Order every Pakistani carrier's cancellation/delay/denied-boarding claim is checked against. This is a genuine gap-filler your original corpus was missing entirely. |
| `delta_domestic_contract_of_carriage.md` | customer_refund, customer_baggage, operator_licensing | Delta (US) | Dense, numbered-rule (Rule 1-24) US legal document — denied boarding compensation formulas, refund rules, baggage liability caps in exact dollar figures |
| `delta_international_contract_of_carriage.md` | customer_refund, customer_baggage, operator_licensing | Delta (US, international) | Same but incorporates Warsaw/Montreal Convention SDR-denominated liability directly into a real carrier's contract — good for teaching the model how treaty limits map onto a carrier's own terms |
| `southwest_contract_of_carriage.md` | customer_refund, customer_baggage, operator_licensing | Southwest (US) | Different refund architecture (Basic/Choice fare → Flight Credit vs. Transferable Flight Credit) — good contrast case so your model doesn't overfit to one refund model |
| `montreal_convention_1999_full_text.md` | regulatory, customer_refund, customer_baggage | ICAO/IATA (treaty) | The actual treaty text every airline's liability clause cites (SDR caps for death/injury, delay, baggage) — lets your arbitration LLM check a carrier's contract against the treaty floor |
| `faa_14cfr_part117_flight_duty_rest.md` | crew_duty_rest, operator_licensing | FAA (US regulator) | Exact flight/duty/rest tables (Table A/B/C) — the "operator/staff" category your objectives call for |
| `FAA_Flightcrew_Duty_Rest_Requirements_Final_Rule.md` | crew_duty_rest, operator_licensing, regulatory | FAA (US regulator) | **New.** Full Federal Register Final Rule narrative (Vol. 77, No. 2, Jan 4 2012) behind 14 CFR Part 117 — not just the tables, but the FAA's reasoning: reserve availability period vs. FDP measurement (Table E), recovery-rest-on-return-to-home-base logic (Table F, rejected by FAA), deadhead transportation duty classification, circadian resynchronization rationale, and why reduced-rest provisions were dropped from the final rule. Pairs with `faa_14cfr_part117_flight_duty_rest.md` — that file gives the *clause*, this one gives the *why*, including rejected alternative tables and industry vs. labor argument summaries (ALPA, SWAPA, NACA, Atlas, Kalitta, UPS, etc.). Useful for arbitration reasoning that needs to cite regulatory intent, not just the limit itself. |

## Category tagging scheme (per your own build note)

Use these `category` frontmatter tags at ingestion time so retrieval can be scoped by *who's asking*:
- `customer_refund` — refund/cancellation/compensation clauses
- `customer_baggage` — baggage allowance, liability, claims
- `crew_duty_rest` — flight/duty/rest limits
- `operator_licensing` — carrier obligations, enforcement, licensing
- `regulatory` — the underlying law/treaty a carrier's contract is built on

## Sites that blocked scraping (need a different approach)

- `piac.com.pk/facilities/baggage-guide` and `piac.com.pk/facilities/travelers-information` — bot-detection blocked automated fetches. You already have the PIA Conditions of Carriage PDF, but for these two specific pages you'll want to either: (a) manually save-as-PDF from a real browser session, or (b) use a headless-browser scraper (Playwright/Selenium) with a realistic user agent from your own infrastructure, since Claude's fetch tool respects bot-detection blocks.
- `caapakistan.com.pk/security/...` (PCAA Security ANO hub) — blocked by robots.txt. The Airworthiness ANO index page *did* load but only shows a title/description table with no direct PDF links extracted — you'd need to visit each `ANO-0XX-AWRG` entry individually from a browser, or request the index differently.
- `united.com/.../contract.html` — pure JavaScript SPA, no server-rendered content. Use the PDF link from Travelers United's page instead if you want United's COC (see below).

## Additional real links worth adding (not yet scraped in this batch — genuine URLs, verified to exist via search)

(remaining content exactly as provided in the user's manifest...)
