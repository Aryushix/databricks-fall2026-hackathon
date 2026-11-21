# Location checks

`notebooks/01b_verify_locations` checks where every facility is, using the challenge's
`india_post_pincode_directory` table (about 165,000 post offices: PIN, office name, district,
state and coordinates). The rules live in `src/hackathon/location_check.py`. The laptop command
`tools/verify_locations.py` runs the same rules on a CSV.

Run it after `01_clean_facilities`. It writes:
- `silver_facilities_verified`: the facilities whose location exists, with the columns below.
  **Build the recommender on this table.**
- `silver_facilities_location_removed`: the facilities whose location could not be found, with the reason.

## Results

From a laptop run on all 9,989 cleaned facilities. The laptop run used the India Post copy that
ships with the `indiapins` package (last updated February 2026), so the Databricks run on the
challenge's table may differ slightly.

| `location_status` | Facilities | Meaning |
| --- | ---: | --- |
| `verified` | 7,978 | Every location field agrees (7,904 of them have all three: PIN, city and coordinates) |
| `partly_verified` | 1,637 | Two fields agree; the third is probably wrong, or not in India Post |
| `conflicting` | 372 | The fields point to different places: we don't know where the facility is |
| `unverified` | 2 | Only one field could be checked |
| `not_found` (removed) | 0 | Every facility could be tied to at least one real India Post place |

Field by field:

| Field | Confirmed | Probably wrong (`contradicted`) | Not in India Post | Couldn't tell / missing |
| --- | ---: | ---: | ---: | ---: |
| PIN | 9,177 | 185 | 205 | 342 / 80 |
| City | 9,255 | 289 | 77 | 368 / 0 |
| Coordinates | 8,702 | 811 | n/a | 470 / 6 |
| State | 9,957 | 17 | n/a | 15 / 0 |

So **811 facilities have coordinates in the wrong place**. Example: a hospital in Aurangabad whose
coordinates are in Mumbai, 277 km away. For these and the other partly verified rows, the PIN area
becomes the point to measure from (`resolved_location_source = pin_area`, 823 facilities).

## How it works

Each facility has three location signals:

| Signal | Looked up as |
| --- | --- |
| PIN (`address_zipOrPostcode`) | Its post offices: district, state and coordinates |
| City (`address_city`, in `address_stateOrRegion`) | Post offices and districts with that name in that state |
| Coordinates (`latitude`, `longitude`) | The point itself |

1. **Match the city.** The city is matched to India Post place names, trying each of these in turn:
   - the exact name;
   - a known alias (Allahabad = Prayagraj, Bombay = Mumbai, Navi Mumbai → Vashi; the list is
     `CITY_ALIASES`);
   - a place whose name starts with the city ("Rohini" → "Rohini Sector 3");
   - a close spelling ("Bhubaneshwar" → "Bhubaneswar");
   - one word of the name ("Greater Noida" → "Noida");
   - the PIN's state;
   - any other state.

   If the city still isn't found, each part of the address lines is tried as a place name.
   `city_match` records which of these worked.
2. **Compare the signals in pairs.** Two signals agree when they point to places within 25 km of
   each other (`AGREE_KM`). A PIN and a city also agree when the city is one of the PIN's district
   or office names.
3. **Judge each field.**
   - `confirmed`: agrees with at least one other field.
   - `contradicted`: the other two fields agree with each other but not with this one, so this
     field is probably wrong.
   - `unconfirmed`: nothing to compare it with, or nothing agrees.
4. **Check the state** against the PIN's state. The PIN only overrules the state when the PIN itself
   is confirmed.
5. **Decide whether the location exists.** A row is removed (`not_found`) only when all three of these hold:
   - its PIN is not in India Post;
   - its city isn't an India Post place;
   - its coordinates are missing or more than 15 km from any post office in the row's state.
6. **Pick the point to measure from.**
   - If the coordinates are confirmed, use them.
   - Otherwise, if the PIN is confirmed, use the PIN area (the median of its post offices).
   - Otherwise, keep the coordinates, marked `_unverified`.

### Errors in the India Post table, and how they're handled

- About 5,600 post offices have coordinates that belong somewhere else: more than 150 km from the
  rest of their district (`OFFICE_COORD_KM`). Those coordinates are ignored; the office itself is kept.
- A few PINs are listed under the wrong state. Example: Agartala's 799001 appears under a Telangana
  district. When nearly every PIN with the same first three digits is in one state, and the facility
  says that state, the facility is not blamed. `location_evidence` says the India Post record looks wrong.

## Columns added

| Column | What it holds |
| --- | --- |
| `location_status`, `location_confidence` | The verdict above; confidence is `high` (3 fields agree), `medium` (2) or `low` |
| `location_evidence` | One plain-English paragraph, for example: *"PIN 431009 is in Aurangabad, Maharashtra (India Post). City 'Aurangabad' is an India Post place in Maharashtra. Coordinates are 276.7 km from the PIN area and 179.1 km from 'Aurangabad'. The other fields agree, so the coordinates are probably wrong."* Quote it in explanations. |
| `pin_check`, `city_check`, `coords_check`, `state_check` | `confirmed`, `contradicted`, `unconfirmed`, `not_found` or `missing` |
| `pin_district`, `pin_state` | What India Post says about the PIN |
| `city_match` | How the city was matched (see step 1) |
| `coords_to_pin_km`, `coords_to_city_km` | The distances behind the verdict |
| `resolved_latitude`, `resolved_longitude`, `resolved_location_source` | The point to measure distances from |
| `address_text_pin` | A different PIN written in the address lines, if any |

New `dq_flags` values:
- `pin_not_in_directory`, `pin_contradicted`
- `city_not_in_directory`, `city_contradicted`
- `coords_contradicted`, `state_contradicted`
- `location_conflicting`, `location_unverified`
- `address_text_pin_differs`

## How to use it in the recommender

- Measure distance from `resolved_latitude` / `resolved_longitude`, never from the raw coordinates.
- Rank `low` confidence rows lower, or show them with a warning such as *"Location uncertain: the
  listed PIN is in Pune but the city is Ratnagiri"*.
- To drop `conflicting` rows as well, set `REMOVE_STATUSES = ["not_found", "conflicting"]` in
  `01b` (or pass `--also-remove conflicting` to the laptop command).

## What this does not check

- **Only location fields are checked.** Services, specialties, phone numbers and the description
  can't be verified from a location. They are claims extracted from the web: `dq_flags` marks the
  doubtful ones, and the crawler (`tools/facility_crawler.py`) fills gaps from the facility's own
  website, recording the source page for each value it adds.
- **The checks aren't fully independent.** If the source geocoded a facility's coordinates from its
  address, the PIN and the coordinates are not independent witnesses. "Verified" means the fields
  are consistent with each other and with India Post, not that someone visited the facility.
- **25 km is a judgement call.** It is wide enough for a rural PIN area or a large city, so two
  places in the same metro area count as agreeing.
