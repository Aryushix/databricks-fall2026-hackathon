# How the facility data is cleaned

The raw Virtue Foundation export has 10,000 rows. Cleaning produces **9,989 facilities**: only 11 exact duplicate rows are removed. Instead of deleting doubtful values, the cleaning **flags** them. **2,519 facilities (25%) carry at least one flag** in `dq_flags`, which is what lets the app say how sure it is about each recommendation.

The rules live in one file, `src/hackathon/cleaning.py`, used by both:
- `notebooks/01_clean_facilities` on Databricks, which writes `workspace.hackathon.silver_facilities`
- `tools/clean_dataset.py` on a laptop, which writes `facilities_cleaned.csv`

Both give identical results. That was checked column by column on the full dataset, and `tests/test_cleaning.py` covers the individual rules.

## How to run it

| Where | Command | Output |
| --- | --- | --- |
| Databricks | Run `notebooks/01_clean_facilities` | `silver_facilities` + `silver_facilities_dq_summary` |
| Laptop | `pip install -r tools/requirements.txt` then `python tools/clean_dataset.py dataset.csv facilities_cleaned.csv` | `facilities_cleaned.csv` |

`01_clean_facilities` uses the first source it finds in `/Volumes/workspace/hackathon/raw/`:

| File in the `raw` volume | What happens |
| --- | --- |
| `facilities_enriched.csv` | Cleaned and crawled on a laptop: loaded as-is |
| `facilities_cleaned.csv` | Cleaned on a laptop: loaded as-is |
| `dataset.csv` | Raw export: cleaned in Databricks |
| nothing | Cleans `bronze_facilities` from notebook `00b` (see the limitation below) |

**Limitation of the bronze-table path.** In the raw file, 30 records have a multi-line description that was split across lines. Any standard CSV reader, including the one that built the shared table, breaks each of these records: the record keeps its first 28 fields, loses the 23 after `description` (capabilities, procedures, coordinates, source URLs…), and leaves 88 junk "fragment" rows behind. From the table, the notebook drops the 88 fragments and keeps the 30 records with the flag `row_truncated_in_source`, but it can't recover their lost fields. Only the raw file can do that. For a complete dataset, upload `dataset.csv` to the `raw` volume (**Catalog → workspace → hackathon → raw → Upload**) and rerun `01`.

**Data never goes into Git.** `.gitignore` blocks `*.csv`, `*.parquet`, the crawler's `*.sqlite` cache, and everything in `data/`. Files live in the Databricks `raw` volume or on your laptop.

## The five stages

| Stage | What it does | Effect on this dataset |
| --- | --- | --- |
| 1. Structural repair | Stitches records whose multi-line `description` was split across lines (with `,null` padding) back into 51 fields | 30 records repaired (`row_repaired_multiline`) |
| 2. Exact duplicates | Drops rows that are identical in every field | 11 rows dropped |
| 3. Column pruning | Drops constant, empty, or redundant columns | 6 columns dropped (below) |
| 4. Field cleaning | Types, phones, emails, URLs, lists, geography, dates, outliers, text | See "Field rules" |
| 5. Quality flags | Writes `dq_flags` per row and links likely duplicates with `dup_group_id` | 2,519 facilities flagged; 13 duplicate groups covering 26 facilities |

### Columns

**Dropped:**

| Column | Reason |
| --- | --- |
| `content_table_id` | Identical to `source_content_id` |
| `organization_type` | Always `facility` |
| `address_country` | Always `India` (`address_countryCode` kept) |
| `countries` | 100% empty |
| `source` | Always `kie` |
| `coordinates` | GeoJSON copy of `latitude`/`longitude` (verified identical) |

**Added:**

| Column | Meaning |
| --- | --- |
| `name_short` | Short display name, set only when `name` looks like it contains an address |
| `officialPhone_type` | `mobile`, `landline`, `toll_free`, `shared_cost`, `foreign`, `other`, or `invalid` |
| `email_all` | All valid emails, when there was more than one |
| `officialWebsite_domain` | Domain of the website, used to spot directory sites |
| `dup_group_id` | Same value means the facilities are probably the same place |
| `dq_flags`, `dq_flag_count` | Every issue found or fix applied for the row |
| `crawl_*` | Only in enriched data; see "Crawler" |

In `silver_facilities`, list columns (`specialties`, `capability`, `procedure`, `equipment`, `source_urls`, …) are `array<string>`. Numbers, booleans, and dates get real types.

### Field rules

