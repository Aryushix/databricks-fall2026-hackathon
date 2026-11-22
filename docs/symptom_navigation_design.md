# Symptom navigation: design note (Step 1, for review)

Status: draft for review, 2026-11-21. No implementation code yet.
Branch: `brian/symptom-navigation`, based on `brian/treatment-verification` (b3bbc9c), so the verifier is on the branch. The crawler fix (`brian/fix-crawler`) isn't needed here.

All counts come from the Phase 2 rule-pass run (19,978 records, `config_hash` 1e47c4b588) and the facility files in `~/hackathon-data`. They are counts, not accuracy.

## 1. Summary

- **The verifier is here and is reused unchanged.** The navigator reads its records; it never re-scores evidence.
- **Query mode 3 isn't implemented yet.** The CLI has only `run` and `show`. I'll add `src/hackathon/verification/query.py` (modes 1–3 over precomputed records) and build the navigation ranker on top of mode 3 (section 6).
- **Only 2 of the 21 care needs have a KB:** hemodialysis and cardiac catheterization. In 11 of the 12 red-flag categories no required need can be verified today. For those, the response is the emergency block, the labeled "capability not verified, call ahead" list and unverified signals. That is the main path, and the design treats it as such.
- **Radius values come from data.** From 143,796 India Post offices, the median distance to the nearest hospital in our data is 20.5 km. A 25 km emergency radius finds a hospital for 59% of offices; widening once to 50 km finds one for 88% (section 5).
- **The review flag is set on almost every match:** 100% of cardiac catheterization LIKELY records and 96% of hemodialysis LIKELY records. Carried through as a bare boolean, it tells the user nothing. I propose showing the specific reasons as warnings instead (section 4).
- **Changes I propose to the brief.** Each one is marked **Change** in the text below:
  1. add verbatim evidence quotes to each match, since `docs/CHALLENGE.md` requires citations;
  2. turn review reasons into specific warnings;
  3. keep CONTRADICTED facilities out of the fallback list too;
  4. set the fallback list's facility types per care need;
  5. don't let a required need with no KB empty the verified list;
  6. add every emergency category to `emergency_department`.

## 2. Interfaces

### 2.1 Verifier output (read only)

The writer is `Verifier.verify()` in `src/hackathon/verification/pipeline.py`. The CLI writes `records.jsonl` and `summary.json`; the copy on Brian's Mac is gzipped, so the store reads both forms.

| Record field | Used for |
| --- | --- |
| `hospital_id` | Join key (= `unique_id`) |
| `normalized_treatment.concept_id`, `.kb_version`, `.kb_review_status` | Which care need it serves; versions; "draft KB" warning |
| `verification_status` | Status policy (section 4) |
| `confidence_score`, `confidence_basis` | Confidence band (always `heuristic_v1` today; shown as a score, never as a probability) |
| `reason_codes` | Warnings and explanations |
| `requires_manual_review`, `review_reasons` | Carried through (section 4) |
| `direct_evidence[]` → `quote`, `field`, `source_type`, `source_url` | **Change 1:** up to 2 verbatim quotes per match, for display only |
| `hospital_location.resolved_latitude/longitude`, `.location_status` | Cross-checked against the facility files at load |
| `pipeline_version`, `config_hash`, `run_timestamp`; `summary.json` `kb_version` | `versions.verification_run` |

Not used: `score_terms`, `p_provides`, `supporting_evidence`, `not_counted_evidence`, `identity_checks`. Using these would mean re-scoring, which non-negotiable 4 forbids. I also import `verification.records.STATUSES` and `verification.kb.load_kb()`, the latter so that care-need ids are checked against real KB file names.

### 2.2 Facility data (facility-side module only)

- `facilities_enriched.csv` is canonical. Columns used: `unique_id`, `name`, `facilityTypeId`, `specialties`, `address_city`, `address_stateOrRegion`, `source_urls`, `officialPhone`, `dq_flags`, `dup_group_id`.
- Joined on `unique_id` with `facilities_verified.csv` for `resolved_latitude`, `resolved_longitude`, `location_status`, `location_confidence` and `location_evidence`.
- Checked at load:
  - both files have the same 9,989 ids;
  - every record `hospital_id` is among them;
  - every KB id in `care_needs.yaml` exists.

  A failed check makes `/health` report the data as unavailable; it never blocks the emergency path.
