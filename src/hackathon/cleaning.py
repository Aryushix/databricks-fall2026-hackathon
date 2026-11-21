#!/usr/bin/env python3
"""
Clean the India healthcare-facility dataset (Virtue Foundation, ~10,000 facilities).

Used in three places, so every path runs the same rules:
  * notebooks/01_clean_facilities   (Databricks: builds workspace.hackathon.silver_facilities)
  * tools/clean_dataset.py          (laptop:  python tools/clean_dataset.py dataset.csv facilities_cleaned.csv)
  * tools/facility_crawler.py       (reuses the phone / email / domain helpers)

    pip install pandas numpy phonenumbers

Stages
  1. Structural repair  - stitch 30 records whose multi-line `description` was split
                          across physical lines (with ",null" padding) back into 51 fields.
  2. Dedup              - drop exact duplicate rows (same unique_id, identical content).
  3. Column pruning     - drop constant / empty / redundant columns.
  4. Field cleaning     - types, phones, emails, URLs, lists, geography, dates, outliers.
  5. Quality flags      - `dq_flags` lists every issue found or fix applied per row,
                          `dup_group_id` links likely duplicate facilities (not removed).
"""
import csv, json, re, sys
from collections import Counter, defaultdict
from datetime import date
from urllib.parse import urlparse

from functools import lru_cache

import numpy as np
import pandas as pd
import phonenumbers
from phonenumbers import PhoneNumberType as PNT

TODAY = pd.Timestamp(date.today())

# --------------------------------------------------------------------------- #
# 1. Structural repair
# --------------------------------------------------------------------------- #
UUID_START = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12},')
PAD = re.compile(r'(,null)+\s*$')
N_HEAD, N_TAIL = 27, 23  # fields before / after `description`
RAW_COLUMNS = [
    'unique_id', 'source_types', 'source_ids', 'source_content_id', 'name', 'organization_type',
    'content_table_id', 'phone_numbers', 'officialPhone', 'email', 'websites', 'officialWebsite',
    'yearEstablished', 'acceptsVolunteers', 'facebookLink', 'address_line1', 'address_line2',
    'address_line3', 'address_city', 'address_stateOrRegion', 'address_zipOrPostcode',
    'address_country', 'address_countryCode', 'countries', 'facilityTypeId', 'operatorTypeId',
    'affiliationTypeIds', 'description', 'area', 'numberDoctors', 'capacity', 'specialties',
    'procedure', 'equipment', 'capability', 'recency_of_page_update',
    'distinct_social_media_presence_count', 'affiliated_staff_presence', 'custom_logo_presence',
    'number_of_facts_about_the_organization', 'post_metrics_most_recent_social_media_post_date',
    'post_metrics_post_count', 'engagement_metrics_n_followers', 'engagement_metrics_n_likes',
    'engagement_metrics_n_engagements', 'source', 'coordinates', 'latitude', 'longitude',
    'cluster_id', 'source_urls',
]
TAIL_COLUMNS = RAW_COLUMNS[N_HEAD + 1:]  # the 23 fields after `description`
UUID_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _parse_tail(tail):
    raw = next(csv.reader([tail]))
    out, i = [], 0
    while i < len(raw):
        x = raw[i]
        # a double-escaped JSON list is shredded by the csv reader: re-join it
        if x.startswith('[') and not x.rstrip('"').endswith(']'):
            parts = [x]
            while not parts[-1].rstrip('"').endswith(']'):
                i += 1
                parts.append(raw[i])
            x = json.dumps(re.findall(r'"+([^"\[\],]+)"+', ','.join(parts)))
        out.append(x)
        i += 1
    while len(out) > N_TAIL and out[-1] == 'null':  # padding beyond the real fields
        out.pop()
    assert len(out) == N_TAIL, f'tail has {len(out)} fields'
    return out