- **Text** (`name`, address lines, `description`): collapses whitespace, keeps line breaks, and repairs broken character codes. For example, `32 PEARLS\x00ae` becomes `32 PEARLS®` and `Shinde\x19s` becomes `Shinde’s`. These invisible control characters truncate CSV readers.
- **Phones**: converted to E.164 (`+91…`) with Google's libphonenumber using India's numbering plan, then typed (mobile, landline…). A stray `+` on a national number is read as Indian first. A number used by 3 or more facilities in 2 or more cities is treated as a directory or call-centre number.
- **Emails**: lower-cased, with `[at]`/`[dot]` decoded and `mailto:` stripped. Cloudflare "[email protected]" placeholders are flagged; the crawler can decode them.
- **Websites**: the first URL is kept. Directory, aggregator, and social sites (Practo, JustDial, Facebook, Google Maps links…) are flagged as not official.
- **Lists**: duplicates and empty items removed, original order kept. Free-text specialties are mapped onto the dataset's camelCase taxonomy where an exact match exists.
- **Categories**: `farmacy` becomes `pharmacy`, and `government` becomes `public`.
- **Year established**: removed if before 1600 or in the future.
- **Doctors / beds**: removed if implausible (5,000+ doctors, 10,000+ beds). Flagged as outliers above 1,000 doctors or 3,000 beds.
- **Dates**: only real ISO dates between 1990 and today are kept. Values like "9 months ago" become NULL and are flagged.
- **Geography**:
  - **City:** aliases normalised (Bangalore → Bengaluru, Gurgaon → Gurugram…).
  - **PIN:** must be 6 digits, not starting with 0.
  - **State:** resolved from names, abbreviations, and typos. When the state is missing, it's inferred from the PIN prefix (3- then 2-digit), learned from rows whose state is already known.
  - **City/PIN conflicts:** if PIN and city agree but contradict the state, the state is corrected. If they don't agree, the row is flagged.
  - **Coordinates:** dropped if outside India, and flagged if over 50 km from the median of the facility's city.
- **Likely duplicates**: facilities with the same normalised name, or the same non-shared phone, within 300 m of each other.

## Flag reference

Counts are from the full raw file. "How to use it" suggests how each flag should affect a recommendation's confidence.

| Flag | Facilities | Meaning | How to use it |
| --- | ---: | --- | --- |
| `coords_far_from_city` | 905 | Point is over 50 km from the city's other facilities | Location uncertain. `01b` checks this properly against India Post: use `coords_check` and `resolved_latitude` / `resolved_longitude` (see `docs/LOCATION_CHECKS.md`) |
| `name_contains_address` | 414 | Name includes address text | Cosmetic: show `name_short` |
| `email_obfuscated` | 363 | Email hidden by Cloudflare on the source page | Contact unknown, not missing |
| `website_not_official` | 325 | Website is a directory or social page | Weaker evidence than the facility's own site |
| `phone_shared_across_facilities` | 299 | Number shared by 3+ facilities in 2+ cities | Don't present it as a direct line |
| `state_inferred_from_pin` | 249 | State was empty; filled from the PIN | Usually reliable |
| `pin_invalid` | 80 | PIN missing or malformed | Location less certain |
| `state_pin_mismatch` | 36 | State and PIN disagree, and the city can't settle it | **Contradictory location**: lower confidence |
| `state_corrected` | 30 | State fixed because PIN and city agreed against it | Reliable after the fix |
| `row_repaired_multiline` | 30 | Record was split across lines and repaired | Complete after repair |
| `possible_duplicate` | 26 | Probably the same facility as others in its `dup_group_id` | Show once; may be merged records |
| `email_invalid` | 20 | An email couldn't be parsed and was dropped | – |
| `post_metrics_most_recent_social_media_post_date_unparseable` | 18 | Last-post date wasn't a real date | Recency unknown |
| `recency_of_page_update_unparseable` | 12 | Page-update date wasn't a real date | Recency unknown |
| `text_control_chars_repaired` | 11 | Broken character codes repaired in name or description | Cosmetic; a few names keep small leftovers |
| `state_inferred_from_city_or_pin` | 10 | State filled from the city or a 2-digit PIN prefix | Weaker inference |
| `numberDoctors_outlier` | 9 | 1,000+ doctors | Don't rank on size |
| `coords_outside_india_removed` | 6 | Coordinates were outside India and removed | No usable location point |
| `year_invalid` | 5 | Year was impossible and removed | – |
| `numberDoctors_implausible_removed` | 3 | 5,000+ doctors, removed | – |
| `capacity_outlier` | 2 | 3,000+ beds | Don't rank on size |
| `capacity_implausible_removed` | 1 | 10,000+ beds, removed | – |
| `row_truncated_in_source` | 30* | *Bronze-table path only:* lost its last 23 fields | Can't be matched on services; use the raw file |

