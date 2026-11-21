"""Check every facility's location against the India Post PIN directory.

Each facility row has three location signals:

  * PIN     address_zipOrPostcode, looked up in the directory (its districts, state, office points)
  * city    address_city, matched to the directory's office and district names in the row's state
  * coords  latitude / longitude

Two signals agree when the places they point to are within AGREE_KM of each other. A signal that
agrees with at least one other is "confirmed". A signal that disagrees with two signals that agree
with each other is "contradicted": that field is probably wrong. The state is checked against the
PIN's state. Every row gets a plain-English `location_evidence` sentence that says what was found.

A row whose location cannot be found at all (its PIN is not in the directory and its city is not a
place in the directory) is removed. Removed rows are returned separately with the reason, so they
can be reviewed or restored.

Pure pandas / numpy: notebooks/01b_verify_locations.py runs it on Databricks against the shared
india_post_pincode_directory table, and tools/verify_locations.py runs it on a laptop.
"""

from __future__ import annotations

import bisect
import difflib
import re

import numpy as np
import pandas as pd

from . import cleaning

AGREE_KM = 25.0          # two signals within this distance point to the same place
FUZZY_CUTOFF = 0.86      # minimum similarity for a misspelt city name ("Bhubaneshwar")
EMPTY = np.empty((0, 2))

# Column names seen in copies of the India Post directory (data.gov.in export, indiapins, ...)
DIRECTORY_COLUMNS = {
    "pincode": ["pincode", "pin_code", "pin", "postal_code", "zipcode"],
    "office": ["officename", "office_name", "name", "post_office", "postoffice", "office"],
    "district": ["district", "districtname", "district_name"],
    "state": ["statename", "state_name", "state"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "long", "lon", "lng"],
}

# words dropped from place names before matching: post-office type codes and direction / size words
_POST_OFFICE_CODES = re.compile(
    r"\b(b\.?\s?o|s\.?\s?o|h\.?\s?o|g\.?\s?p\.?\s?o|n\.?\s?d\.?\s?s\.?\s?o|p\.?\s?o|mdg|post)\b\.?")
_EDGE_WORDS = {"east", "west", "north", "south", "central", "urban", "rural", "suburban", "city",
               "district", "dist", "cantonment", "cantt", "e", "w"}
GENERIC_TOKENS = {"nagar", "new", "old", "kasba", "thana", "mandi", "bazar", "bazaar", "road",
                  "colony", "sector", "camp", "station", "main", "sahib", "sadar", "town", "gram",
                  "pimple", "puram", "pura", "pur", "ganj", "khurd", "kalan", "chowk", "market"}
OFFICE_COORD_KM = 150.0   # an office further than this from its district's centre has bad coordinates
NEAR_OFFICE_KM = 15.0     # coordinates this close to an India Post office in the row's state are a real place