def read_and_repair(path):
    lines = open(path, encoding='utf-8').read().split('\n')
    header = next(csv.reader([lines[0]]))
    groups = []
    for i, line in enumerate(lines[1:], 1):
        if UUID_START.match(line):
            groups.append([line])
        elif line == '' and i == len(lines) - 1:
            continue
        else:
            groups[-1].append(line)

    rows, repaired = [], set()
    for g in groups:
        if len(g) == 1:
            rows.append(next(csv.reader([g[0]])))
            continue
        head = next(csv.reader([g[0] + '"']))
        assert len(head) == N_HEAD + 1
        parts = [head[-1]] + [PAD.sub('', m).strip('"') for m in g[1:-1]]
        k = g[-1].find('""",')
        parts.append(g[-1][:k].strip('"'))
        desc = '\n'.join(parts).strip().strip('"').strip()
        desc = desc.replace('," ', ', ').replace(' ,', ',')  # undo injected quote artefacts
        rows.append(head[:-1] + [desc] + _parse_tail(g[-1][k + 4:]))
        repaired.add(head[0])

    assert all(len(r) == len(header) for r in rows)
    df = pd.DataFrame(rows, columns=header).replace({'null': None, 'NULL': None, '': None})
    return df, repaired


def prepare_parsed_rows(df):
    """Prepare rows that a standard CSV reader already parsed (e.g. the Databricks bronze table).

    A standard parse of the raw file splits the 30 multi-line records: each one keeps its first
    28 fields, loses the 23 after `description`, and leaves ~88 fragment rows whose `unique_id`
    is a piece of text. Fragments are dropped; truncated records are kept and flagged so their
    missing fields are never mistaken for "the facility has no data".
    Returns (df, repaired_ids, extra_flags, info).
    """
    missing_cols = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f'source is missing expected columns: {missing_cols}')
    df = df[RAW_COLUMNS].copy()
    df = df.astype(object).where(df.notna(), None).replace({'null': None, 'NULL': None, '': None})
    real = df.unique_id.fillna('').str.match(UUID_ID)
    fragments = int((~real).sum())
    df = df[real].reset_index(drop=True)
    truncated = df[TAIL_COLUMNS].isna().all(axis=1)
    extra_flags = {uid: {'row_truncated_in_source'} for uid in df.unique_id[truncated]}
    info = {'fragment_rows_dropped': fragments, 'truncated_records_flagged': int(truncated.sum())}
    return df, set(), extra_flags, info


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def missing(v):
    return v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v))


def jlist(v):
    if missing(v):
        return []
    try:
        x = json.loads(v)
    except (TypeError, ValueError):
        return [v]
    return x if isinstance(x, list) else [x]


def uniq(items):
    seen, out = set(), []
    for x in items:
        if x is None:
            continue
        x = str(x).strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def dump(items):
    return json.dumps(items, ensure_ascii=False, separators=(',', ':')) if items else None


# Broken unicode escapes left as control characters (they truncate CSV readers):
#   '\x00ae' -> '\u00ae' (the '\u00' became NUL), '\x1f4a1' -> a broken emoji, '\x19' -> right quote
BROKEN_LATIN1 = re.compile(r'\x00([0-9a-fA-F]{2})')
BROKEN_EMOJI = re.compile(r'\x1f[0-9a-fA-F]{3}')
CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def fix_control_chars(s):
    s = BROKEN_EMOJI.sub('', s)
    s = BROKEN_LATIN1.sub(lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace('\x19', '\u2019')
    return CONTROL_CHARS.sub(' ', s)


def clean_text(s):
    if missing(s):
        return None
    s = fix_control_chars(str(s)).replace('\xa0', ' ')
    s = re.sub(r'[ \t]+', ' ', s).strip().strip(',').strip()
    return s or None


# ---- phones --------------------------------------------------------------- #
PHONE_TYPES = {PNT.MOBILE: 'mobile', PNT.FIXED_LINE: 'landline',
               PNT.FIXED_LINE_OR_MOBILE: 'mobile_or_landline', PNT.TOLL_FREE: 'toll_free',
               PNT.SHARED_COST: 'shared_cost', PNT.VOIP: 'voip'}


@lru_cache(maxsize=None)
def normalize_phone(p):
    """Return (E.164, type) using libphonenumber's India numbering plan.
    Types: mobile, landline, toll_free, shared_cost, foreign, other, invalid."""
    if missing(p) or not str(p).strip():
        return None, None
    s = str(p).strip()
    digits = re.sub(r'\D', '', s)
    if not digits:
        return None, None
    candidates = []
    # a stray '+' on a national number ("+5322421425", "+18008901111") is common here:
    # try the Indian national reading first
    if len(digits) == 10 or digits.startswith(('1800', '1860')) or (len(digits) == 11 and digits[0] == '0'):
        candidates.append(digits)
    candidates.append('+' + digits if s.startswith('+') else digits)
    for c in candidates:
        try:
            n = phonenumbers.parse(c, 'IN')
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(n):
            e164 = phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)
            if n.country_code != 91:
                return e164, 'foreign'
            return e164, PHONE_TYPES.get(phonenumbers.number_type(n), 'other')
    return ('+' + digits if s.startswith('+') else digits), 'invalid'