## Coverage after cleaning

Every field is a claim extracted from web text, so coverage says what's present, not what's true.

| Field | Facilities with a value |
| --- | ---: |
| Description | 100% |
| Specialties | 100% |
| Capability (non-empty list) | 99.6% |
| Coordinates | 99.9% |
| PIN code | 99.2% |
| Official phone | 95.0% |
| Procedure (non-empty list) | 88.8% |
| Website | 83.9% |
| Email | 81.6% |
| Equipment (non-empty list) | 71.4% |
| Year established | 47.8% |
| Number of doctors | 36.4% |
| Bed capacity | 25.2% |

## Crawler (optional enrichment)

`tools/facility_crawler.py` visits each facility's **own** website to fill fields that are mostly empty: email (including Cloudflare-hidden ones), phones, year established, bed count, social links, and schema.org data.

- **Runs on a laptop.** Databricks Free Edition blocks most outbound web traffic.
- **Polite:**
  - follows each site's `robots.txt`, and skips a site whose `robots.txt` can't be reached (recorded as `robots_unreachable:<error>`, or `robots_server_error:<status>` for a 5xx; rerun with `--retry-failed` to try those sites again)
  - has at most `--concurrency` sites in progress at once, `robots.txt` requests included
  - honours `Crawl-delay` and makes one request at a time per site
  - identifies itself with your contact address
  - fetches at most 4 pages per site, 2 MB each
- **Skips aggregators.** Practo, JustDial and the other directory and social sites are not crawled; their terms usually forbid scraping.
- **Resumable and auditable.** Every page and result is stored in a local SQLite cache, which stays out of Git.
- **Bed counts** are the facility's current size only. Units ("a 10-bedded ICU"), history ("began in 1997 with 10 beds") and chain totals ("4,290 beds across 18 hospitals") are ignored.
- **Emails** on the site's own domain come first, so a web developer's stray address isn't picked.
- **Merge rules:**
  - fills **only empty fields** and never overwrites
  - records where each filled value came from in `crawl_filled_fields` and `crawl_sources`
  - copies nothing from a site it can't tie to the facility, and says why in `crawl_skip_reason`:

    | Reason | Meaning |
    | --- | --- |
    | `shared_website` | 3+ facilities list that domain (`--chain-threshold`), usually a chain's head office |
    | `name_mismatch` | The domain doesn't carry the facility's name: no distinctive word of it and not its initials (Ayesha Clinic listed with `jehangirhospital.com`). Generic names like "City Hospital" never match |
    | `redirected_elsewhere` | The site now forwards to a domain that doesn't carry the name either |

  - what was found for a skipped facility still goes in `crawl_phones_found` and `crawl_socials_found`
- **Changed the extraction rules?** `reextract --db crawl.sqlite --out enrichment.csv` re-runs them on the cached pages with no network; use it in place of `export`.

```bash
python tools/facility_crawler.py crawl  --input facilities_cleaned.csv --db crawl.sqlite --contact "you@andrew.cmu.edu" --limit 200
python tools/facility_crawler.py export --db crawl.sqlite --out enrichment.csv
python tools/facility_crawler.py merge  --input facilities_cleaned.csv --enrichment enrichment.csv --out facilities_enriched.csv
```

Upload `facilities_enriched.csv` to the `raw` volume, then rerun `01_clean_facilities` (it loads the enriched file instead of cleaning the raw data) and `01b_verify_locations`.

In a 50-site test on real websites, 38 sites returned data. The other 12 were mostly sites whose `robots.txt` forbids crawlers. Check a sample of the results before relying on them.

**Full run, 2026-11-22.**
- 5,213 of 8,060 sites crawled OK.
- Of the 2,847 that didn't:
  - 1,984: `robots.txt` couldn't be reached, mostly connection errors from dead or unreachable hosts;
  - about 390: `DecodingError`, a body that didn't match its `Content-Encoding`. It hit 422 unrelated hosts, so it may be on our side;
  - 315: HTTP errors;
  - 133: `robots.txt` said no or returned a server error.
- `merge` filled fields for 781 facilities: email 300, year established 306, bed count 230, official phone 45, Facebook link 29.
- It left 1,028 facilities unfilled because it couldn't tie the site to them: 791 `name_mismatch`, 184 `shared_website`, 53 `redirected_elsewhere`.
