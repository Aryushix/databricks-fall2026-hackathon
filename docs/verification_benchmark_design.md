# Verification benchmark: design note (Phase 4, step 1)

**Status: for review. Nothing is built yet.** This is step 1 of the Phase 4 brief. Work stops here until the
decisions in section 14 are answered.

The counts below are rule-pass status counts, **not accuracy**. There are no labels yet.

## 0. Summary

- **Three layers, kept apart.** They are separate in code, in storage and in every report:
  - **A, integrity audit:** every record, reported as pass/fail.
  - **B, synthetic stress set:** designed cases, marked `SYNTHETIC`.
  - **C, labels on real pairs:** *gold* labels come from humans and are the only source of reported precision,
    recall or calibration. *Silver* labels come from Claude and are for development only.
- **The crawl re-run changes the sample.** The crawler was still writing to `crawl.sqlite` during this step.
  - On a consistent copy taken at 06:25 UTC on 2026-11-22, 1,609 sites had crawled OK (393 in the earlier crawl).
  - CONFIRMED rose from 6 / 46 / 83 / 34 to **49 / 218 / 437 / 164** (cardiac catheterization / hemodialysis /
    emergency department / CT scan).
  - Nobody has read the new CONFIRMED records yet.
  - The gold sample is drawn only after the crawl finishes and the records are regenerated, and it is pinned to
    that records file.
- **What 2 judges × 3 hours buys: about 110 labeled pairs.** That assumes 2 minutes a pair and includes overlap,
  adjudication and error typing.
  - That is enough to test decision 2 on **one** treatment: CONFIRMED precision with a Wilson 95% lower bound
    ≥ 0.95. It passes only if all 73 of that treatment's labeled CONFIRMED pairs are right.
  - Testing two treatments while allowing one error each takes about 2 × 8 hours.
- **Recall can be estimated, but only roughly.** Recall over NOT CONFIRMED / UNVERIFIED can be estimated with
  inverse-probability weights. At these sample sizes it is an order-of-magnitude figure with a wide interval.
- **Calibration is out of reach for now.** The proposed minimum is 300 gold pairs per treatment, and no plan
  smaller than 2 × 12 hours reaches it, so `heuristic_v1` stays.
- **Three decisions the brief doesn't list:** what judges may look at (§5.2), how CANT_TELL counts (§5.1), and
  rulings on seven places where `decide.py` and the docs differ or the docs are silent (§3.3). The audit needs a
  ruling on each.

## 1. What the data looks like now

Interim run:
- inputs: `facilities_verified.csv` and the 06:25 UTC copy of `crawl.sqlite`;
- rule pass `rules_v2`, all four KBs;
- 9,989 facilities, so 39,956 records.

These numbers will move when the crawl finishes.

| Stratum | Cath | Hemodialysis | ED | CT |
| --- | ---: | ---: | ---: | ---: |
| CONFIRMED (all `DIRECT_OFFICIAL`) | 49 | 218 | 437 | 164 |
| *same, with the earlier crawl* | *6* | *46* | *83* | *34* |
| LIKELY · direct claim, extracted only (`DIRECT_SINGLE_ORIGIN`) | 194 | 765 | 2,112 | 1,107 |
| LIKELY · official page, score under the threshold (`DIRECT_OFFICIAL`) | 19 | 19 | 60 | 23 |
| LIKELY · chain site only | 8 | 26 | 36 | 15 |
| LIKELY · via key prerequisites (`PREREQS_MET`; 4 cath records also fail the facility-type gate) | 92 | 20 | 0 | 0 |
| NOT CONFIRMED · confusable only | 207 | 11 | 389 | 560 |
| NOT CONFIRMED · other entity | 78 | 74 | 93 | 95 |
| NOT CONFIRMED · implausible facility type | 41 | 0 | 262 | 9 |
| NOT CONFIRMED · stale | 4 | 33 | 82 | 27 |
| NOT CONFIRMED · mention only | 125 | 17 | 16 | 14 |
| NOT CONFIRMED · key prerequisites missing | 83 | 0 | 0 | 0 |
| NOT CONFIRMED · copied, chain, conflict, planned, ambiguous entity, camp, partial negative | 15 | 23 | 26 | 28 |
| CONTRADICTED | 0 | 0 | 5 | 10 |
| UNVERIFIED · prerequisites only | 2,485 | 1,374 | 539 | 2,034 |
| UNVERIFIED · no evidence, but the record touches the area (screen hit) | 496 | 398 | 1,426 | 965 |
| UNVERIFIED · no evidence and no screen hit | 6,093 | 7,011 | 4,506 | 4,938 |

