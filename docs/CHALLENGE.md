# CMU Datathon, Fall 2026: Find the right care

**The task:** given a **service** (e.g. dialysis, emergency C-section, NICU, cardiac surgery, MRI, burn care) and a **location** (a city, district, PIN code, or coordinates), return the best facility option or options from the Virtue Foundation dataset. Each recommendation needs a clear, evidence-based explanation.

Typical users: a community health worker in rural Bihar with a dialysis patient, an NGO coordinator in Maharashtra looking for a high-risk delivery, a district planner routing trauma cases from a highway accident hotspot. The format is open (app, dashboard, Genie space, notebook, agent, or a mix), but it has to suit a **non-technical user**.

## The one rule: show your reasoning

Every recommendation must answer two questions:

- **WHY this facility?** What evidence says it exists, offers this service, and is the best choice for this location?
- **HOW did you decide?** What logic, scoring, model, or rules turned 10,000 records into this answer, and what did we weigh and trade off?

A build that can't explain itself doesn't meet the requirements, however good it looks.

## Submission checklist

- [ ] Runs on **Databricks Free Edition**
- [ ] Uses the **provided facility dataset** (`databricks_virtue_foundation_dataset_dais_2026`)
- [ ] Clear **non-technical user workflow**
- [ ] **Cites the underlying facility text** (`description`, `source_urls`) for every important claim, score, or ranking
- [ ] **Communicates uncertainty** instead of presenting weak evidence as fact

## About the data

About 10,000 facilities with 51 attributes, mostly **extracted from web text**. Treat every field as a claim to verify. Some records merge two facilities, have contradictory locations, or list services with almost nothing to back them up.

| Field | Coverage (organisers' figures) |
| --- | ---: |
| Description | 100% |
| Capability | 99.7% |
| Procedure | 92.5% |
| Equipment | 77.0% |
| Year established | 47.8% |
| Number of doctors | 36.4% |
| Capacity | 25.2% |

## Where the repo stands

| Piece | Status | Where |
| --- | --- | --- |
| Load the shared data into our schema | Done | `notebooks/00b_load_virtue_foundation` |
| Clean facilities, with a flag on every doubtful value | Done | `notebooks/01_clean_facilities`, `docs/DATA_CLEANING.md` |
| Clean the PIN directory and NFHS-5 tables | Ready to run | `notebooks/02_clean_silver` |
| Check each facility's location and give it a point to measure from | Done | `notebooks/01b_verify_locations`, `docs/LOCATION_CHECKS.md`: `resolved_latitude` / `resolved_longitude`, `location_confidence`, `location_evidence` |
| Resolve the user's location (city / district / PIN) to a point or area | To do | Reuse `location_check.Directory`: `find_city()` and the PIN lookups |
| Match a service to a facility's claims, with the supporting text | In progress: cardiac catheterization and hemodialysis | `src/hackathon/verification`, `docs/TREATMENT_VERIFICATION.md` |
| Score and rank (evidence strength × distance × data quality) | In progress: tiered ranking by verified status, confidence band, then distance; location problems become warnings | `verification/query.py` (query mode 3), `navigation/rank.py`, `docs/SYMPTOM_NAVIGATION.md` |
| Explain each pick: quotes from `description`, `source_urls`, and an uncertainty note | In progress: verbatim quotes, source or `source_urls`, warnings and a templated explanation per pick | `navigation/rank.py` |
| Non-technical front end | In progress: "Find care" page in the Streamlit app, plus an API | `app/pages/1_Find_care.py`, `navigation/api.py` |
| Score and rank (evidence strength × distance × data quality) | To do | Use `dq_flags` and `location_confidence` to lower confidence (see `docs/DATA_CLEANING.md`, `docs/LOCATION_CHECKS.md`) |
| Explain each pick: quotes from `description`, `source_urls`, and an uncertainty note | To do | |
| Non-technical front end | To do | Genie space, Databricks App (`app/`), or both |