- Note: the `facilities_enriched.csv` in `~/hackathon-data` predates the crawler fix (it still has `crawl_chain_site`). Regenerate it once `brian/fix-crawler` merges. Capability doesn't depend on it: the verifier ran on `facilities_verified.csv`.

### 2.3 Reused helpers

| Need | Reuse |
| --- | --- |
| Distance | `cleaning.haversine_km` (same formula `location_check` uses) |
| Missing values, JSON lists | `cleaning.missing`, `cleaning.jlist` |
| LLM | `Config().llm_endpoint` (`databricks-gpt-oss-120b`), `rag._workspace().serving_endpoints.get_open_ai_client()`, `rag._text_from_content`, and the `response_format: json_schema` pattern from `classify.LLMClassifier`; temperature 0 |
| User location as PIN or city (later) | `location_check.Directory.find_city()` and its PIN lookups. Not used in M1–M3: the brief takes coordinates only (decision 8) |

### 2.4 New code

```
src/hackathon/verification/query.py   modes 1-3 over records (mode 3 = one treatment ranked near a point)
src/hackathon/navigation/
  contract.py    Pydantic request/response models (one model for library and API)
  red_flags.py   deterministic screen over the raw message
  extract.py     LLM extraction + rule fallback, synonym normalisation
  care_needs.py  findings + fired rules -> care needs (config-driven)
  store.py       CapabilityStore protocol + FileCapabilityStore (facility data lives here only)
  rank.py        status policy, distance, tiered sort (calls query.py mode 3 per need)
  explain.py     templated explanations and warnings
  travel.py      TravelEstimator protocol + NullTravelEstimator (returns None)
  service.py     recommend(): screen first, every later stage isolated
  api.py         FastAPI: POST /recommend-hospitals, GET /health
configs/red_flags.yaml, care_needs.yaml, symptom_terms.yaml, navigation.yaml
```

New dependencies: `fastapi`, `pydantic>=2` and `uvicorn`. The Databricks Apps runtime ships `fastapi` and `uvicorn`. CI also needs `httpx` for FastAPI's test client.

## 3. Care needs and KB coverage

"Signal" counts are facilities listing that `specialties` value (any facility type; sparse and unverified). "Claims" counts are upper bounds from the Phase 1 draft patterns. For `emergency_department` and `general_surgery` I wrote quick draft patterns today; both are upper bounds.

| Care need | Used by | KB today | Proxy KB | Unverified signal | Claims in data | Proposal |
| --- | --- | --- | --- | --- | ---: | --- |
| `emergency_department` | 9 of 12 categories; **Change 6:** all 12 | No | – | `emergencyMedicine` 2,087 | ~2,996 | Draft KB, wave A |
| `cardiac_catheterization` | Heart attack | **Yes** (6 CONFIRMED, 288 LIKELY) | – | `interventionalCardiology` 48 | 578 | Use now; stands in for "cath lab" until `pci` exists |
| `pci` | Heart attack | No | `cardiac_catheterization` (its children include `pci`) | – | 401 | Wave B |
| `adult_icu_ventilation` | Heart attack, breathing, poisoning | No | – | `criticalCareMedicine` 1,674 | 2,041 (any ICU) | Wave A |
| `ct_scan` | Stroke, trauma, unconscious, abdominal | No | – | `radiology` 3,008 | 1,514 | Wave A |
| `trauma_care_24x7` | Trauma | No | – | `burnAndTraumaPlasticSurgery` 131 | 873 | Wave A |
| `blood_bank` | Trauma, pregnancy | No | – | – | 588 | Wave A |
| `stroke_thrombolysis` | Stroke | No | – | `neurology` 1,968 | 260 | Wave B |
| `burn_unit` | Burns | No | – | `burnAndTraumaPlasticSurgery` 131 | 152 | Wave B |
| `emergency_c_section_24x7` | Pregnancy | No | – | `gynecologyAndObstetrics` 4,503 | 18 explicit (452 any C-section) | Wave B; expect almost all UNVERIFIED |
| `nicu` / `picu` | Pregnancy, newborn/child | No | – | `neonatologyPerinatalMedicine` 1,258 / `pediatricCriticalCareMedicine` 219 | 727 / 278 | Wave B |
| `anti_snake_venom` / `anti_rabies` | Poisoning, bites | No | – | – | 20 / 9 | Draft only: too little evidence to verify; the fallback list and 112 carry this case |
| `general_surgery` | Abdominal pain | No | – | `generalSurgery` 3,203 | ~1,849 | Wave C |
| `orthopedics` | "knee hurts for months" | No | `knee_replacement`, `hip_replacement` (no KB yet) | `orthopedicSurgery` 3,526 | 773 / 499 | Wave C (proxy KBs) |
| `ophthalmology` | "vision getting blurry" | No | `cataract_surgery` (no KB yet) | `ophthalmology` 2,832 | 792 | Wave C (proxy KB) |
| `mri` | Knee (supporting) | No | – | `radiology` 3,008 | 956 | Wave C |
| `hemodialysis` | "I'm on dialysis" | **Yes** (46 CONFIRMED, 906 LIKELY) | – | `nephrology` 2,162 | 1,049 | Use now: the end-to-end demo |
| `dentistry` | Toothache | No | – | `facilityTypeId = dentist` 490; `dentistry` 4,202 | – | No KB planned; signals only |
| `inpatient_psychiatry` | Self-harm (supporting only) | No | – | `psychiatry` 1,176 | 209 | Draft only; the crisis block never depends on it |