How to read the table:
- **Screen hit** is a broad keyword net over the whole record, wider than the KB. For hemodialysis it is
  `kidney|renal|nephr|dialy`. It is used only to stratify UNVERIFIED pairs for recall sampling, never as
  evidence. KB misses will concentrate there.
- **The threshold fit depends on records just under the threshold.** Under the 0.90 threshold sit 60 ED records,
  and 23 for CT, that have an official-page claim but a score of 0.82–0.89, so their status is LIKELY.
  Section 9.6 needs labels on them.
- **505 records have a truncated evidence list** (more than 8 items in one list), so the audit can't recount
  them from the record alone (§3.2).
- **The cells are uneven:** 195 cells are non-empty and 74 of them hold fewer than 5 pairs.

## 2. Layers and tiers

| Layer | Checks | Covers | Code / data | Reported as | Tier tag |
| --- | --- | --- | --- | --- | --- |
| A. Integrity audit | every value traceable and rule-consistent | 100% of records | code in repo; reports in `~/hackathon-data/benchmark/audit/` | pass/fail counts | `integrity` |
| B. Synthetic stress set | rules on designed hard cases | ~80 cases | `eval/verification/stress.yaml` in repo | "behaviour on designed cases" | `synthetic` |
| C. Gold labels | is the status right | stratified sample | `~/hackathon-data/labels/`, `raw` volume, Delta `gold_treatment_labels` | precision, recall, calibration | `gold` |
| C. Silver labels | development only | larger sample | outside the repo, separate table | never reported | `silver` |

Every number the benchmark writes carries its tier, and code enforces the separation:
- **Separate types and files.** Gold and silver labels have separate types (`GoldLabel`, `SilverLabel`), files and
  tables.
- **Separate reports.** The builder for `benchmark_report.md` only accepts gold labels. Silver numbers go to
  `dev_report.md`, headed "SILVER: development only, not a measurement".
- **A guard test.** A unit test checks that passing silver labels to the gold report builder raises an error.

## 3. A. Integrity audit

```
python -m hackathon.verification audit --records ~/hackathon-data/verification/records.jsonl --facilities ~/hackathon-data/facilities_verified.csv --crawl-db ~/hackathon-data/crawl.sqlite --out-dir ~/hackathon-data/benchmark/audit
```

### 3.1 Checks

Each check re-derives the value from the inputs without calling the code that produced it.

| ID | Check | Re-derived from |
| --- | --- | --- |
| A1 | One record per facility × KB, no duplicates, no unknown ids | facilities CSV × `kb/*.yaml` |
| A2 | Each `quote` is an exact substring of what it cites | extracted: the raw CSV cell of `field`, or that list item. Official: the cached page's text at `source_url`, flattened by the audit's own HTML-to-text pass (not `ledger._page_lines`) |
| A3 | Every `source_url` exists | official evidence: a `pages` row with status 200 in the crawl DB. Extracted evidence: `source_url` is null and `source_type` is `extracted_record` (never guessed). `record_sources.own_domain_urls` is a subset of the row's `source_urls` |
| A4 | `evidence_counts` and `independent_origin_count` match a recount | list length = min(count, 8). Origins = distinct `origin_cluster_id` over direct, positive, counted-entity, not copied, not stale evidence |
| A5 | Status and reason codes match the decision table | `audit/spec.py`, an independent implementation of the spec in §3.3. It never imports `decide.py` or `kb.py` and reads the KB YAML itself |
| A6 | Score arithmetic | prior + Σ terms = `logit`; sigmoid(`logit`) = `p_provides` (±0.001). `confidence_score` = p for CONFIRMED/LIKELY, 1 − p for NOT CONFIRMED/CONTRADICTED, null for UNVERIFIED |
| A7 | Ranges and versions | `confidence_score` in [0, 1]. `confidence_basis` = `heuristic_v1` (or a registered `calibrated_v1@…`). Dates parse and aren't in the future. `kb_version` hash = the KB file. `config_hash` = the config file |
| A8 | Decision 6 | every CONFIRMED record has a direct item with `source_type` `official_site_page`; none rests on extracted evidence only |
| A9 | Summary agrees | `summary.json` status, review and reason counts = a recount of the records |
| A10 | Nothing uncounted is counted | no copied, stale, other-entity or confusable item appears in `direct_evidence` |

### 3.2 Truncated evidence

Records list at most 8 items per evidence list, so A4 and A5 can't be recomputed exactly for the 505 records
that have more.

**Proposal:** an additive option `run --write-evidence` that writes `evidence.jsonl.gz`, holding every evidence
item of every pair. The record contract doesn't change.
- With that file, the audit recounts everything.
- Without it, truncated records are reported as `not_recountable`: a warning listed with record ids, never a pass.