# ---- emails --------------------------------------------------------------- #
EMAIL_RE = re.compile(r'^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$')


def clean_emails(v):
    """Return (list_of_valid_emails, flags)."""
    flags = set()
    if missing(v):
        return [], flags
    raw = jlist(v) if str(v).startswith('[') else [v]
    out = []
    for e in raw:
        if missing(e):
            continue
        e = str(e).replace('\xa0', ' ').strip().lower()
        if 'email protected' in e:
            flags.add('email_obfuscated')   # Cloudflare placeholder; crawler can decode
            continue
        e = re.sub(r'\s*[\[\(]\s*at\s*[\]\)]\s*', '@', e)
        e = re.sub(r'\s*[\[\(]\s*dot\s*[\]\)]\s*', '.', e)
        e = e.removeprefix('mailto:').strip(' .;,')
        if EMAIL_RE.match(e):
            out.append(e)
        else:
            flags.add('email_invalid')
    return uniq(out), flags


# ---- websites ------------------------------------------------------------- #
NON_OFFICIAL_DOMAINS = {
    'practo.com', 'justdial.com', 'eka.care', 'lybrate.com', 'medindia.net', 'docindia.org',
    'hexahealth.com', 'credihealth.com', 'sulekha.com', 'indiamart.com', 'clinicspots.com',
    'drlogy.com', 'healthfrog.in', 'onefivenine.com', 'mappls.com', 'skedoc.com', 'quikr.com',
    'facebook.com', 'm.facebook.com', 'instagram.com', 'youtube.com', 'linkedin.com',
    'in.linkedin.com', 'x.com', 'twitter.com', 'wa.me', 'linktr.ee', 'bit.ly', 'g.page',
    'goo.gl', 'maps.app.goo.gl', 'google.com', 'business.site', 'blogspot.com',
}


def domain_of(u):
    if missing(u) or not u:
        return None
    u = u if re.match(r'^https?://', u, re.I) else 'http://' + u
    try:
        h = urlparse(u).netloc.lower().split(':')[0]
    except ValueError:
        return None
    return h[4:] if h.startswith('www.') else h or None


def is_non_official(dom):
    return isinstance(dom, str) and bool(dom) and any(dom == d or dom.endswith('.' + d) for d in NON_OFFICIAL_DOMAINS)