**Proposal.** The KBs are verifier work: new YAML files run through the existing pipeline, with no navigator changes needed. I'd build them as their own track after the crawler re-run:

- **Wave A** unlocks the emergency path: `emergency_department`, `ct_scan`, `adult_icu_ventilation`, `trauma_care_24x7`, `blood_bank`.
- **Wave B:** `pci`, `stroke_thrombolysis`, `burn_unit`, `nicu`, `picu`, `emergency_c_section_24x7`.
- **Wave C:** `general_surgery`, `knee_replacement`, `hip_replacement`, `cataract_surgery`, `mri`.

`emergency_department` is a service, not a treatment, but it fits the KB format: synonyms such as "24x7 emergency" and "casualty"; confusables such as "in case of emergency call", "dental emergency" and "eye emergency"; and `plausible_facility_types: [hospital]`. All new KBs start at `review_status: draft`, and the navigator shows "draft KB" in the warnings. The rule pass costs nothing; the LLM pass for 15 treatments was estimated at about 83 DBU in the Phase 1 memo.

Until then, a care need without a KB returns `verification_kb: null`, its unverified signals and a `coverage_note` line saying it is not yet verifiable for any facility.

## 4. Status policy

| Status of a required need with a KB | Treatment |
| --- | --- |
| `CONFIRMED` | Eligible, tier 1 |
| `LIKELY` | Eligible, tier 2; warning "likely, not confirmed" plus its reason codes |
| `NOT CONFIRMED`, `UNVERIFIED` | Not a match; can appear only in the fallback list |
| `CONTRADICTED` | Never shown for that need. **Change 3:** not in the fallback list either when the need is required (0 real cases today; covered by synthetic tests) |

**Required needs without a KB (change 5).** These can't be assessed for anyone, so they are left out of eligibility and the sort key. They are listed in each result's `unmatched_required_needs` as "not verifiable yet". If a request has no required need with a KB, the verified list is empty by construction and the response says why. Otherwise, a single missing KB would empty every list.

**Review flags (change 2).**

| Records flagged for review | Share | Main reasons |
| --- | ---: | --- |
| Cardiac catheterization CONFIRMED | 100% | "highly specialized treatment" (6 of 6) |
| Cardiac catheterization LIKELY | 100% | "highly specialized treatment" (288); "only direct evidence is an extracted claim whose source page is unknown" (208) |
| Hemodialysis CONFIRMED | 93% | "CONFIRMED without every key prerequisite documented" (39 of 46) |
| Hemodialysis LIKELY | 96% | "only direct evidence is an extracted claim…" (860 of 906) |

`requires_manual_review` is carried through as the brief asks, and is also set for `location_status` `partly_verified`, `conflicting` or `unverified` (the data has 2 `unverified` rows besides the three values in the brief). Because it is nearly always true, the user-facing part is `warnings`: a fixed mapping from each review reason and reason code to one short sentence, for example:

- "Listed by the facility record only; the source page is unknown."
- "Location fields disagree; check the address."

The flag doesn't affect ranking.

## 5. Location, distance and radius

**Patient points.** I measured from India Post offices as a stand-in for where people live: 143,796 offices with plausible coordinates (inside India's box and within 150 km of their district's median office, the `OFFICE_COORD_KM` rule from `location_check`). They are not population-weighted and lean rural. Distances are straight-line, from `resolved_latitude`/`resolved_longitude`.

| Nearest facility of this kind | n | p25 | p50 | p75 | p90 | ≤25 km | ≤50 km | ≤100 km | ≤150 km |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Any facility | 9,988 | 7.5 | 17.8 | 31.0 | 46.5 | 65% | 92% | 99.5% | 100% |
| Hospital (`facilityTypeId`) | 5,626 | 8.9 | 20.5 | 35.7 | 53.6 | 59% | 88% | 98.8% | 99.9% |
| Hospital listing `emergencyMedicine` (unverified) | 1,906 | 13.6 | 28.9 | 47.9 | 69.3 | 44% | 77% | 97% | 99.4% |
| Hemodialysis CONFIRMED or LIKELY | 952 | 20.4 | 41.5 | 70.0 | 103.7 | 31% | 59% | 89% | 98% |
| Hemodialysis CONFIRMED | 46 | 87.8 | 158.9 | 256.8 | 436.2 | 5% | 11% | 30% | 47% |
| Cardiac cath CONFIRMED or LIKELY | 294 | 33.9 | 61.1 | 95.6 | 130.7 | 18% | 40% | 78% | 94% |
| Cardiac cath CONFIRMED | 6 | 243.3 | 441.0 | 614.7 | 803.0 | 1% | 3% | 8% | 14% |

The dataset is not a census of India's hospitals, so "none within 25 km" means none in our data. The output says so.

**Proposed radii** (in `navigation.yaml`, each commented):

| Urgency | Radius | Widen once to | Why |
| --- | ---: | ---: | --- |
| emergency | 25 km | 50 km | 59% of offices have a listed hospital within 25 km and 88% within 50 km. A wider radius suggests a long self-transport; the 112/108 instruction comes first anyway |
| urgent | 50 km | 100 km | Same-day travel; 99% have a hospital within 100 km |
| routine | 100 km | 150 km | Planned care. Hemodialysis LIKELY or better is within 100 km for 89% and within 150 km for 98% |

**Coverage check.**

- Coordinates must be numeric, within range and not (0, 0).
- They must fall inside the data's bounding box plus 0.5°: latitude 7.66–34.90, longitude 68.86–95.94. The resolved coordinates span 8.17–34.40 and 69.36–95.44.
- The box also covers sea and neighbouring countries, so a second check applies: if no facility is within the widened radius, the response gives a clear "outside our coverage" message and no list. The emergency block is still returned.
- Excluded from distance: 1 facility without resolved coordinates. It's counted in `coverage_note`.

**Duplicates.** The 13 `dup_group_id` groups (2 rows each) collapse to the member with the best status, then the higher `location_confidence`. The other id is kept as `also_listed_as`.

## 6. Ranking (extends query mode 3)

Mode 3 in `query.py` returns, for one KB concept: the candidates within a radius, sorted by status, then confidence, then distance, with `conflicting` locations carrying a warning. The navigator calls it once per required need that has a KB and combines the results with this sort key:

1. status tier of the weakest assessable required need (CONFIRMED < LIKELY);
2. number of assessable required needs matched (more first);
3. confidence band of the weakest matched need;
4. `straight_line_distance_km`.

With one required need, this is exactly mode 3's order, with confidence banded.

**Confidence bands.** Bands are on `heuristic_v1`, which is an ordering, not a probability. Observed: CONFIRMED records all score ≥ 0.913. The 1,194 LIKELY records split 226 below 0.65, 799 in 0.65–0.80 (mostly single-origin claims at 0.65–0.75) and 167 at or above 0.80 (multi-origin or prerequisites met).

| Urgency | Band edges | Effect |
| --- | --- | --- |
| emergency, urgent | 0.65, 0.80 | Separates weak, typical and strong LIKELY evidence before distance decides |
| routine | 0.80 | Only strong vs. the rest, so distance decides among typical LIKELY matches |