### 3.3 Decision-table spec and the gaps found

The audit re-implements the decision table from a written spec, not from the code. I'll add the spec to
`docs/TREATMENT_VERIFICATION.md` as a table: status, conditions, reason codes, review flags and code order.
Comparing `decide.py` with the current docs turned up seven gaps:

| # | Docs say | `decide.py` does | Proposed ruling |
| --- | --- | --- | --- |
| G1 | CONFIRMED needs "an OFFERED claim on the facility's own website" | also accepts `PARTNER_ON_SITE` | code (decision 1 counts partner-run on site); fix the docs |
| G2 | LIKELY needs "a plausible facility type … or every key prerequisite documented" (ambiguous) | the prerequisite path gives LIKELY even for an implausible type, with a review flag (4 cath records) | code; document it |
| G3 | the status rules don't mention staleness | stale positives don't count, so the status becomes NOT CONFIRMED · `STALE` (146 records have it as the main reason) | code; document it |
| G4 | only the undated tie is described | a positive that outranks the negative continues to CONFIRMED/LIKELY with `CONFLICT_RESOLVED_POSITIVE` | code; document it |
| G5 | a chain site "describes the chain, not the branch" | chain-page positives can reach LIKELY (`CHAIN_SITE_ONLY`, 85 records), never CONFIRMED | code; document it |
| G6 | no main reason defined | the first code is the de facto main reason | write the order into the spec (sampling depends on it) |
| G7 | silent | if the **model** pass labels an *ambiguous-term* match `OFFERED`, the item is listed in `direct_evidence` but never counts toward origins or status (the rule pass converts it to `AMBIGUOUS`, `parse_llm_output` doesn't) | fix in code: treat it as `AMBIGUOUS`, plus a test. This only matters under `--llm` |

With G1–G6 ruled "docs follow code", A5 should pass on today's records. Any mismatch is an A5 failure with
record ids; I won't quietly align the spec to the code.

### 3.4 Output, exit codes, CI

- **Output:** `audit_report.json` (every failure with record ids) and `audit_report.md` (counts per check, the
  first 50 ids per check).
- **Exit codes:** 0 if everything passes, 1 on any failure, 2 on unreadable input.
- **CI on the synthetic fixtures.** Build the SYNTHETIC facilities from `tests/test_verification_pipeline.py`
  plus a synthetic crawl DB, then run `run` and then `audit`.
- **Mutation tests in CI.** For each check, a test corrupts one record and asserts that that check fails and no
  other. The corruptions:
  - edit a quote;
  - swap a status;
  - inflate a count;
  - set a date in the future;
  - make a CONFIRMED record rest on extracted evidence only;
  - put the summary off by one.

  An audit that can't fail proves nothing.
- **Real run (step 2):** on the final records, with the report in `~/hackathon-data/benchmark/audit/<run_id>/`.

## 4. B. Synthetic stress set

**File:** `eval/verification/stress.yaml`.

Each case gives:
- one or more synthetic facilities, with any official-page lines;
- the expected status;
- `require_codes`, which must be present, and optionally `forbid_codes`.

Example:

```yaml
- id: hd-outsourced-01
  kb: hemodialysis
  category: outsourced
  facilities:
    - id: f1
      name: Tamravel Hospital
      city: Kalpitnagar
      type: hospital
      capability: ["Dialysis for our patients is arranged through an outside nephrology centre"]
  expect: {status: CONTRADICTED, require_codes: [OUTSOURCED]}
```

- **Coverage.** Every KB gets ≥ 15 cases; I'll aim for about 20, so about 80 in total, across the ten
  categories in the brief:
  - **Direct:** CONFIRMED with an official page, LIKELY with extracted claims only.
  - **Children:** cath PCI/angioplasty, hemodialysis CRRT and plain "dialysis", ED casualty and pediatric
    emergency, CT named exams.
  - **Confusables:** one case per confusable id in the KB.
  - **Negations:** unopposed (CONTRADICTED), an undated tie (`CONFLICT_UNRESOLVED`), a subgroup only
    (`PARTIAL_NEGATIVE`).
  - **Outsourced, referral, tie-up:** these must hold up against scheme empanelment ("ESIC tie-up"), which is
    *not* outsourcing.
  - **Planned:** coming soon.
  - **Discontinued**, including a restarted service.
  - **Copied listings and chain sites:** three unrelated facilities sharing a listing; a chain site is never
    CONFIRMED.
  - **Other entity**, including an official site that names someone else.
  - **Prerequisites only:** cath and hemodialysis go to LIKELY · `PREREQS_MET` or UNVERIFIED ·
    `PREREQS_PARTIAL`; ED and CT, which have no prerequisite path, go to UNVERIFIED · `NON_KEY_PREREQS_ONLY`.
- **Learned from real data, copied from nothing.** I'll read real records to learn the phrasing patterns, then
  write new text:
  - facility names and towns are invented and checked against the facilities file and the India Post directory;
  - domains use the reserved `.example` top-level domain;
  - phone numbers are left out.
- **Public-safety guard.** `tools/check_public_safe.py --facilities ~/hackathon-data/facilities_verified.csv`
  runs locally before each commit and fails on any of these:
  - an 8-word shingle shared with any real claim;
  - any real facility name or domain;
  - anything shaped like a phone number.

  CI runs a data-free version: domains and phone patterns only.
- **Runner:** `python -m hackathon.verification stress` builds the facilities and an in-memory crawl DB from the
  YAML, runs `Verifier`, and writes `stress_report.md`. The CI gate is 100%.
- **Honesty rule.** The report records the **first-run pass rate before any fixes**. A failing case is fixed in
  the rules or KB, or its expectation is changed with a written reason. An expectation is never edited just to
  turn a case green.
- **What this set can't tell us:**
  - how often each pattern occurs in the real data, so it says nothing about real-world accuracy;
  - paraphrases nobody thought of;
  - extraction errors, since cases are written as clean claims;
  - whether a policy boundary is right (only that the code applies it);
  - entity confusions between names we didn't invent.

  The cases are also written by the same author as the rules. The navigation evaluation had the same weakness
  (its 0/25 over-triage was optimistic for that reason).

## 5. C. Labels on real records

### 5.1 The question and the answers

> Based on the evidence available to us, does this facility provide this treatment in-house, now?

| Answer | Meaning |
| --- | --- |
| `YES` | The evidence establishes in-house provision now, including a partner-run unit or visiting specialist on the premises (decision 1) |
| `NO` | The evidence establishes it is *not* provided in-house now: outsourced, referral only, tie-up with another provider, planned or coming soon, discontinued, or stated as unavailable |
| `CANT_TELL` | Neither. This includes a record that only mentions a confusable (CT angiography for cath) or nothing at all |

How the answers are used:
- **CONFIRMED is strict.** A CONFIRMED record asserts that the evidence establishes provision, so for CONFIRMED
  any answer other than `YES` counts as an error, `CANT_TELL` included. This is the figure tested against the
  0.95 target (decision 8).
- **LIKELY is reported as a distribution** of YES / CANT_TELL / NO, with "share YES" as its precision.
- **A miss** is a `YES` on a NOT CONFIRMED or UNVERIFIED pair. Recall is built from misses.
- **What a label is not.** Labels judge documentary evidence, not whether the service is available today. That
  needs a call to the facility, which is out of scope (decision 6). The labeling guide says this in its first
  paragraph.

### 5.2 What judges may look at (decision 7, not in the brief)

Options:
- **(a) The sheet only:** the full record text, the cached official-page lines, and the verifier's highlighted
  passages.
- **(b) (a) plus the facility's live official website and the record's own source pages,** with no general web
  search and a cap of about 3 minutes per pair.
- **(c) (b) plus web search.**

**Proposed: (b).** With (a), a YES on an extracted-only LIKELY claim mostly restates the extractor, which tells
us little.

Every label also records `evidence_basis`: `official_site`, `source_page`, `record_text_only` or `none`. The
report can then split LIKELY into "a page supports it" and "only the extracted text says so".

**Blinding.** The sheet hides the verifier's status, reason codes, score, review flags and stratum, and rows are
shuffled across strata. Some leakage can't be avoided: CONFIRMED rows always carry official-page quotes.

### 5.3 Fields per label

Each label records:
- `judge_id`, `label`, `evidence_basis`;
- `note` (free text);
- optional `top_quote_assertion`: the assertion type of the top highlighted quote, used for evidence-level
  accuracy (§9.3);
- `labeled_at`;
- `prelabel_shown`, copied from the manifest.

**Error type** uses the fixed taxonomy: confusable, wrong entity, copied listing, stale, outsourced missed,
negation missed, chain site, extraction error, other. Judges are blind to the verifier, so they can't fill it in
while labeling. After import, the tool lists every pair where gold and status disagree, and an **unblinded
error-typing pass** assigns the type: one judge, about 2 minutes a pair. It is costed in §7.

### 5.4 Gold and silver

**Gold**
- Two humans label the sample, with 20% overlap.
- Cohen's kappa is computed on the overlap.
- The two judges adjudicate disagreements together, and the adjudicated label is the gold label (`judge_id`
  `adjudicated`).

**Silver**
- **Model:** Claude served on Databricks (decision 4), proposed `databricks-claude-sonnet-5-5`. Databricks hosts
  its Claude endpoints inside the Databricks security perimeter, so the data doesn't leave the platform.
  [docs](https://docs.databricks.com/aws/machine-learning/foundation-model-apis/supported-models)
- **Call path:** the Anthropic Messages API with `output_config.format`. The Claude 5.5 endpoints reject the Chat
  Completions `response_format` parameter with HTTP 400, so this is a small new client, not `LLMClassifier`.
  [docs](https://docs.databricks.com/aws/en/machine-learning/model-serving/query-anthropic-messages.html)
- **Prompt:** `silver_judge_v1` is the labeling guide's own text plus the sheet row, at temperature 0.
- **Output:** `{label, evidence_basis, quote, note}`. The quote must be an exact substring of the text shown,
  otherwise the label is stored as `invalid`.
- **What silver sees:** the sheet only (option a), since it can't open live sites. Silver and gold can therefore
  differ by design, and the dev report shows that difference.
- **Why a different model:** silver is a different model family from the classifier (`databricks-gpt-oss-120b`),
  so their errors are less correlated. They are not uncorrelated, because both read the same extracted text.
- **Uses:**
  - pre-labels on the gold sheets;
  - mining disagreements between silver and the status for error analysis;
  - dev metrics in `dev_report.md`.
- **Never used for:** reported metrics, calibration, the threshold, or changing a record.
- **Sample:** the gold sample, for pre-labels and silver-vs-gold agreement, plus about 1,500 more pairs drawn by
  the same sampler under a separate manifest.

## 6. Sampling (`src/hackathon/verification/sample.py`)

- **Frame.** Every facility × treatment pair in one pinned records file. The manifest stores the file's sha256
  and the hashes of the facilities CSV and the crawl snapshot.
- **Strata:** treatment × status × main reason × evidence source × facility type.
  - **Main reason:** the first reason code under the spec's order (G6). UNVERIFIED splits into prerequisites
    only, screen hit, and nothing.
  - **Evidence source:** `official` if any evidence item comes from an official page, otherwise `extracted_only`,
    otherwise `none`.
  - **Facility type:** hospital / clinic / other (dentist, doctor, pharmacy, nursing home, missing).
- **Small cells.** Cells with fewer than 5 pairs are merged into a "rest of ⟨treatment, status⟩" cell, and the
  manifest records the merge.
- **Allocation.**
  - The plan sets quotas per treatment × status group (§7).
  - Within a group, allocation is proportional across cells, so precision per status is self-weighting.
  - If a floor of one pair per cell breaks proportionality, intervals use Kish's effective n.
  - Pairs behind verified matches in the navigation judgment sheet form a certainty stratum (§10).
- **Inclusion probability.** Each pair stores π = nₕ / Nₕ for its cell, and its weight is 1/π.
- **Determinism.** The seed comes from `configs/benchmark.yaml`, numpy `PCG64` runs over the sorted frame, and a
  test checks that the same inputs and seed give the same manifest.
- **Manifest.**
  - `manifest.csv`, one row per pair: pair id, cell, Nₕ, nₕ, π, judge, overlap flag, pre-label-shown flag, row
    order.
  - `manifest.json`: `label_set_version` (`lsv1-YYYYMMDD-<records sha8>`), file hashes, KB versions,
    config hash, seed and plan.
  - A manifest is never overwritten: a new draw is a new version.
- **Insufficient.**
  - A treatment whose CONFIRMED pool is under 73 is reported as **insufficient (pool)**.
  - A treatment whose pool is big enough but whose labeled n is too small is **not tested (capacity)**.
  - Treatments are never pooled silently. A pooled figure appears only on its own line, labeled pooled.

## 7. Capacity calculator

```
python -m hackathon.verification capacity --judges 2 --hours 3 --minutes-per-pair 2
```

Unique pairs U = (J × H × 60 − J × 20) / c, where the cost of one unique pair is
c = m(1 + f) + f·d·2a + e·t. The terms:
- J judges, H hours each;
- 20 minutes per judge to read the guide;
- m minutes per pair;
- f = 0.2, the overlap share;
- d = 0.15, the disagreement rate on the overlap;
- a = 3 minutes of joint adjudication, for both judges;
- e = 0.15, the share of pairs needing error typing;
- t = 2 minutes per error-typed pair.

| Plan | m = 1.5 | m = 2.0 | m = 3.0 |
| --- | ---: | ---: | ---: |
| 2 judges × 3 h | 140 | 111 | 78 |
| 2 × 6 h | 298 | 236 | 166 |
| 2 × 8 h | 403 | 319 | 225 |
| 2 × 12 h | 614 | 486 | 343 |

The value of m is a guess:
- Evidence rows show a few quotes and a link.
- UNVERIFIED rows need a scan of the whole record (median 193 words, 90th percentile about 500).
- The first 20 pairs per judge are timed, and the calculator is re-run with the measured m.

Wilson 95% lower bound for CONFIRMED precision:

| Labeled pairs | 0 errors | 1 error | 2 errors |
| ---: | ---: | ---: | ---: |
| 12 | 0.758 | | |
| 30 | 0.886 | 0.833 | 0.787 |
| 50 | 0.929 | 0.895 | 0.865 |
| 73 | 0.950 | 0.926 | 0.905 |
| 100 | 0.963 | 0.946 | 0.930 |

The smallest n that clears 0.95 is 73 with 0 errors, 110 with 1, 142 with 2 and 173 with 3.

What each plan buys, at m = 2.0:

- **Plan 3a, 2 × 3 h (~110 pairs): prove ED only.**
  - Sample: 73 ED CONFIRMED pairs, all blind, plus 37 more: ED LIKELY 15, NOT CONFIRMED 8, UNVERIFIED screen
    hit 8, navigation certainty pairs 6.
  - ED passes only with zero errors. With one error the bound is 0.926: "not shown".
  - The other three treatments are not tested.
- **Plan 3b, 2 × 3 h: screen all four.**
  - Sample: about 27 pairs per treatment: 12 CONFIRMED, 8 LIKELY, 3 NOT CONFIRMED, 4 UNVERIFIED.
  - No treatment can reach 0.95 (12 of 12 gives 0.758).
  - It finds gross problems and gives a silver-vs-gold agreement figure.
- **Plan 8a, 2 × 8 h (~320 pairs): ED and hemodialysis.**
  - CONFIRMED: 110 pairs each, so one error is tolerated.
  - Plus, per treatment: 20 LIKELY (10 of them from the official-page-under-threshold group, for §9.6),
    10 NOT CONFIRMED, 10 UNVERIFIED screen hit and 5 UNVERIFIED with nothing.
  - Plus about 10 navigation certainty pairs.
- **Full first gold set, all four treatments (~880 pairs): about 2 × 22 h.** Cardiac catheterization may still be
  insufficient (pool).

**Recall stays rough at every plan.** At plan 8a an UNVERIFIED screen-hit pair carries a weight of about 40–140
(1,426 ED pairs ÷ 10 sampled). One YES found there moves the recall estimate a lot, so recall is reported with a
bootstrap interval and called an order-of-magnitude estimate.

## 8. Labeling workflow

**Export**

```
python -m hackathon.verification export --manifest ~/hackathon-data/labels/<lsv>/manifest.csv --out-dir ~/hackathon-data/labels/<lsv>/sheets
```

- **Files:** one sheet per judge, as `judge_<id>.csv` and `judge_<id>.xlsx`. The XLSX has dropdowns, a frozen
  header and wrapped text; the CSV has the same columns.
- **Columns shown:**
  - facility name, city and state, facility type;
  - the treatment, with the guide's one-line definition;
  - the highlighted passages with links (official: the cached page and the live URL; extracted: "page unknown"
    and the record's source URLs);
  - the full record text;
  - the silver pre-label, blank on blind rows.
- **Columns the judge fills in:** label, evidence basis, top-quote assertion, note.

**Pre-labels:** shown or hidden per decision 3, randomized within cell and recorded in the manifest.

**Import**

```
python -m hackathon.verification import --manifest ~/hackathon-data/labels/<lsv>/manifest.csv --sheets ~/hackathon-data/labels/<lsv>/sheets
```

- **Validation:**
  - no unknown pair ids;
  - allowed values only;
  - one label per judge per pair;
  - every assigned row present or marked skipped;
  - the judge id matches the file.
- **Writes:**
  - `labels.jsonl`, append-only, one line per judge label, with the sheet file's hash;
  - the Delta table `workspace.hackathon.gold_treatment_labels`.

**Agreement:** Cohen's kappa on the overlap, both 3-way and YES vs not-YES, with a bootstrap interval. An
adjudication sheet puts disagreements side by side.

**Error typing:** after adjudication (§5.3).

**Streamlit labeling page:** not now. The sheet flow comes first.

## 9. Metrics and calibration (`python -m hackathon.verification benchmark`)

1. **Precision per treatment × status, with Wilson 95% intervals.**
   - CONFIRMED: strict share YES.
   - LIKELY: the YES / CANT_TELL / NO distribution, plus the split by evidence basis.
   - NOT CONFIRMED and UNVERIFIED: the miss rate.
   - CONTRADICTED: share NO.
2. **Weighted recall and the confusion matrix.**
   - Recall: Σ w over gold-YES pairs with status CONFIRMED or LIKELY, divided by Σ w over all gold-YES pairs.
   - The interval comes from a stratified bootstrap (resample within cells, 2,000 replicates).
   - The confusion matrix of status vs gold is shown both weighted (a population estimate) and as raw counts.
3. **Evidence-level accuracy: rule pass vs model pass vs `DualPass`, per assertion type**, using the judge's
   `top_quote_assertion`. It needs that optional column and a model pass that has actually run on the Databricks
   endpoint, which hasn't happened yet. Until both exist, the section says "not measured"; silver never fills
   it in.
4. **Error taxonomy:** counts per type with example pair ids. The text of the examples stays in
   `~/hackathon-data`.
5. **Calibration of P(gold YES | `p_provides`).**
   - **Minimum n:** 300 gold pairs per treatment, with at least 50 YES and 50 not-YES, spread across the score
     range.
   - **Method:** isotonic regression weighted by 1/π, with 5-fold cross-validation.
   - **Outputs:** a reliability table (10 equal-mass bins), weighted ECE, and a 5-bin binned check.
   - **When records may switch.** A record may say `confidence_basis: calibrated_v1@<lsv>` only if the minimum n
     is met *and* the cross-validated ECE beats `heuristic_v1`'s. This means a new scorer class and a config
     change, both with your approval.
   - **Below the minimum,** the report shows n and the gap to it.
6. **Threshold fit.**
   - **Who is eligible:** CONFIRMED and LIKELY · `DIRECT_OFFICIAL` pairs, meaning an official OFFERED or partner
     claim, a plausible facility type and no conflict.
   - **Fit:** find the lowest t for which eligible pairs with p ≥ t have a Wilson lower bound ≥ 0.95, with
     n ≥ 73.
   - **Report:** t, n and the errors. The config is not changed.
   - **Caveat:** choosing t on the same labels is optimistic, so t is a proposal to re-check on the next label set.
7. **Run-vs-run diff.**

   ```
   python -m hackathon.verification diff --records-a A.jsonl --records-b B.jsonl --labels labels.jsonl
   ```

   For each labeled pair it lists status A, status B and gold, and marks each flip right, wrong or neutral. It
   also counts flips across the whole population. Uses: the interim vs final crawl, `rules_v2` vs `rules_v3`,
   before and after a KB change.

   Labels judge pairs, not statuses, so they survive rule changes. They are tied to an evidence snapshot, though:
   the diff also lists labeled pairs whose evidence changed between A and B.
8. **Tiers in the report.** Every table carries a tier column, and the report header lists which numbers are
   gold, silver, synthetic and integrity.

## 10. Navigation tie-in

**Current state.** `human_judgment.csv` from `tools/eval_navigation.py` has 74 rows:
- none is judged yet;
- it was built before the ED and CT KBs existed;
- only 8 rows carry a verified match, all hemodialysis.

**Plan.**
1. After the final records exist, re-run the navigation evaluation.
2. Put the (hospital, KB) pairs behind verified matches into the gold sample as a certainty stratum (π = 1,
   capped at 40).
3. Once both sheets are judged, join on (`hospital_id`, `kb_id`): navigation judgment (appropriate y/n) ×
   verifier gold label. For example: "shown as verified, gold not YES: k of n".

These are counts. A rate is shown only once n ≥ 30.

## 11. Storage and safety

| What | Where |
| --- | --- |
| Audit reports | `~/hackathon-data/benchmark/audit/<run_id>/` |
| Manifests, sheets, imports, `labels.jsonl`, adjudication and error-typing sheets | `~/hackathon-data/labels/<lsv>/` and `/Volumes/workspace/hackathon/raw/labels/<lsv>/` |
| Human labels (Delta) | `workspace.hackathon.gold_treatment_labels` |
| Model labels (Delta) | `workspace.hackathon.dev_treatment_model_labels`, with `label_tier = 'silver'`. A table named `silver_…` would read as a cleaned table in this repo's medallion naming |
| Benchmark and dev reports | `~/hackathon-data/benchmark/reports/<lsv>/` |
| Stress set and its report | in the repo (SYNTHETIC) |

**CI additions.**
- **Data-file check.** Extend it to reject `*.jsonl`, `*.gz` and anything under a `labels/` path.
  - Allowlist: `eval/**`, which already holds `eval/questions.jsonl`; `tests/fixtures/**`; and `*.synthetic.*`.
- **URL and phone check.** In `eval/` and `tests/`, reject any URL outside reserved or documentation domains, and
  any phone-number pattern.
- **`.gitignore`** gains `labels/`, `*.jsonl.gz` and `evidence.jsonl*`.

## 12. Code to add

| Path | What |
| --- | --- |
| `src/hackathon/verification/audit/spec.py` | the independent decision table (from §3.3's spec) |
| `src/hackathon/verification/audit/checks.py` | A1–A10 |
| `src/hackathon/verification/sample.py` | strata, allocation, π, manifest, capacity calculator |
| `src/hackathon/verification/labels.py` | sheet export, import validation, kappa, adjudication and error-typing sheets |
| `src/hackathon/verification/silver.py` | the Claude client (Messages API), prompt `silver_judge_v1` |
| `src/hackathon/verification/benchmark.py` | metrics, calibration, threshold fit, diff, the gold and dev reports |
| `configs/benchmark.yaml` | seed, plan quotas, screen terms, minimum n, silver endpoint |
| `eval/verification/stress.yaml` | the SYNTHETIC stress set |
| `docs/LABELING_GUIDE.md` | the label definition and 10+ synthetic worked examples |
| `tools/check_public_safe.py` | the local guard against real text in the repo |
| `notebooks/08_benchmark_verification.py` | audit, benchmark and diff on Databricks |
| `tests/test_verification_{audit,stress,sample,labels,benchmark}.py` | mutation tests, the 100% stress gate, determinism, validation, the guard that rejects silver |

Each new subcommand goes on the existing CLI: `audit`, `stress`, `sample`, `capacity`, `export`, `import`,
`agreement`, `silver`, `benchmark`, `diff`.

## 13. Assumptions

1. The crawl finishes and the records are regenerated before step 2. All interim numbers here will move.
2. Labels judge documentary evidence, not real availability.
3. The rule pass (`rules_v2`) is the classifier under test. The model pass hasn't run on the endpoint, so
   evidence-level comparisons wait for it.
4. The workspace serves a Claude endpoint. To check: `databricks serving-endpoints list`. If it doesn't, the
   fallback is another non-gpt-oss endpoint that the workspace lists.
5. Labeling takes about 2 minutes a pair until the pilot measures it.
6. Allocation is proportional within each status, so the Wilson interval per status is valid. Weights enter
   recall, the confusion matrix and calibration.
7. The screen terms are a sampling device only. They live in `configs/benchmark.yaml` and never feed the
   verifier.
8. The same author writes the stress set and the rules (§4).
9. Silver sees the sheet only; gold may also open live sites (§5.2).
10. Only `heuristic_v1` exists until calibration clears the minimum n. No score is called a probability before
    then.

## 14. Decisions (proposed answers)

| # | Decision | Proposed | What it changes |
| --- | --- | --- | --- |
| 1 | Labeling capacity | **2 × 8 h** (plan 8a). If 2 × 3 h stays: plan 3a (ED only) | how many treatments get a CONFIRMED precision test |
| 2 | Who the judges are | you plus one teammate who hasn't worked on the KBs. A clinician, if available, reviews the guide's boundary cases and adjudicates cath/ED disagreements (< 1 h), but isn't a primary judge | independence; the clinical boundary calls |
| 3 | Pre-labels | **blind on every CONFIRMED pair tested against 0.95**; 80% shown / 20% blind elsewhere, randomized within cell | keeps the 0.95 claim free of anchoring. At these sizes the 20% blind group detects only large anchoring effects |
| 4 | Silver judge | `databricks-claude-sonnet-5-5`; fallback `databricks-claude-opus-5-5` or another non-gpt-oss endpoint | correlation with the classifier |
| 5 | Treatments in the first gold set | **ED and hemodialysis**: the largest CONFIRMED pools, the emergency path, and the most common verified match in navigation. Cath and CT get silver only for now ("not tested"); cath is "insufficient" if its final CONFIRMED pool is under 73 | the sample plan |
| 6 | Phone verification | **No** | labels stay documentary |
| 7 | *(new)* What judges may look at | **sheet plus live official site and the record's source pages**, no web search, about 3 min cap, `evidence_basis` recorded | what YES means for extracted-only LIKELY |
| 8 | *(new)* CANT_TELL for CONFIRMED | **counts as an error (strict)**; LIKELY is reported as a distribution | the 0.95 test |
| 9 | *(new)* Decision-table gaps G1–G7 | docs follow code for G1–G6; fix G7 in code, with a test | what the audit's A5 enforces |