# ---- geography ------------------------------------------------------------ #
STATES = [
    'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh', 'Goa', 'Gujarat',
    'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka', 'Kerala', 'Madhya Pradesh',
    'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha', 'Punjab',
    'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand',
    'West Bengal', 'Andaman And Nicobar Islands', 'Chandigarh',
    'Dadra And Nagar Haveli And Daman And Diu', 'Delhi', 'Jammu And Kashmir', 'Ladakh',
    'Lakshadweep', 'Puducherry',
]
STATE_KEY = {s.lower(): s for s in STATES}
STATE_ALIASES = {
    'tamilnadu': 'Tamil Nadu', 'orissa': 'Odisha', 'up': 'Uttar Pradesh', 'u.p': 'Uttar Pradesh',
    'u.p.': 'Uttar Pradesh', 'uttarpradesh': 'Uttar Pradesh', 'uttar prades h': 'Uttar Pradesh',
    'chattisgarh': 'Chhattisgarh', 'cg': 'Chhattisgarh', 'telengana': 'Telangana', 'ts': 'Telangana',
    'uttaranchal': 'Uttarakhand', 'uttranchal': 'Uttarakhand', 'u.k.': 'Uttarakhand', 'uk': 'Uttarakhand',
    'm.p.': 'Madhya Pradesh', 'mp': 'Madhya Pradesh', 'madhyapradesh': 'Madhya Pradesh',
    'madhya': 'Madhya Pradesh', 'gj': 'Gujarat', 'mh': 'Maharashtra', 'ms': 'Maharashtra',
    'br': 'Bihar', 'dl': 'Delhi', 'nct': 'Delhi', 'nct of delhi': 'Delhi', 'nct delhi': 'Delhi',
    'delhi ncr': 'Delhi', 'delhi/ncr': 'Delhi', 'ncr-delhi': 'Delhi', 'new delhi': 'Delhi',
    'west delhi': 'Delhi', 'east delhi': 'Delhi', 'south delhi': 'Delhi',
    'north west delhi': 'Delhi', 'south east delhi area': 'Delhi', 'pondicherry': 'Puducherry',
    'u.t of puducherry': 'Puducherry', 'jammu & kashmir': 'Jammu And Kashmir',
    'kashmir': 'Jammu And Kashmir', 'srinagar kashmir': 'Jammu And Kashmir',
    'jammu, j&k': 'Jammu And Kashmir', 'punjab region': 'Punjab',
    'dadra & nagar haveli & daman & diu': 'Dadra And Nagar Haveli And Daman And Diu',
    'daman and diu': 'Dadra And Nagar Haveli And Daman And Diu', 'chandigarh (ut)': 'Chandigarh',
    'north goa': 'Goa', 'south goa': 'Goa', 'west tripura': 'Tripura',
}
CITY_ALIASES = {
    'bangalore': 'Bengaluru', 'bengaluru east': 'Bengaluru', 'gurgaon': 'Gurugram',
    'trivandrum': 'Thiruvananthapuram', 'trivandrum city': 'Thiruvananthapuram',
    'vizag': 'Visakhapatnam', 'cochin': 'Kochi', 'mysore': 'Mysuru', 'pondicherry': 'Puducherry',
    'baroda': 'Vadodara', 'calicut': 'Kozhikode', 'new mumbai': 'Navi Mumbai',
    'navi mumbai (new mumbai)': 'Navi Mumbai', 'navi-mumbai': 'Navi Mumbai',
}
INDIA_BBOX = (6.0, 37.6, 68.0, 97.5)  # lat_min, lat_max, lon_min, lon_max


def resolve_state(v):
    """Return canonical state or None (None => try PIN inference)."""
    if missing(v) or not v:
        return None
    k = re.sub(r'\s+', ' ', v.strip().lower())
    if k in STATE_KEY:
        return STATE_KEY[k]
    if k in STATE_ALIASES:
        return STATE_ALIASES[k]
    for part in re.split(r'[;,/]| - ', k):          # "Ghaziabad, Uttar Pradesh"
        part = part.strip()
        if part in STATE_KEY:
            return STATE_KEY[part]
    for s in sorted(STATES, key=len, reverse=True):    # "Bijapur-karnataka"
        if s.lower() in k:
            return s
    return None


def clean_city(v):
    v = clean_text(v)
    if not v:
        return None
    v = re.sub(r'-\d+$', '', v).strip()                # "Ludhiana-1"
    return CITY_ALIASES.get(v.lower(), v)


def clean_pin(v):
    if missing(v):
        return None
    d = re.sub(r'\s', '', str(v))
    return d if re.fullmatch(r'[1-9]\d{5}', d) else None


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


# ---- dates ---------------------------------------------------------------- #
def clean_date(v):
    """ISO date or None. Relative ('9 months ago') / partial ('February 12') -> None."""
    if missing(v) or not v:
        return None, False
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)):
        return None, True
    d = pd.to_datetime(v, errors='coerce')
    if pd.isna(d) or d.year < 1990 or d > TODAY:
        return None, True
    return d.date().isoformat(), False


# --------------------------------------------------------------------------- #
# 2-5. Clean
# --------------------------------------------------------------------------- #
DROP_COLS = {
    'content_table_id': 'identical to source_content_id in every row',
    'organization_type': "constant ('facility')",
    'address_country': "constant ('India'); address_countryCode kept",
    'countries': '100% empty',
    'source': "constant ('kie')",
    'coordinates': 'GeoJSON duplicate of latitude/longitude (verified identical)',
}
LIST_COLS = ['source_types', 'source_ids', 'websites', 'affiliationTypeIds', 'procedure',
             'equipment', 'capability', 'source_urls']
INT_COLS = ['numberDoctors', 'capacity', 'area', 'distinct_social_media_presence_count',
            'number_of_facts_about_the_organization', 'post_metrics_post_count',
            'engagement_metrics_n_followers', 'engagement_metrics_n_likes',
            'engagement_metrics_n_engagements']