# same place, different names (renamed cities, old spellings, a locality and its district)
CITY_ALIASES = [
    ("prayagraj", "allahabad"), ("bengaluru", "bangalore"), ("gurugram", "gurgaon"),
    ("mysuru", "mysore"), ("kochi", "cochin", "ernakulam"), ("thiruvananthapuram", "trivandrum"),
    ("hubballi", "hubli", "dharwad"), ("belagavi", "belgaum"), ("kalaburagi", "gulbarga"),
    ("mangaluru", "mangalore", "dakshina kannada"), ("chhatrapati sambhajinagar", "aurangabad"),
    ("dharashiv", "osmanabad"), ("ahilyanagar", "ahmednagar", "ahmadnagar"),
    ("puducherry", "pondicherry"), ("vadodara", "baroda"), ("kozhikode", "calicut"),
    ("visakhapatnam", "vizag", "vishakhapatnam"), ("kolkata", "calcutta"), ("mumbai", "bombay"),
    ("chennai", "madras"), ("shivamogga", "shimoga"), ("tumakuru", "tumkur"),
    ("vijayapura", "bijapur"), ("ballari", "bellary"), ("kanpur", "kanpur nagar"),
    ("thrissur", "trichur"), ("tiruchirappalli", "trichy", "tiruchirapalli"),
    ("thoothukudi", "tuticorin"), ("nashik", "nasik"),
    ("mohali", "sahibzada ajit singh nagar", "sas nagar", "s a s nagar"), ("panaji", "panjim"),
    ("alappuzha", "alleppey"), ("deesa", "disa"), ("kollam", "quilon"), ("kannur", "cannanore"),
    ("palakkad", "palghat"), ("thalassery", "tellicherry"), ("chikkamagaluru", "chikmagalur"),
    ("kanniyakumari", "kanyakumari"),
    ("rajamahendravaram", "rajahmundry"), ("kadapa", "cuddapah"), ("bathinda", "bhatinda"),
    ("hisar", "hissar"), ("sonipat", "sonepat"), ("dehradun", "dehra dun"),
    ("bhubaneswar", "bhubaneshwar"), ("hazaribagh", "hazaribag"), ("davanagere", "davangere"),
    ("navi mumbai", "vashi", "thane"), ("greater noida", "noida", "gautam buddha nagar"),
    ("pimpri chinchwad", "pimpri", "chinchwad"), ("berhampore", "baharampur", "murshidabad"),
    ("bardhaman", "burdwan", "purba bardhaman"), ("sri ganganagar", "ganganagar"),
    ("muktsar", "sri muktsar sahib"), ("hospet", "hosapete"), ("agartala", "tripura"),
    ("dombivli", "dombivali"), ("indirapuram", "ghaziabad"), ("thiruvalla", "tiruvalla"),
    ("delhi", "new delhi"),
]
_ALIASES: dict[str, list[str]] = {}
for _group in CITY_ALIASES:
    for _name in _group:
        _ALIASES.setdefault(_name, []).extend(n for n in _group if n != _name)

_PIN_IN_TEXT = re.compile(r"(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)")
_CHECK_FLAGS = {
    "pin_check": {"not_found": "pin_not_in_directory", "contradicted": "pin_contradicted"},
    "city_check": {"not_found": "city_not_in_directory", "contradicted": "city_contradicted"},
    "coords_check": {"contradicted": "coords_contradicted"},
    "state_check": {"contradicted": "state_contradicted"},
    "location_status": {"conflicting": "location_conflicting", "unverified": "location_unverified"},
}
RESULT_COLUMNS = [
    "location_status", "location_confidence", "location_evidence",
    "pin_check", "city_check", "coords_check", "state_check",
    "pin_district", "pin_state", "city_match", "coords_to_pin_km", "coords_to_city_km",
    "resolved_latitude", "resolved_longitude", "resolved_location_source",
    "address_text_pin",
]


def place_key(name):
    """'Bengaluru Urban', 'Anna Nagar S.O', 'Dombivli East' -> 'bengaluru', 'anna nagar', 'dombivli'."""
    if not isinstance(name, str) or not name.strip():
        return None
    s = re.sub(r"\(.*?\)", " ", name.lower())
    s = _POST_OFFICE_CODES.sub(" ", s)
    words = re.sub(r"[^a-z]+", " ", s).split()
    full = list(words)
    while len(words) > 1 and words[-1] in _EDGE_WORDS:     # "Dombivli East", "Bengaluru Urban"
        words.pop()
    while len(words) > 1 and words[0] in _EDGE_WORDS:      # "North 24 Parganas", "South Delhi"
        words.pop(0)
    if len(words) == 1 and (len(words[0]) < 4 or words[0] in GENERIC_TOKENS):
        words = full                                        # keep "New Town" whole
    return " ".join(words) or None