**Emergency property.** In emergency mode a farther CONFIRMED match always outranks a nearer non-match. That follows from key 1, and a property test will check it. Non-matches never enter the verified list at all.

**Fallback list ("nearby facilities, capability not verified: call ahead").**

- Up to 3 entries, ordered by distance only.
- Always included for emergencies; otherwise only when verified matches are fewer than `max_results`.
- Facility types come per care need from config (**change 4**): hospitals by default, dentists plus hospitals for `dentistry`.
- Facilities CONTRADICTED for a required need are excluded (change 3).
- Each entry shows its unverified signals, labeled as such.

**Explanations and evidence.**

- One templated sentence per result, built from its fields. For example: "Likely hemodialysis (reason: one listing source); no review of the source page yet; 12.4 km straight-line." A proxy match says it is evidence the department exists, never that the person needs that procedure.
- Per **change 1**, each match carries up to 2 verbatim `quote`s from `direct_evidence` with their field and source. When the page is unknown, it shows "page unknown" plus up to 3 of the facility's `source_urls`.

**Travel.** `TravelEstimator` returns `None` unless a provider is configured; none is. The output never shows a travel time and never calls the nearest facility the fastest.

## 7. Safety path (M1)

- **Order in `recommend()`:**
  1. validate the input;
  2. run the red-flag screen on the raw message (pure function; rules and contacts loaded at startup);
  3. build the emergency block;
  4. then extraction, care needs, data and ranking, each in its own `try`.

  A failure sets `degraded: true` with a reason code and keeps the emergency block. The API returns 200, not 5xx. A missing or invalid `red_flags.yaml` fails closed: the service doesn't start and `/health` reports it. 112 is also a code constant, so the emergency line can't be lost.
- **Rules (`configs/red_flags.yaml`).** Fields: `rules_version`, `review_status: draft`, and per rule `id`, `category`, `patterns`, `floor`, `care_needs`.
  - Patterns cover misspellings and informal English, romanized Hindi/Hinglish and Devanagari.
  - Negation is a scoped cue ("no", "not", "nahi", "नहीं" within a short window before the term). It only suppresses when unambiguous: "no chest pain" doesn't fire; "no chest pain but my arm and jaw hurt" does.
  - The model can raise urgency, never lower it below the rule floor. When torn between two levels, the higher one wins.
- **Contacts, from config with source URLs:**
  - 112: national emergency number (ERSS).
  - 108: ambulance in most states; the 112 site says 108 is being integrated into 112.
  - Tele-MANAS 14416 or 1-800-891-4416: national tele-mental-health helpline, 24x7, multi-language.

  Self-harm content always gets the crisis block (112 and Tele-MANAS) and is never answered with only a list.
- **Reviews needed before anyone relies on it:** a clinician for the whole rule set; a Hindi speaker for the non-English patterns.

## 8. Extraction and privacy (M2)

- **Model call.** The patient message goes inside delimiters as untrusted data, and the output is validated against a Pydantic schema with the brief's fields. Terms are normalised through `configs/symptom_terms.yaml` (a reviewed table: "tummy", "pet mein dard" → abdomen), not through free model text.
- **Fallback.** Timeout (configurable, default 8 s), error, quota exhaustion or schema failure falls back to rule extraction with `extraction_method: "rules_fallback"`.
- **Follow-up questions** for `unclear` messages are templated from `missing_information`, not generated by the model.
- **Privacy.**
  - No raw message or coordinates in logs, caches or output.
  - Logs carry request id, urgency, rule ids, counts, latency and degraded reasons only.
  - The patient-text cache is off by default and is never the Phase 2 `labels.sqlite`.
  - Action item: confirm that payload logging (AI Gateway inference tables) is off for the serving endpoint the app calls.

## 9. Decisions (brief section 10, plus four more)

