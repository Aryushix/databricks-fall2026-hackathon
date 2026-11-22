# Treatment verification

Does this facility provide this treatment, and what evidence says so? For every facility and treatment,
`src/hackathon/verification` returns a status (CONFIRMED, LIKELY, NOT CONFIRMED, UNVERIFIED, CONTRADICTED),
reason codes, a confidence score and the exact evidence behind the verdict.

**A result describes what the documented evidence supports. It is not a guarantee that a treatment is
clinically available today.** Every field in this dataset is a claim extracted from the web.

Status: **Phase 2** of the build. Two treatment families are wired end to end (cardiac catheterization and
hemodialysis). Query modes 1–3 over the precomputed records are in `verification/query.py` (mode 3 is the base
of symptom navigation's ranking, `docs/SYMPTOM_NAVIGATION.md`). Spark batch runs (Phase 3) and the evaluation
and calibration tools (Phase 4) are not built yet. The design memo behind the choices below covers the data audit.

## How it works

1. **Ledger.** Every facility is split once into atomic claims: one per list item, description sentence,
   or line of the facility's own website (cached by `tools/facility_crawler.py` in `crawl.sqlite`). Each
   claim carries a content hash, its lineage and its origin cluster.
   - All extracted fields of a record come from one upstream extraction run, so they form **one** origin
     cluster (`vf:<unique_id>`). Agreement between `capability`, `procedure` and `description` is never
     counted as corroboration.
   - Each official website is its own cluster (`official:<domain>`), but only if the site names the
     facility (`entities.owns_site`). A site that names someone else, or names another city, is
     `official_site_unmatched` and its claims count as another facility's. A site shared by 3+ records,
     or by records in 2+ cities, is a chain site and describes the chain, not the branch.
2. **Tagging** (`kb.py`, `kb/*.yaml`). Each treatment has one reviewed KB entry: synonyms, children
   (coronary angiography and PCI are forms of cardiac catheterization), ambiguous terms settled by
   context, confusables that must never count (CT angiography, eye angiography, "biochemical
   angioplasty", urinary catheters, peritoneal dialysis), and prerequisites (cath lab, interventional
   cardiology, dialysis machines, nephrology). Confusables are matched first and block any overlapping
   positive match; the longest match wins, so "cath lab" is a prerequisite, not the procedure.
3. **Labels** (`classify.py`). Each tagged claim gets an assertion type: `OFFERED`, `PARTNER_ON_SITE`,
   `OUTSOURCED_OFF_SITE`, `REFERRAL_ONLY`, `PLANNED_OR_COMING_SOON`, `DISCONTINUED`, `NOT_OFFERED`,
   `EDUCATIONAL_OR_GENERIC`, `OTHER_ENTITY`, `OUTREACH_CAMP`, `PERSONNEL_ONLY`, `RESEARCH_OR_LISTING`,
   `EXTRACTOR_ARTIFACT` or `AMBIGUOUS`. The rule pass always runs; `--llm` adds the Databricks model pass
   (`DualPass`), whose quote must be an exact substring of the claim. Disagreements are flagged. Labels are
   cached in SQLite by claim text, concept, model and version. Entity matching runs in code, not in the
   model.
4. **Copies** (`copies.py`). A long claim that recurs under 3+ unrelated facilities, or a list item pasted
   as a block into 3+ unrelated facilities' lists, is a `COPIED_LISTING` and never counts as support.
5. **Score** (`scoring.py`, `heuristic_v1`). Log-odds from a prior (by specialization and facility type),
   plus one term per origin cluster from its single best positive claim, minus its best negative claim,
   plus a capped prerequisite term. Weights are hand-set in `configs/verification.yaml`.
6. **Status** (`decide.py`). Deterministic rules, in this order:
   1. **CONTRADICTED**: a credible negative about the treatment itself and this facility, more reliable or
      more recent than the best positive, or unopposed. An undated tie is `CONFLICT_UNRESOLVED`.
   2. **CONFIRMED**: an `OFFERED` claim on the facility's own website (extracted fields alone never
      confirm, decision 6), a plausible facility type, no unresolved conflict, and a score at or above
      `confirmed_threshold`.
   3. **LIKELY**: a direct claim that is not copied, from a plausible facility type, with at least one key
      prerequisite for a highly specialized treatment; or every key prerequisite documented.
   4. **NOT CONFIRMED**: the treatment is mentioned, but nothing establishes provision.
   5. **UNVERIFIED**: the treatment is never mentioned. Absence is never negative evidence.

`confidence_score` is labelled `confidence_basis: heuristic_v1`. For CONFIRMED and LIKELY it is the
score; for NOT CONFIRMED and CONTRADICTED it is one minus the score; for UNVERIFIED it is `null`. It is
not a calibrated probability until Phase 4 fits it on labeled pairs.

### What it reuses from this repo