def _pin_text(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return cleaning.clean_pin(s)


def _directory_state(v):
    if not isinstance(v, str) or not v.strip():
        return None
    return cleaning.resolve_state(re.sub(r"^the\s+", "", v.strip(), flags=re.I))


def normalize_directory(raw: pd.DataFrame) -> pd.DataFrame:
    """Any copy of the India Post directory -> columns pincode, office, district, state, lat, lon."""
    by_key = {re.sub(r"[\s_]", "", str(c).lower()): c for c in raw.columns}
    picked = {}
    for want, options in DIRECTORY_COLUMNS.items():
        for option in options:
            if option.replace("_", "") in by_key:
                picked[want] = by_key[option.replace("_", "")]
                break
    absent = [w for w in ("pincode", "office", "district", "state") if w not in picked]
    if absent:
        raise ValueError(f"PIN directory is missing {absent}; its columns are {list(raw.columns)}. "
                         "Add the right names to location_check.DIRECTORY_COLUMNS.")
    d = pd.DataFrame({w: raw[c].to_numpy() for w, c in picked.items()})
    d["pincode"] = d["pincode"].map(_pin_text)
    d["state"] = d["state"].map(_directory_state)
    for c in ("office", "district"):
        d[c] = d[c].where(d[c].map(lambda v: isinstance(v, str) and bool(v.strip())))
    lat = pd.to_numeric(d.get("latitude"), errors="coerce") if "latitude" in d else np.nan
    lon = pd.to_numeric(d.get("longitude"), errors="coerce") if "longitude" in d else np.nan
    d["lat"], d["lon"] = lat, lon
    inside = d.lat.between(*cleaning.INDIA_BBOX[:2]) & d.lon.between(*cleaning.INDIA_BBOX[2:])
    d.loc[~inside, ["lat", "lon"]] = np.nan
    # a few thousand offices carry another place's coordinates: drop coordinates that sit far from
    # the rest of their district (the office itself, its name and PIN are kept)
    grp = [d.state.fillna(""), d.district.fillna("")]
    centre = d.groupby(grp)[["lat", "lon"]].transform("median")
    off = pd.Series(cleaning.haversine_km(d.lat, d.lon, centre.lat, centre.lon), index=d.index)
    spread = off.groupby(grp).transform("median")
    bad = off > np.maximum(OFFICE_COORD_KM, 3 * spread.fillna(0))
    d.loc[bad, ["lat", "lon"]] = np.nan
    d["okey"] = d.office.map(place_key)
    d["dkey"] = d.district.map(place_key)
    return d.dropna(subset=["pincode"])[["pincode", "office", "district", "state", "lat", "lon",
                                         "okey", "dkey"]].reset_index(drop=True)


def _group_points(df: pd.DataFrame, by: list[str]) -> dict:
    """{group key tuple: (n, 2) array of the group's non-null lat/lon} without a per-group loop."""
    x = df.sort_values(by, kind="stable").reset_index(drop=True)
    keys = list(x[by].itertuples(index=False, name=None))
    starts = np.flatnonzero([True] + [a != b for a, b in zip(keys[1:], keys[:-1])])
    pts = x[["lat", "lon"]].to_numpy(dtype=float)
    out = {}
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(x)
        p = pts[start:end]
        out[keys[start]] = p[~np.isnan(p).any(axis=1)]
    return out


def _min_km(lat, lon, pts):
    if pts.size == 0 or lat is None or lon is None or np.isnan(lat) or np.isnan(lon):
        return np.nan
    return float(cleaning.haversine_km(lat, lon, pts[:, 0], pts[:, 1]).min())


class Directory:
    """Lookups over a normalised directory: PIN -> places, and (state, place name) -> points."""

    def __init__(self, d: pd.DataFrame):
        self.has_coords = bool(d.lat.notna().any())

        def sets(col, fn=None):
            x = d[["pincode", col]].dropna().drop_duplicates()
            if fn:
                x = x.assign(**{col: x[col].map(fn)}).drop_duplicates()
            return x.groupby("pincode")[col].agg(lambda s: sorted(s)).to_dict()

        self.pin_states = sets("state")
        self.pin_districts = sets("district", str.title)
        self.pin_dkeys = {k: set(v) for k, v in sets("dkey").items()}
        self.pin_okeys = {k: set(v) for k, v in sets("okey").items()}
        for pin in d.pincode.unique():
            self.pin_states.setdefault(pin, [])
            self.pin_districts.setdefault(pin, [])
            self.pin_dkeys.setdefault(pin, set())
            self.pin_okeys.setdefault(pin, set())
        self.pin_points = _group_points(d, ["pincode"])
        self.pin_points = {k[0]: v for k, v in self.pin_points.items()}
        self.pin_centre = {pin: (tuple(np.median(p, axis=0)) if len(p) else (np.nan, np.nan))
                           for pin, p in self.pin_points.items()}
        # (state, place name) -> points of every office with that name or in that district
        long = pd.concat([d[["state", "okey", "lat", "lon"]].rename(columns={"okey": "key"}),
                          d[["state", "dkey", "lat", "lon"]].rename(columns={"dkey": "key"})])
        long = long.dropna(subset=["state", "key"])
        self.places: dict[str, dict[str, np.ndarray]] = {}
        for (state, key), pts in _group_points(long, ["state", "key"]).items():
            self.places.setdefault(state, {})[key] = pts
        self.names = {s: sorted(p) for s, p in self.places.items()}
        # state that nearly all PINs with the same first three digits belong to (catches directory
        # rows filed under the wrong state, e.g. an Agartala office listed under a Telangana district)
        ps = d[["pincode", "state"]].dropna().drop_duplicates()
        ps = ps.assign(prefix=ps.pincode.str[:3])
        counts = ps.groupby(["prefix", "state"]).size()
        share = counts / counts.groupby(level=0).transform("sum")
        top = share[share >= 0.9].reset_index()
        self.prefix_state = dict(zip(top.prefix, top.state))
        offices = d.dropna(subset=["lat", "lon"])
        self.office_pts = offices[["lat", "lon"]].to_numpy(dtype=float)
        self.office_state = offices.state.to_numpy()
        self.office_name = offices.office.to_numpy()
        self._cache: dict = {}

    def _in_state(self, keys, state, loose=True):
        places, names = self.places[state], self.names[state]
        for i, k in enumerate(keys):
            if k in places:
                return ("exact" if i == 0 else "alias:" + k), places[k]
        if not loose:
            return None
        for k in keys:                                     # "Rohini" -> "rohini sector 3"
            if len(k) < 4 or k in GENERIC_TOKENS:
                continue
            lo = bisect.bisect_left(names, k + " ")
            hits = []
            while lo < len(names) and names[lo].startswith(k + " "):
                hits.append(places[names[lo]])
                lo += 1
            if hits:
                return "prefix:" + k, np.vstack(hits)
        k = keys[0]
        if len(k) >= 5:                                    # "Bhubaneshwar" -> "bhubaneswar"
            close = [c for c in difflib.get_close_matches(k, names, n=3, cutoff=FUZZY_CUTOFF)
                     if c[0] == k[0]]
            if close:
                return "fuzzy:" + close[0], places[close[0]]
        for tok in sorted(k.split(), key=len, reverse=True):   # "Greater Noida" -> "noida"
            if len(tok) >= 5 and tok != k and tok not in GENERIC_TOKENS and tok in places:
                return "partial:" + tok, places[tok]
        return None

    def find_city(self, city, state, also_states=(), address=()):
        """Where is this city? Returns (how it matched, state it was found in, points).

        Looks in the row's state first, then in the PIN's states, then (exact names only) in every
        other state. When the city is not found, each comma-separated part of the address lines is
        tried as a place name in the row's state. (None, None, EMPTY) when nothing matches.
        """
        k = place_key(city)
        cache_key = (k, state, tuple(also_states), tuple(address))
        if cache_key in self._cache:
            return self._cache[cache_key]
        found = (None, None, EMPTY)
        keys = [k, *_ALIASES.get(k, [])] if k else []
        if keys and state in self.places:
            hit = self._in_state(keys, state)
            if hit:
                found = (hit[0], state, hit[1])
        if keys and found[0] is None:
            for st in also_states:
                if st != state and st in self.places:
                    hit = self._in_state(keys, st)
                    if hit:
                        found = ("other_state:" + st, st, hit[1])
                        break
        if keys and found[0] is None:
            elsewhere = [s for s in self.places if s != state and any(x in self.places[s] for x in keys)]
            if elsewhere:
                pts = np.vstack([self.places[s][x] for s in elsewhere for x in keys if x in self.places[s]])
                found = ("other_state:" + ", ".join(elsewhere[:3]), elsewhere[0], pts)
        if found[0] is None and state in self.places:
            for line in address:
                if not isinstance(line, str):
                    continue
                for part in re.split(r"[,;/]|\s-\s", line):
                    pk = place_key(part)
                    if pk and len(pk) >= 4 and pk not in GENERIC_TOKENS:
                        hit = self._in_state([pk, *_ALIASES.get(pk, [])], state, loose=False)
                        if hit:
                            found = ("address:" + pk, state, hit[1])
                            break
                if found[0] is not None:
                    break
        self._cache[cache_key] = found
        return found

    def nearest_office(self, lat, lon):
        """(distance km, office name, state) of the closest office with coordinates."""
        if not len(self.office_pts) or pd.isna(lat) or pd.isna(lon):
            return np.nan, None, None
        dist = cleaning.haversine_km(lat, lon, self.office_pts[:, 0], self.office_pts[:, 1])
        i = int(np.argmin(dist))
        return float(dist[i]), self.office_name[i], self.office_state[i]


def _pins_in_text(*texts):
    out = []
    for t in texts:
        if isinstance(t, str):
            out += [a + b for a, b in _PIN_IN_TEXT.findall(t)]
    return out


def _check_row(r, directory: Directory, agree_km: float):
    pin, city, state = r["address_zipOrPostcode"], r["address_city"], r["address_stateOrRegion"]
    lat = pd.to_numeric(r["latitude"], errors="coerce")
    lon = pd.to_numeric(r["longitude"], errors="coerce")
    has_coords = not (pd.isna(lat) or pd.isna(lon))
    pin = _pin_text(pin)
    pin_found = pin in directory.pin_states
    match, city_state, city_pts = directory.find_city(
        city, state, also_states=directory.pin_states.get(pin, []) if pin_found else (),
        address=(r.get("address_line1"), r.get("address_line2"), r.get("address_line3")))
    city_found = match is not None

    out = {c: None for c in RESULT_COLUMNS}
    out["pin_check"] = "missing" if pin is None else ("unconfirmed" if pin_found else "not_found")
    out["city_check"] = ("missing" if place_key(city) is None
                         else ("unconfirmed" if city_found else "not_found"))
    out["coords_check"] = "unconfirmed" if has_coords else "missing"
    out["city_match"] = match
    if pin_found:
        out["pin_district"] = ", ".join(directory.pin_districts[pin]) or None
        out["pin_state"] = ", ".join(directory.pin_states[pin]) or None

    # pairwise agreement (None = cannot be compared)
    pin_pts = directory.pin_points.get(pin, EMPTY) if pin_found else EMPTY
    plat, plon = directory.pin_centre.get(pin, (np.nan, np.nan)) if pin_found else (np.nan, np.nan)
    d_pg = _min_km(lat, lon, pin_pts) if pin_found and has_coords else np.nan
    d_cg = _min_km(lat, lon, city_pts) if city_found and has_coords else np.nan
    out["coords_to_pin_km"] = None if np.isnan(d_pg) else round(d_pg, 1)
    out["coords_to_city_km"] = None if np.isnan(d_cg) else round(d_cg, 1)
    agree = {}
    if pin_found and has_coords and not np.isnan(d_pg):
        agree[("pin", "coords")] = d_pg <= agree_km
    if city_found and has_coords and not np.isnan(d_cg):
        agree[("city", "coords")] = d_cg <= agree_km
    if pin_found and city_found:
        keys = {place_key(city), *_ALIASES.get(place_key(city), [])}
        if match and ":" in match and not match.startswith("other_state"):
            keys.add(match.split(":", 1)[1])
        by_name = bool(keys & (directory.pin_dkeys[pin] | directory.pin_okeys[pin]))
        d_pc = _min_km(plat, plon, city_pts)
        if by_name:
            agree[("pin", "city")] = True
        elif not np.isnan(d_pc):
            agree[("pin", "city")] = d_pc <= agree_km

    available = [s for s, ok in (("pin", pin_found), ("city", city_found), ("coords", has_coords)) if ok]

    def agrees(a, b):
        return agree.get((a, b), agree.get((b, a)))

    verdict = {}
    for s in available:
        others = [o for o in available if o != s]
        if any(agrees(s, o) for o in others):
            verdict[s] = "confirmed"
        elif len(others) == 2 and agrees(*others) and all(agrees(s, o) is False for o in others):
            verdict[s] = "contradicted"
        else:
            verdict[s] = "unconfirmed"
    for s, v in verdict.items():
        out[s + "_check"] = v
    confirmed = [s for s, v in verdict.items() if v == "confirmed"]

    # state: the PIN decides when it is trustworthy, otherwise the city name
    prefix_state = directory.prefix_state.get(pin[:3]) if pin else None
    pin_record_suspect = bool(pin_found and prefix_state and directory.pin_states[pin]
                              and prefix_state not in directory.pin_states[pin] and state == prefix_state)
    out["_pin_record_suspect"] = prefix_state if pin_record_suspect else None
    if pin_found and directory.pin_states[pin]:
        if state in directory.pin_states[pin] or (pin_record_suspect and state == prefix_state):
            out["state_check"] = "confirmed"
        elif verdict.get("pin") == "confirmed":
            out["state_check"] = "contradicted"
        else:
            out["state_check"] = "unconfirmed"
    elif city_found:
        out["state_check"] = ("confirmed" if city_state == state
                              else "contradicted" if verdict.get("city") == "confirmed" else "unconfirmed")
    else:
        out["state_check"] = "unconfirmed"

    near = (np.nan, None, None)
    if not pin_found and not city_found and has_coords:
        near = directory.nearest_office(lat, lon)    # do the coordinates at least sit in a real place?
    out["_near_office"] = near
    if not pin_found and not city_found and not (near[0] <= NEAR_OFFICE_KM and near[2] == state):
        status = "not_found"
    elif len(available) < 2:
        status = "unverified"
    elif len(confirmed) < 2:
        status = "conflicting"
    elif len(confirmed) == len(available) and out["pin_check"] != "not_found" and out["city_check"] != "not_found":
        status = "verified"
    else:
        status = "partly_verified"
    out["location_status"] = status
    out["location_confidence"] = ("high" if len(confirmed) == 3
                                  else "medium" if len(confirmed) == 2 else "low")

    # best location to measure distances from
    if verdict.get("coords") == "confirmed":
        out["resolved_latitude"], out["resolved_longitude"] = float(lat), float(lon)
        out["resolved_location_source"] = "facility_coordinates"
    elif verdict.get("pin") == "confirmed" and not np.isnan(plat):
        out["resolved_latitude"], out["resolved_longitude"] = float(plat), float(plon)
        out["resolved_location_source"] = "pin_area"
    elif has_coords and status != "not_found":
        out["resolved_latitude"], out["resolved_longitude"] = float(lat), float(lon)
        out["resolved_location_source"] = "facility_coordinates_unverified"
    elif pin_found and not np.isnan(plat):
        out["resolved_latitude"], out["resolved_longitude"] = float(plat), float(plon)
        out["resolved_location_source"] = "pin_area_unverified"

    text_pins = [p for p in _pins_in_text(r.get("address_line1"), r.get("address_line2"),
                                          r.get("address_line3")) if p != pin]
    out["address_text_pin"] = text_pins[0] if text_pins else None

    out["location_evidence"] = _evidence(r, pin, pin_found, out, match, city_state, verdict)
    return out


def _evidence(r, pin, pin_found, out, match, city_state, verdict):
    city, state = r["address_city"], r["address_stateOrRegion"]
    parts = []
    if pin is None:
        parts.append("No valid PIN.")
    elif pin_found:
        if out["pin_district"] or out["pin_state"]:
            parts.append(f"PIN {pin} is in {out['pin_district'] or 'an unnamed district'}, "
                         f"{out['pin_state'] or 'an unnamed state'} (India Post).")
        else:
            parts.append(f"PIN {pin} is in the India Post directory, without a district or state.")
        if out.get("_pin_record_suspect"):
            parts.append(f"That India Post record looks wrong: other PINs starting {pin[:3]} are in "
                         f"{out['_pin_record_suspect']}.")
    else:
        parts.append(f"PIN {pin} is not in the India Post directory.")
    if match is None:
        parts.append(f"City '{city}' is not an India Post place in {state}." if place_key(city) else "No city.")
    elif match.startswith("address:"):
        parts.append(f"City '{city}' is not an India Post place in {state}, but the address part "
                     f"'{match.split(':', 1)[1]}' is.")
    elif match.startswith("other_state"):
        parts.append(f"City '{city}' is an India Post place in {match.split(':', 1)[1]}, not {state}.")
    else:
        how = "" if match == "exact" else f" (matched as '{match.split(':', 1)[1]}')"
        parts.append(f"City '{city}' is an India Post place in {city_state}{how}.")
    if out["coords_check"] != "missing":
        dists = []
        if out["coords_to_pin_km"] is not None:
            dists.append(f"{out['coords_to_pin_km']:g} km from the PIN area")
        if out["coords_to_city_km"] is not None:
            dists.append(f"{out['coords_to_city_km']:g} km from '{city}'")
        if dists:
            parts.append("Coordinates are " + " and ".join(dists) + ".")
    else:
        parts.append("No coordinates.")
    near = out.get("_near_office") or (np.nan,)
    if not np.isnan(near[0]) and near[0] <= NEAR_OFFICE_KM and out["location_status"] != "not_found":
        parts.append(f"The coordinates are {near[0]:.1f} km from the {near[1]} post office ({near[2]}).")
    wrong = [s for s, v in verdict.items() if v == "contradicted"]
    label = {"pin": "PIN", "city": "city", "coords": "coordinates"}
    if out["state_check"] == "contradicted":
        wrong.append("state")
        label["state"] = f"state ({state})"
    if out["location_status"] == "not_found":
        parts.append("Location could not be found, so the row is removed.")
    elif wrong:
        plural = len(wrong) > 1 or wrong == ["coords"]
        parts.append("The other fields agree, so the " + " and ".join(label[w] for w in wrong)
                     + (" are" if plural else " is") + " probably wrong.")
    elif out["location_status"] == "conflicting":
        parts.append("The location fields point to different places; the location is uncertain.")
    elif out["location_status"] == "unverified":
        parts.append("Only one location field could be checked.")
    if out["address_text_pin"]:
        parts.append(f"The address text mentions another PIN, {out['address_text_pin']}.")
    return " ".join(parts)


def _add_flags(existing, row):
    flags = [f for f in (existing if isinstance(existing, str) else "").split(";") if f]
    for col, mapping in _CHECK_FLAGS.items():
        flag = mapping.get(row[col])
        if flag and flag not in flags:
            flags.append(flag)
    if row["address_text_pin"] and "address_text_pin_differs" not in flags:
        flags.append("address_text_pin_differs")
    return ";".join(flags) or None


def verify(facilities: pd.DataFrame, directory, agree_km: float = AGREE_KM,
           remove_statuses=("not_found",)):
    """Check each facility's location.

    `directory` is a raw directory frame or a Directory. Rows whose location_status is in
    `remove_statuses` get keep = False (add "conflicting" to also drop rows whose location fields
    point to different places). Returns (results, summary): `results` has one row per facility with
    unique_id, keep, the RESULT_COLUMNS and updated dq_flags / dq_flag_count.
    """
    if not isinstance(directory, Directory):
        directory = Directory(normalize_directory(directory))
    need = ["unique_id", "address_zipOrPostcode", "address_city", "address_stateOrRegion",
            "latitude", "longitude", "address_line1", "address_line2", "address_line3", "dq_flags"]
    absent = [c for c in need if c not in facilities.columns]
    if absent:
        raise ValueError(f"facilities are missing columns {absent}")
    frame = facilities[need].astype(object)
    frame = frame.where(frame.notna(), None)                 # Spark nulls arrive as NaN
    rows = [_check_row(r, directory, agree_km) for r in frame.to_dict("records")]
    res = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    res.insert(0, "unique_id", facilities["unique_id"].to_numpy())
    res.insert(1, "keep", (~res.location_status.isin(list(remove_statuses))).to_numpy())
    res["dq_flags"] = [_add_flags(f, r) for f, r in zip(frame["dq_flags"], rows)]
    res["dq_flag_count"] = [len(s.split(";")) if isinstance(s, str) else 0 for s in res.dq_flags]
    summary = {
        "facilities": len(res),
        "removed": int((~res.keep).sum()),
        "removed_statuses": list(remove_statuses),
        "location_status": res.location_status.value_counts().to_dict(),
        "location_confidence": res[res.keep].location_confidence.value_counts().to_dict(),
        **{c: res[res.keep][c].value_counts().to_dict()
           for c in ("pin_check", "city_check", "coords_check", "state_check")},
        "resolved_location_source": res[res.keep].resolved_location_source.value_counts().to_dict(),
        "directory_has_coordinates": directory.has_coords,
    }
    return res, summary


def apply(facilities: pd.DataFrame, results: pd.DataFrame):
    """Facility frame + verify() results -> (kept rows with the new columns, removed rows)."""
    base = facilities.drop(columns=[c for c in ("dq_flags", "dq_flag_count", *RESULT_COLUMNS)
                                    if c in facilities.columns])
    merged = base.merge(results, on="unique_id", how="left", validate="one_to_one")
    kept = merged[merged.keep].drop(columns="keep").reset_index(drop=True)
    removed = merged[~merged.keep].drop(columns="keep").reset_index(drop=True)
    return kept, removed


COLUMN_COMMENTS = {
    "location_status": "verified: every location field agrees | partly_verified: two agree, another is "
                       "wrong or not in India Post | conflicting: the fields point to different places | "
                       "unverified: only one field could be checked",
    "location_confidence": "high: PIN, city and coordinates agree | medium: two agree | low: fewer",
    "location_evidence": "Plain-English account of the location checks against India Post: quote it",
    "pin_check": "PIN vs India Post: confirmed, contradicted (probably wrong), unconfirmed, not_found, missing",
    "city_check": "City vs India Post place names: confirmed, contradicted, unconfirmed, not_found, missing",
    "coords_check": "Coordinates vs the PIN and city: confirmed, contradicted, unconfirmed, missing",
    "state_check": "State vs the PIN's state: confirmed, contradicted, unconfirmed",
    "pin_district": "District(s) India Post lists for the PIN",
    "pin_state": "State(s) India Post lists for the PIN",
    "city_match": "How the city matched India Post: exact, alias:, prefix:, fuzzy:, partial:, address:, other_state:",
    "coords_to_pin_km": "Distance from the coordinates to the nearest post office with this PIN",
    "coords_to_city_km": "Distance from the coordinates to the nearest India Post place with the city's name",
    "resolved_latitude": "Best latitude for distance ranking (see resolved_location_source)",
    "resolved_longitude": "Best longitude for distance ranking (see resolved_location_source)",
    "resolved_location_source": "facility_coordinates, pin_area (coordinates were wrong) or *_unverified",
    "address_text_pin": "A different PIN written in the address lines, if any",
}


def load_indiapins() -> pd.DataFrame:
    """The India Post directory bundled with the `indiapins` package (for laptop runs and tests)."""
    import bz2
    import importlib.resources
    import json

    path = importlib.resources.files("indiapins") / "pins.json.bz2"
    with bz2.open(path, "rt", encoding="utf-8") as fh:
        return pd.DataFrame([json.loads(line) for line in fh if line.strip()])