BOOL_COLS = ['acceptsVolunteers', 'affiliated_staff_presence', 'custom_logo_presence']


def clean(df, repaired_ids, extra_flags=None):
    flags = defaultdict(set)
    for uid, fs in (extra_flags or {}).items():
        flags[uid] |= set(fs)
    n_raw = len(df)

    # -- 2. exact duplicates
    df = df.drop_duplicates().reset_index(drop=True)
    assert df.unique_id.is_unique
    n_exact_dups = n_raw - len(df)
    for uid in repaired_ids:
        flags[uid].add('row_repaired_multiline')

    # -- 3. drop columns
    df = df.drop(columns=list(DROP_COLS))

    # -- 4a. free text
    text_cols = ['name', 'address_line1', 'address_line2', 'address_line3', 'description']
    has_ctrl = df[text_cols].apply(lambda col: col.fillna('').astype(str).str.contains(CONTROL_CHARS)).any(axis=1)
    for uid in df.unique_id[has_ctrl]:
        flags[uid].add('text_control_chars_repaired')
    for c in text_cols:
        df[c] = df[c].map(clean_text)
    addr_like = df.name.str.contains(r'\b\d{6}\b|(?:,.*){2,}|\b(?:floor|road|nagar|complex)\b',
                                     case=False, regex=True, na=False)
    for uid in df.unique_id[addr_like]:
        flags[uid].add('name_contains_address')
    # short display name: text before the first " - ", " – " or "," (only for flagged names)
    short = df.name.str.split(r'\s[-–]\s|,', n=1, regex=True).str[0].str.strip()
    df['name_short'] = short.where(addr_like & (short.str.len() >= 3))

    # -- 4b. categoricals
    df['facilityTypeId'] = df.facilityTypeId.replace({'farmacy': 'pharmacy'})
    df['operatorTypeId'] = df.operatorTypeId.replace({'government': 'public'})

    # -- 4c. lists: dedupe, drop empty/null items, keep first-seen order
    for c in LIST_COLS:
        df[c] = df[c].map(lambda v: dump(uniq(jlist(v))))

    # specialties: map free-text items onto the camelCase taxonomy where possible
    taxonomy = Counter(x for v in df.specialties.dropna() for x in jlist(v)
                       if x and re.fullmatch(r'[a-z][A-Za-z]+', x))

    def to_taxonomy(x):
        if re.fullmatch(r'[a-z][A-Za-z]+', x):
            return x
        words = re.findall(r'[A-Za-z]+', x)
        camel = words[0].lower() + ''.join(w.capitalize() for w in words[1:]) if words else x
        return camel if camel in taxonomy else x
    df['specialties'] = df.specialties.map(lambda v: dump(uniq(to_taxonomy(x) for x in jlist(v) if x)))

    # -- 4d. phones
    def norm_list(v):
        return uniq(normalize_phone(p)[0] for p in jlist(v) if p)
    df['phone_numbers'] = df.phone_numbers.map(lambda v: dump(norm_list(v)))
    norm = df.officialPhone.map(normalize_phone)
    df['officialPhone'] = norm.str[0]
    df['officialPhone_type'] = norm.str[1]
    for uid, t in zip(df.unique_id, df.officialPhone_type):
        if t == 'invalid':
            flags[uid].add('phone_invalid')
    # the same number on many facilities in different cities = directory / call-centre number
    g = df.dropna(subset=['officialPhone']).groupby('officialPhone')
    shared = g.filter(lambda x: len(x) >= 3 and x.address_city.nunique() >= 2).officialPhone.unique()
    for uid in df.unique_id[df.officialPhone.isin(shared)]:
        flags[uid].add('phone_shared_across_facilities')

    # -- 4e. emails
    em = df.email.map(clean_emails)
    df['email'] = em.map(lambda t: t[0][0] if t[0] else None)
    df['email_all'] = em.map(lambda t: dump(t[0]) if len(t[0]) > 1 else None)
    for uid, (_, f) in zip(df.unique_id, em):
        flags[uid] |= f

    # -- 4f. websites
    def first_url(v):
        if not missing(v) and str(v).startswith('['):
            items = uniq(jlist(v))
            return items[0] if items else None
        return clean_text(v)
    df['officialWebsite'] = df.officialWebsite.map(first_url)
    df['officialWebsite_domain'] = df.officialWebsite.map(domain_of)
    for uid, d in zip(df.unique_id, df.officialWebsite_domain):
        if is_non_official(d):
            flags[uid].add('website_not_official')
    df['facebookLink'] = df.facebookLink.map(clean_text)

    # -- 4g. year established
    y = pd.to_numeric(df.yearEstablished, errors='coerce')
    bad_year = y.notna() & ((y < 1600) | (y > TODAY.year))
    for uid in df.unique_id[bad_year]:
        flags[uid].add('year_invalid')
    df['yearEstablished'] = y.where(~bad_year).astype('Int64')

    # -- 4h. numerics + outliers
    for c in INT_COLS:
        df[c] = pd.to_numeric(df[c], errors='coerce').round().astype('Int64')
    impossible = {'numberDoctors': 5000, 'capacity': 10000}
    suspicious = {'numberDoctors': 1000, 'capacity': 3000}
    for c, lim in impossible.items():
        m = df[c] >= lim
        for uid in df.unique_id[m.fillna(False)]:
            flags[uid].add(f'{c}_implausible_removed')
        df.loc[m.fillna(False), c] = pd.NA
    for c, lim in suspicious.items():
        for uid in df.unique_id[(df[c] >= lim).fillna(False)]:
            flags[uid].add(f'{c}_outlier')

    # -- 4i. booleans
    for c in BOOL_COLS:
        df[c] = df[c].map({'true': True, 'false': False}).astype('boolean')

    # -- 4j. dates
    for c in ['post_metrics_most_recent_social_media_post_date', 'recency_of_page_update']:
        res = df[c].map(clean_date)
        for uid, (_, bad) in zip(df.unique_id, res):
            if bad:
                flags[uid].add(f'{c}_unparseable')
        df[c] = res.str[0]

    # -- 4k. geography
    df['address_city'] = df.address_city.map(clean_city)
    df['address_zipOrPostcode'] = df.address_zipOrPostcode.map(clean_pin)
    raw_state = df.address_stateOrRegion.map(clean_text)
    df['address_stateOrRegion'] = raw_state.map(resolve_state)
    # empirical PIN-prefix -> state map from rows whose state is already canonical
    pin3 = df.address_zipOrPostcode.str[:3]
    tab = pd.crosstab(pin3, df.address_stateOrRegion)
    share = tab.max(axis=1) / tab.sum(axis=1)
    pin_state = tab.idxmax(axis=1)[(share >= 0.8) & (tab.sum(axis=1) >= 3)]
    inferred = df.address_stateOrRegion.isna() & pin3.isin(pin_state.index)
    df.loc[inferred, 'address_stateOrRegion'] = pin3[inferred].map(pin_state)
    for uid in df.unique_id[inferred]:
        flags[uid].add('state_inferred_from_pin')
    # fallbacks for district/city names typed into the state field ("Mumbai", "Kupwara")
    city_key = df.address_city.str.lower()
    ctab = pd.crosstab(city_key, df.address_stateOrRegion)
    cshare = ctab.max(axis=1) / ctab.sum(axis=1)
    city_state = ctab.idxmax(axis=1)[(cshare >= 0.8) & (ctab.sum(axis=1) >= 3)]
    pin2 = df.address_zipOrPostcode.str[:2]
    ptab = pd.crosstab(pin2, df.address_stateOrRegion)
    pshare = ptab.max(axis=1) / ptab.sum(axis=1)
    pin2_state = ptab.idxmax(axis=1)[pshare >= 0.8]
    for keys, mapping in ((raw_state.str.lower(), city_state), (city_key, city_state),
                          (pin2, pin2_state)):
        m = df.address_stateOrRegion.isna() & keys.isin(mapping.index)
        df.loc[m, 'address_stateOrRegion'] = keys[m].map(mapping)
        for uid in df.unique_id[m]:
            flags[uid].add('state_inferred_from_city_or_pin')
    unresolved = df.address_stateOrRegion.isna() & raw_state.notna()
    df.loc[unresolved, 'address_stateOrRegion'] = raw_state[unresolved]
    for uid in df.unique_id[unresolved]:
        flags[uid].add('state_unrecognized')
    mismatch = (pin3.isin(pin_state.index) & ~inferred & ~unresolved
                & (pin3.map(pin_state) != df.address_stateOrRegion))
    # if the city's usual state agrees with the PIN's state, the state field is the odd one
    # out (e.g. Hyderabad tagged Andhra Pradesh pre-2014, Gurugram tagged Delhi): correct it
    pin_st, city_st = pin3.map(pin_state), city_key.map(city_state)
    fix = mismatch & (pin_st == city_st)
    df.loc[fix, 'address_stateOrRegion'] = pin_st[fix]
    for uid in df.unique_id[fix]:
        flags[uid].add('state_corrected')
    for uid in df.unique_id[mismatch & ~fix]:
        flags[uid].add('state_pin_mismatch')
    for uid in df.unique_id[df.address_zipOrPostcode.isna()]:
        flags[uid].add('pin_invalid')

    lat = pd.to_numeric(df.latitude, errors='coerce')
    lon = pd.to_numeric(df.longitude, errors='coerce')
    out = ~(lat.between(*INDIA_BBOX[:2]) & lon.between(*INDIA_BBOX[2:]))
    for uid in df.unique_id[out]:
        flags[uid].add('coords_outside_india_removed')
    df['latitude'] = lat.where(~out)
    df['longitude'] = lon.where(~out)
    # far from the median location of other facilities in the same city
    key = df.address_city.str.lower()
    counts = key.map(key.value_counts())
    med_lat = df.groupby(key).latitude.transform('median')
    med_lon = df.groupby(key).longitude.transform('median')
    dist = haversine_km(df.latitude, df.longitude, med_lat, med_lon)
    far = (counts >= 5) & (dist > 50)
    for uid in df.unique_id[far.fillna(False)]:
        flags[uid].add('coords_far_from_city')

    # -- 5. likely duplicate facilities (same name or same unique phone, within 300 m)
    parent = {u: u for u in df.unique_id}

    def find(u):
        while parent[u] != u:
            parent[u] = parent[parent[u]]
            u = parent[u]
        return u

    def link_groups(keys):
        for _, grp in df.assign(_k=keys).dropna(subset=['_k', 'latitude']).groupby('_k'):
            if len(grp) < 2:
                continue
            ids, la, lo = grp.unique_id.values, grp.latitude.values, grp.longitude.values
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if haversine_km(la[i], lo[i], la[j], lo[j]) <= 0.3:
                        a, b = find(ids[i]), find(ids[j])
                        parent[max(a, b)] = min(a, b)
    link_groups(df.name.str.lower().str.replace(r'[^a-z0-9]', '', regex=True))
    link_groups(df.officialPhone.where(~df.officialPhone.isin(shared)))
    root = df.unique_id.map(find)
    multi = root.map(root.value_counts()) > 1
    df['dup_group_id'] = root.where(multi)
    for uid in df.unique_id[multi]:
        flags[uid].add('possible_duplicate')

    # place derived columns next to their source columns
    cols = [c for c in df.columns if c not in ('name_short', 'officialPhone_type', 'email_all',
                                               'officialWebsite_domain')]
    for after, new_col in (('name', 'name_short'), ('officialPhone', 'officialPhone_type'),
                           ('email', 'email_all'), ('officialWebsite', 'officialWebsite_domain')):
        cols.insert(cols.index(after) + 1, new_col)
    df = df[cols]

    df['dq_flags'] = df.unique_id.map(lambda u: ';'.join(sorted(flags[u])) or None)
    df['dq_flag_count'] = df.unique_id.map(lambda u: len(flags[u])).astype(int)

    stats = {'raw_rows': n_raw, 'exact_duplicates_dropped': n_exact_dups,
             'rows_repaired': len(repaired_ids), 'rows_out': len(df),
             'flag_counts': Counter(f for s in flags.values() for f in s)}
    return df, stats


def main(src, dst):
    df, repaired = read_and_repair(src)
    df, stats = clean(df, repaired)
    df.to_csv(dst, index=False, encoding='utf-8')
    print(f"raw rows parsed: {stats['raw_rows']}  repaired: {stats['rows_repaired']}  "
          f"exact dups dropped: {stats['exact_duplicates_dropped']}  rows out: {stats['rows_out']}")
    print('columns dropped:', ', '.join(DROP_COLS))
    for k, v in stats['flag_counts'].most_common():
        print(f'  {k:55s}{v:6d}')


if __name__ == '__main__':
    main(*(sys.argv[1:3] if len(sys.argv) >= 3 else ('dataset.csv', 'facilities_cleaned.csv')))