| From | Used for |
| --- | --- |
| `cleaning.jlist`, `clean_text`, `missing` | Reading list fields and text exactly as cleaning wrote them |
| `cleaning.domain_of`, `is_non_official`, `NON_OFFICIAL_DOMAINS` | Website domains and directory sites |
| `location_check` columns (`location_status`, `location_confidence`, `location_evidence`, `resolved_latitude/longitude`) | Identity checks and the output's location block |
| `config.Config.llm_endpoint` | The model for the `--llm` pass (`models.llm_endpoint: null` means "use Config") |
| `rag._workspace()`, `rag._text_from_content` | The Databricks client and response parsing for the model pass |
| `crawl.sqlite` schema from `tools/facility_crawler.py` | Official website pages |

## Install and configure

```bash
pip install -r tools/requirements.txt
```

Settings live in `configs/verification.yaml`: decisions, model names, the source reliability hierarchy,
scoring weights, thresholds, copy detection and review flags. Every output records `pipeline_version`,
`config_hash` and each KB entry's version and content hash.

## Run it

Inputs are the CSV that `tools/verify_locations.py` writes (the laptop copy of `silver_facilities_verified`)
and, optionally, the crawler cache. **Write outputs outside the repo**: they are real facility records.

```bash
python tools/verify_treatments.py run \
    --facilities ~/hackathon-data/facilities_verified.csv \
    --crawl-db ~/hackathon-data/crawl.sqlite \
    --out-dir ~/hackathon-data/verification

python tools/verify_treatments.py show --records ~/hackathon-data/verification/records.jsonl \
    --hospital-id <unique_id> --treatment cardiac_catheterization
```

Add `--llm` on Databricks (or with a Databricks CLI profile) to run the model pass. A full run over
9,989 facilities and both families takes about 2 minutes on a laptop with the rule pass alone.

Output: `records.jsonl` (one record per facility and treatment) and `summary.json` (counts by status,
reason code and review flag). Each record has the contract fields (`verification_status`,
`reason_codes`, `confidence_score`, `confidence_basis`, `independent_origin_count`, `direct_evidence`,
`supporting_evidence`, `contradictory_evidence`, `missing_prerequisites`, `reasoning_summary`,
`requires_manual_review`, `review_reasons`, `pipeline_version`, `run_timestamp`), plus
`not_counted_evidence`, `identity_checks`, `record_sources` and `score_terms` for auditing. Each evidence
item has `claim_id, quote, field, assertion_type, concept_ids, source_url, source_type, lineage,
origin_cluster_id, source_date, retrieval_date, entity_match, relevance_score, classifier_model,
classifier_version`. `reasoning_summary` is built from a template over the codes and evidence.

## First run, 2026-11-21 (status counts, not accuracy)

Rule pass only, on all 9,989 facilities. There are no labels yet, so these are counts, not performance.

| Treatment | CONFIRMED | LIKELY | NOT CONFIRMED | UNVERIFIED | CONTRADICTED | Flagged for review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cardiac catheterization | 6 | 288 | 535 | 9,160 | 0 | 416 |
| Hemodialysis | 46 | 906 | 152 | 8,885 | 0 | 966 |

Every one of the 16,705 evidence quotes in that run was checked to be verbatim in the input CSV or the
cached page. Neither family has a credible negative claim in the data, so CONTRADICTED is covered only by
the synthetic regression suite.

## Phase 3: batch runs and more treatments

The CLI is also `python -m hackathon.verification` with four subcommands:

```bash
PYTHONPATH=src python -m hackathon.verification run \
    --facilities ~/hackathon-data/facilities_verified.csv --crawl-db ~/hackathon-data/crawl.sqlite \
    --out-dir ~/hackathon-data/verification --workers 4 --log-format json

PYTHONPATH=src python -m hackathon.verification show --records ~/hackathon-data/verification/records.jsonl \
    --hospital-id <unique_id> --treatment ct_scan
PYTHONPATH=src python -m hackathon.verification profile --records ~/hackathon-data/verification/records.jsonl \
    --hospital-id <unique_id>
PYTHONPATH=src python -m hackathon.verification rank --records ~/hackathon-data/verification/records.jsonl \
    --treatment emergency_department --lat <lat> --lon <lon> --radius-km 50
```

- `--workers N` tags claims and verifies facility shards in N processes. It applies to the rule pass only, and only once there are at least 50,000 claims. Output is identical to a single-process run, including record order; a test checks this. A model pass (`--llm`) stays in one process, because it shares one label cache and the endpoint's rate limit.
- Every run writes `run_log.jsonl`: timings and counts per stage, with no claim text.
- `show`, `profile` and `rank` read `.jsonl` or `.jsonl.gz` records (query modes 1, 2 and 3).
- On Databricks, `notebooks/07_verify_treatments.py` runs the same pipeline as a job. It writes `records.jsonl.gz`, `summary.json`, `run_log.jsonl` and `labels.sqlite` to the `raw` volume under `navigation/verification`, plus the Delta table `silver_treatment_verification`. It hasn't been run on the workspace yet.

**Wave A, 2026-11-22.** New KBs, all `review_status: draft`. These are rule-pass counts on the same 9,989 facilities, not accuracy:

| Treatment | CONFIRMED | LIKELY | NOT CONFIRMED | UNVERIFIED | CONTRADICTED | Flagged for review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Emergency department | 83 | 2,433 | 814 | 6,654 | 5 | 2,575 |
| CT scan | 34 | 1,210 | 655 | 8,081 | 9 | 1,279 |

Before committing, every CONFIRMED record and a sample of LIKELY records were read. That review added these confusables:
- emergency department: single-specialty emergency lines (cardiac, neuro, ENT, eye, dental, veterinary), emergency home care, ambulances, and the public "911 emergency services" line;
- CT: the bare acronym "CTA", which also names organisations.

The rule classifier also learned "via an outside …" as outsourcing. That change is `rules_v2`, so cached `rules_v1` labels aren't reused. Cardiac catheterization and hemodialysis counts are unchanged.

**Finished crawl, 2026-11-22.** The crawler was re-run on every site: 5,213 of 8,060 crawled OK (393 before), and
the verifier now has official pages for 5,222 facilities (543 of those sites don't name the facility; 130 domains
are chains). Same rules (`rules_v2`), same 9,989 facilities, run `240517db1f17`. Counts, not accuracy:

| Treatment | CONFIRMED | LIKELY | NOT CONFIRMED | UNVERIFIED | CONTRADICTED | Flagged for review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cardiac catheterization | 88 | 331 | 600 | 8,970 | 0 | 543 |
| Hemodialysis | 472 | 708 | 172 | 8,637 | 0 | 1,141 |
| Emergency department | 995 | 1,880 | 1,093 | 6,014 | 7 | 2,935 |
| CT scan | 362 | 1,048 | 949 | 7,621 | 9 | 1,451 |

Unlike the earlier runs, the 1,049 records that became CONFIRMED with the new pages haven't all been read. A random
40 of them showed four patterns the rules don't handle yet (`docs/verification_benchmark_design.md`, §1.2).

## Tests

`pytest` runs `tests/test_verification_components.py` (KB, ledger, every assertion type, model-output
checks, cache, entities, copies) and `tests/test_verification_pipeline.py` (a SYNTHETIC fixture with one
facility per status, the output contract, and "extracted fields alone never confirm"). No real records
are in the tests: the repo is public and the data's sharing terms keep it out.

## Run the evaluation

Not built yet: sampling, labeling guidelines, metrics, calibration and threshold fitting are Phase 4.
`decisions.confirmed_precision_target` is 0.95 on the Wilson 95% lower bound, so the threshold fit needs
about 73 labeled CONFIRMED pairs with no errors, or 142 with at most 2.

## Add a treatment

1. Copy `src/hackathon/verification/kb/hemodialysis.yaml` to `<concept_id>.yaml` and fill in synonyms,
   children, ambiguous terms, confusables, prerequisites, `key_groups`, `specialization`,
   `plausible_facility_types` and codes (MeSH; ICD-10-PCS where it maps cleanly).
2. Keep `review_status: draft` until a clinician or a teammate has checked it; records carry it.
3. Add SYNTHETIC `examples` with the expected match kind; `tests/test_verification_components.py`
   checks every example of every entry.
4. Run `verify_treatments.py run --treatments <concept_id>`. Nothing else is reprocessed: the ledger is
   rebuilt in seconds and cached labels are reused.

## Known limitations

- **Extracted claims have no page.** `source_urls` is one list per facility, so an extracted claim's source
  is `extracted_record`, never upgraded by guessing. Extracted fields alone can reach LIKELY at most.
- **Official evidence covers about half the facilities.** The finished crawl cached pages for 5,222 of
  9,989 facilities; 543 of those sites don't name the facility. The rest have no website in the data, or
  their site couldn't be crawled (about 2,850 sites, mostly unreachable; about 390 failed on response
  decoding, which may be fixable on our side). Without an official page a facility can't reach CONFIRMED.
- **Quotes are the extractor's words.** The substring check stops a model inventing quotes; it can't catch
  an extraction that misread the page. Official-page quotes are the page's own text.
- **The rule pass is a cue list.** It misses paraphrases and can misfire (tests pin the known cases).
  The model pass is implemented and unit-tested with a fake client but has not run against the
  Databricks endpoint yet.
- **No embedding retrieval yet.** Tagging is pattern-based; claims phrased in ways the KB doesn't list are
  missed (recall unknown until Phase 4).
- **Copy detection catches exact and near-exact copies,** not paraphrased ones.
- **Entity matching is name-based.** A claim about a sibling that doesn't name it can't be told apart,
  except when the same record also names the sibling (then all its unnamed claims become `ENTITY_AMBIGUOUS`).
- **Chain sites** are detected only among facilities in this dataset; a chain with one record here passes
  as a branch site.
- **Scores are heuristic** until calibrated on ground-truth labels.
- **Wave A boundaries are policy choices.** A single-specialty emergency line ("24/7 emergency cardiac
  care", "neuro emergency care") doesn't count as an emergency department; the trauma KB, still to come,
  will cover trauma. An ambulance is a non-key prerequisite, never evidence of a department. A CT exam
  named in a news report about one patient counts as a CT scanner, the same way a procedure performed
  counts for the other treatments.