1. **Emergency department and general surgery KBs.** Agree with drafting them. Order: waves A–C in section 3, with `emergency_department` first since it appears in every emergency category. Draft `anti_snake_venom` and `anti_rabies` too (cheap), but expect nearly all UNVERIFIED (20 and 9 facilities have any claim). Until KBs exist: unverified signals only, labeled.
2. **Fallback list.** Agree: up to 3, always for emergencies, otherwise when verified matches are fewer than `max_results`. Add changes 3 and 4.
3. **Radius and bands.** Radii 25→50 / 50→100 / 100→150 km; bands 0.65 and 0.80 for emergency and urgent, 0.80 for routine (sections 5 and 6). Revisit after the crawler re-run and the wave A KBs.
4. **Languages.** Agree: English, romanized Hindi/Hinglish and Devanagari Hindi, best effort. Other scripts → `unclear` with the emergency line, because the screen can't read them. Tele-MANAS takes calls in many languages, which helps here.
5. **Demo surface.** API plus a "Find care" page in the existing Streamlit app (`app/app.py`, deployed by `app.yaml`). `docs/CHALLENGE.md` requires a non-technical workflow, and an API alone isn't one. The page calls `navigation.service.recommend()` in-process and renders the emergency block first. FastAPI runs locally with uvicorn and can be a second Databricks App.
   - Free Edition allows up to 3 apps.
   - Apps run 24 hours after a start or redeploy, then stop until restarted.
   - Apps are reachable only by users in the Databricks account.

   Data files can't go in the public repo, so on Databricks the store reads them from the `raw` volume.
6. **Care-need roles in emergencies (needs a clinician).** For "possible heart attack", making `cardiac_catheterization` required ranks a cath-capable hospital anywhere in the radius above a nearer hospital without verified cath. The nearer one still appears in the fallback list. This is a routing policy, and in practice the 108/112 service decides. I've kept the brief's roles and marked the mapping draft.
7. **Listed phone numbers.** "Call ahead" is weak without a number. Proposal: show `officialPhone` labeled "listed number, not verified", and never one flagged `phone_shared_across_facilities` (a call-centre or directory number). Default if you don't decide: no phone numbers.
8. **Location as PIN or city.** The challenge's users give a city, district or PIN. Proposal: after M3, accept an optional PIN or city resolved with `location_check.Directory`. The location is still supplied by the user, never inferred.
9. **Who judges.** The human-judgment export (25 cases × top 5, two judges) is about 4 person-hours at roughly a minute per judgment. Labeling capacity from Phase 1 decision 3 is still open.

### Decisions taken (2026-11-22)

- **6.** Brian: ideally a clinician reviews this, but in an emergency getting the patient a result comes first. So emergencies list the nearest hospitals first, with any verified match they have, then verified matches further away, and an emergency always gets a result. Config: `search.emergency_nearest_first`, `search.emergency_always_result`.
- **7.** Yes: `listed_phone`, labeled "not verified by us", never a number flagged `phone_shared_across_facilities`.
- **8.** Coordinates only for now (default).
- **Hindi review:** a human in the loop reviews the non-English patterns.
- **9.** Still open: who judges the export.

## 10. Known limits

- Recommendation quality is bounded by verifier precision, and LIKELY precision hasn't been measured. Example: one LIKELY cardiac catheterization record rests on a single line describing one patient's case, not a service the hospital offers. The human-judgment export is the check.
- Metrics in M4 measure consistency with the verifier, not clinical correctness.
- Straight-line distance ignores roads, terrain and rivers.
- The red-flag rules are a draft until a clinician and a Hindi speaker review them.

## Sources

- [112 India: Emergency Response Support System](https://112.gov.in/about)
- [DGHS: National Mental Health Programme (Tele-MANAS 14416 / 1800-89-14416)](https://dghs.mohfw.gov.in/national-mental-health-programme.php)
- [PIB: Tele-MANAS helpline numbers](https://pib.gov.in/PressReleasePage.aspx?PRID=2022057)
- [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)
- [Databricks Apps environment (FastAPI and uvicorn ports)](https://docs.databricks.com/dev-tools/databricks-apps/system-env)
- [Databricks Apps dependencies (pre-installed fastapi 0.115, uvicorn; Python 3.11)](https://learn.microsoft.com/en-za/azure/databricks/dev-tools/databricks-apps/dependencies)
- [Databricks Apps (only users in the account can open an app)](https://docs.databricks.com/gcp/dev-tools/databricks-apps)
- Phase 1 memo: https://claude.ai/artifact/WBxBiQdZpAQhrXfD65Ff3G
