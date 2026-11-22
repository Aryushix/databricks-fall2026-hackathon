#!/usr/bin/env python3
r"""
facility_crawler.py - enrich the cleaned facility dataset from each facility's OWN website.

Runs on your LAPTOP, not on Databricks (Free Edition blocks most outbound web traffic).
Uses the phone / email / domain helpers from src/hackathon/cleaning.py.

    pip install -r tools/requirements.txt

    python tools/facility_crawler.py crawl  --input facilities_cleaned.csv --db crawl.sqlite \
                                            --contact "you@example.com" --limit 200
    python tools/facility_crawler.py export --db crawl.sqlite --out enrichment.csv
    python tools/facility_crawler.py merge  --input facilities_cleaned.csv \
                                            --enrichment enrichment.csv --out facilities_enriched.csv

Then upload facilities_enriched.csv to /Volumes/workspace/hackathon/raw/ and run
notebooks/01_clean_facilities: it loads the enriched file in place of the raw data.

    crawl --retry-failed   keep successes, try every failed facility again (a plain re-run skips them)
    reextract --out X.csv  re-run the extraction rules on cached pages, no network; use instead of export
                           after changing the rules

Failed facilities record why in `error`: robots_disallowed (the site's robots.txt said no),
robots_unreachable:<Error> (robots.txt couldn't be loaded, so the site was skipped),
robots_server_error:<status>, http_<status>, or an httpx error name.

merge fills only empty fields, and copies nothing from a site it can't tie to the facility. Why
is in `crawl_skip_reason`: shared_website (--chain-threshold or more facilities list that domain,
usually a chain's head office), name_mismatch (the domain doesn't carry the facility's name, see
site_matches_name), or redirected_elsewhere (the site now forwards to a domain that doesn't).
What was found still goes in crawl_phones_found / crawl_socials_found, and stays in enrichment.csv.

What it collects per facility: emails (incl. Cloudflare-obfuscated ones), phones, year
established, bed count, social links, schema.org JSON-LD (telephone, email, foundingDate, geo).

Politeness / safety
  * robots.txt honoured per host (unreachable robots.txt => host skipped), Crawl-delay honoured
  * one request at a time per host + configurable delay; at most --concurrency sites in progress
  * identifies itself with a User-Agent that includes your contact address
  * only the facility's own domain is crawled; directory / aggregator / social sites are skipped
    (their terms usually prohibit scraping)
  * at most --max-pages pages per site, 2 MB per page, HTML only
  * every page and result is stored in SQLite, so runs are resumable and auditable
"""
import argparse, asyncio, json, re, sqlite3, sys, time, zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hackathon.cleaning import EMAIL_RE, domain_of, is_non_official, missing, normalize_phone  # noqa: E402

MAX_BYTES = 2_000_000
LINK_HINTS = ('contact', 'about', 'reach', 'location', 'find-us', 'findus', 'address',
              'overview', 'who-we-are', 'history', 'profile', 'facilities', 'infrastructure')
SOCIAL = {'facebook.com': 'facebook', 'instagram.com': 'instagram', 'linkedin.com': 'linkedin',
          'youtube.com': 'youtube', 'x.com': 'x', 'twitter.com': 'x'}
YEAR_RE = re.compile(r'(?:established|estd\.?|est\.|founded|since|started|inception)'
                     r'\s*(?:in|on|:|-|year)?\s*(?:the\s+year\s+)?((?:18|19|20)\d{2})\b', re.I)
BEDS_RE = re.compile(r'(?<![\d,.])(\d{1,2},\d{3}|\d{1,4})\s*(?:\+\s*)?[- ]?(?:bedded|beds?)\b', re.I)
# Bed counts that are not the facility's current size: a unit ("10-bedded ICU", "ICU with 10 beds"),
# the past ("began in 1997 with 10 beds") or a whole chain ("4,290 beds across 18 hospitals").
_UNIT = (r'(?:n?icus?|iccu|picu|sicu|micu|ccu|hdu|intensive(?: care)?(?: units?)?|critical(?: care)?|'
         r'dialysis|emergency|casualty|wards?|'
         r'private|rooms?|suites?|isolation|day ?care|labou?r|maternity|burns?|trauma|cath|wing|block|floor)')
BED_UNIT_AFTER = re.compile(r'\s*(?:[a-z\-]+\s+)?' + _UNIT + r'\b', re.I)
BED_UNIT_BEFORE = re.compile(r'\b' + _UNIT + r'\b\s*(?:(?:with|of|has|having|have|containing|comprising)\s+'
                             r'|[:(\-–]\s*)?$', re.I)
BED_PAST = re.compile(r'\b(?:began|begun|started|starting|initially|inception|humble|modest|beginning|'
                      r'commenced|originally|opened with|back in|with (?:just|only|a mere))\b', re.I)
BED_CHAIN = re.compile(r'\b(?:across|network|group|chain|combined|cumulative)\b', re.I)
# ...unless it says this is the size now ("has now grown to 1100 beds")
BED_NOW = re.compile(r'\b(?:now|today|currently|presently|grown to|increased to|expanded to|upgraded to|'
                     r'total of)\b', re.I)
# a year in a timeline: "1990 Beds Increased to 675"
BED_YEAR_VERB = re.compile(r'\s*(?:increased|expanded|added|raised|upgraded|grew|went|reached)\b', re.I)
PHONE_RE = re.compile(r'(?<![\d])(?:\+91[\s\-]?|0)?(?:[6-9]\d{4}[\s\-]?\d{5}|\d{2,4}[\s\-]\d{6,8}'
                      r'|1800[\s\-]?\d{3}[\s\-]?\d{3,4})(?![\d])')
EMAIL_TXT_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
JUNK_EMAIL = re.compile(r'(example\.|sentry|wixpress|\.png$|\.jpg$|\.webp$|@2x|domain\.com)', re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  url TEXT PRIMARY KEY, host TEXT, status INTEGER, content_type TEXT,
  fetched_at TEXT, html BLOB, error TEXT);
CREATE TABLE IF NOT EXISTS results (
  unique_id TEXT PRIMARY KEY, website TEXT, status TEXT, pages_fetched INTEGER,
  emails TEXT, phones TEXT, year_established INTEGER, beds INTEGER, socials TEXT,
  jsonld TEXT, evidence TEXT, crawled_at TEXT, error TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# --------------------------------------------------------------------------- #
# Extraction (pure functions - easy to unit test)
# --------------------------------------------------------------------------- #
def decode_cfemail(hexstr):
    """Cloudflare email obfuscation: first byte is the XOR key."""
    try:
        key = int(hexstr[:2], 16)
        return ''.join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return None


def bed_counts(text):
    """Bed counts that describe the facility as it is now (see BED_UNIT_*, BED_PAST, BED_CHAIN)."""
    out, this_year = [], datetime.now().year
    for m in BEDS_RE.finditer(text):
        n = int(m.group(1).replace(',', ''))
        if not 5 <= n <= 5000:
            continue
        start = max(text.rfind('.', 0, m.start()), text.rfind('|', 0, m.start()), m.start() - 41)
        near = text[start + 1:m.start()]                         # same sentence, at most 40 chars
        after = text[m.end():m.end() + 40].split('.')[0]
        if 1900 <= n <= this_year + 1 and BED_YEAR_VERB.match(after):
            continue
        past = BED_PAST.search(near) and not BED_NOW.search(near)
        if (past or BED_UNIT_AFTER.match(after) or BED_UNIT_BEFORE.search(near[-25:])
                or BED_CHAIN.search(after) or BED_CHAIN.search(near[-30:])):
            continue
        out.append(n)
    return out


def rank_emails(emails, site):
    """The facility's own domain first, then addresses that name the site (drbagchiivf@gmail.com
    on drbagchiivf.com), then the rest, so a stray mailto left by a web developer comes last."""
    label = (site or '').split('.')[0]

    def rank(e):
        dom = e.rsplit('@', 1)[-1]
        if site and (dom == site or dom.endswith('.' + site)):
            return 0
        return 1 if len(label) >= 4 and label in e else 2
    return sorted(emails, key=rank)


# Words that say what a facility is, not which one. A domain made of these ("cityhospital.in")
# can't be told apart from any other facility's, so on their own they never tie a site to a name.
NAME_GENERIC = {
    'hospital', 'hospitals', 'clinic', 'clinics', 'polyclinic', 'dispensary', 'medical', 'medicals',
    'college', 'centre', 'center', 'centres', 'centers', 'health', 'healthcare', 'care', 'cure',
    'nursing', 'home', 'homes', 'multispeciality', 'multispecialty', 'multi', 'speciality', 'specialty',
    'superspeciality', 'superspecialty', 'super', 'institute', 'institution', 'research', 'sciences',
    'science', 'university', 'foundation', 'trust', 'memorial', 'general', 'district', 'govt',
    'government', 'civil', 'community', 'primary', 'maternity', 'diagnostic', 'diagnostics', 'lab',
    'labs', 'laboratory', 'pharmacy', 'dental', 'dentist', 'dentistry', 'eye', 'heart', 'child',
    'children', 'kids', 'women', 'mother', 'ortho', 'surgical', 'surgery', 'city', 'new', 'india',
    'indian', 'national', 'international', 'global', 'the', 'and',
    'sri', 'shri', 'shree', 'sree', 'smt', 'late',                       # honorifics
}
NAME_STOP = {'of', 'and', 'the', 'for', 'in', 'at', 'by'}               # left out of initials
_FACILITY_WORDS = {'hospital', 'hospitals', 'clinic', 'clinics', 'nursing', 'home', 'centre', 'center'}
_SECOND_LEVEL = {'co', 'org', 'net', 'ac', 'edu', 'gov', 'nic', 'res', 'gen', 'firm', 'ind', 'com'}


def _domain_label(domain):
    """'www.amritahospital.org' -> 'amritahospital', 'aiims.ac.in' -> 'aiims'."""
    parts = [p for p in (domain_of(domain) or '').split('.') if p]
    if len(parts) > 1:
        parts = parts[:-1]                                   # the top-level domain
    while len(parts) > 1 and parts[-1] in _SECOND_LEVEL:     # .co.in, .ac.in, .org.in
        parts = parts[:-1]
    return re.sub(r'[^a-z0-9]', '', ''.join(parts))


def site_matches_name(name, domain, city=None):
    """True if the domain carries the facility's name: a distinctive word of it ("amrita" in
    amritahospital.org, "dmc" in dmch.edu) or its initials ("nsmch" for Netaji Subhas Medical
    College & Hospital). Generic words (NAME_GENERIC) and the facility's city don't count, and
    a place after a comma ("Holy Family Hospital, Jaipur") is left out."""
    if missing(name) or missing(domain):
        return False
    label = _domain_label(domain)
    if not label:
        return False
    head = re.sub(r"['’]s\b", '', str(name).split(',')[0].lower())
    words = re.findall(r'[a-z0-9]+', head)
    place = set(re.findall(r'[a-z0-9]+', str(city).lower())) if not missing(city) else set()
    if any(len(w) >= 3 and not w.isdigit() and w not in NAME_GENERIC and w not in place and w in label
           for w in words):
        return True
    words = [w for w in words if w not in NAME_STOP]
    # initials of the whole name, and without the trailing "Hospital" or city: "kem" for
    # King Edward Memorial Hospital (kem.edu), "bmc" for Burdwan Medical College Burdwan
    core = list(words)
    while core and (core[-1] in _FACILITY_WORDS or core[-1] in place):
        core.pop()
    return any(len(ini) >= 3 and ini in label
               for ini in {''.join(w[0] for w in words), ''.join(w[0] for w in core)})


def _walk_jsonld(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _walk_jsonld(x)
    elif isinstance(obj, dict):
        yield obj
        for k in ('@graph', 'mainEntity', 'publisher', 'provider'):
            if k in obj:
                yield from _walk_jsonld(obj[k])


def extract(html, page_url):
    soup = BeautifulSoup(html, 'html.parser')
    out = {'emails': [], 'phones': [], 'years': [], 'beds': [], 'socials': {}, 'jsonld': {},
           'links': []}

    # JSON-LD (most reliable when present)
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
        except (json.JSONDecodeError, TypeError):
            continue
        for o in _walk_jsonld(data):
            for k_src, k_dst in (('telephone', 'phones'), ('email', 'emails')):
                vals = o.get(k_src)
                for v in vals if isinstance(vals, list) else [vals]:
                    if isinstance(v, str):
                        out[k_dst].append(v.removeprefix('mailto:'))
            if isinstance(o.get('foundingDate'), str) and re.match(r'(18|19|20)\d{2}', o['foundingDate']):
                out['years'].append(int(o['foundingDate'][:4]))
            geo = o.get('geo')
            if isinstance(geo, dict) and geo.get('latitude') and geo.get('longitude'):
                out['jsonld']['geo'] = [geo['latitude'], geo['longitude']]
            same = o.get('sameAs')
            for s in same if isinstance(same, list) else [same]:
                if isinstance(s, str):
                    out['links'].append(s)

    # Cloudflare-protected emails
    for el in soup.select('[data-cfemail]'):
        e = decode_cfemail(el['data-cfemail'])
        if e:
            out['emails'].append(e)
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if '/cdn-cgi/l/email-protection#' in href:
            e = decode_cfemail(href.split('#', 1)[1])
            if e:
                out['emails'].append(e)
        elif href.lower().startswith('mailto:'):
            out['emails'].append(href[7:].split('?')[0])
        elif href.lower().startswith('tel:'):
            out['phones'].append(href[4:])
        else:
            out['links'].append(urljoin(page_url, href))

    for t in soup(['script', 'style', 'noscript']):
        t.decompose()
    text = soup.get_text(' ', strip=True)
    out['emails'] += EMAIL_TXT_RE.findall(text)
    out['phones'] += PHONE_RE.findall(text)
    out['years'] += [int(y) for y in YEAR_RE.findall(text)]
    out['beds'] += bed_counts(text)

    for link in out['links']:
        d = domain_of(link) or ''
        for dom, name in SOCIAL.items():
            if d == dom or d.endswith('.' + dom):
                out['socials'].setdefault(name, link.split('?')[0])
    return out


def clean_found(emails, phones, years, site=None):
    em = []
    for e in emails:
        e = e.strip().lower().strip('.,;:')
        if EMAIL_RE.match(e) and not JUNK_EMAIL.search(e) and e not in em:
            em.append(e)
    em = rank_emails(em, site)
    ph = []
    for p in phones:
        n, t = normalize_phone(p)
        if t in ('mobile', 'landline', 'mobile_or_landline', 'toll_free', 'shared_cost') and n not in ph:
            ph.append(n)
    this_year = datetime.now().year
    yrs = [y for y in years if 1800 <= y <= this_year]
    year = Counter(yrs).most_common(1)[0][0] if yrs else None
    return em, ph, year


def summarize(extractions, site):
    """Combine one site's page extractions [(url, extract(...)), ...] into a result row."""
    emails, phones, years, beds, socials, jsonld, evidence = [], [], [], [], {}, {}, {}
    for url, x in extractions:
        for field, vals in (('email', x['emails']), ('phone', x['phones']),
                            ('year', x['years']), ('beds', x['beds'])):
            if vals:
                evidence.setdefault(field, url)
        emails += x['emails']; phones += x['phones']; years += x['years']; beds += x['beds']
        for k, v in x['socials'].items():
            socials.setdefault(k, v)
        jsonld.update(x['jsonld'])
    em, ph, year = clean_found(emails, phones, years, site)
    # largest remaining figure: the headline size beats stray small mentions bed_counts() missed
    bed = max(beds) if beds else None
    return em, ph, year, bed, socials, jsonld, evidence


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
class Crawler:
    def __init__(self, db, user_agent, delay, concurrency, max_pages, timeout, use_cache=True,
                 transport=None):
        self.db = db
        self.ua = user_agent
        self.delay = delay
        self.sem = asyncio.Semaphore(concurrency)   # every request, robots.txt included
        self.max_pages = max_pages
        self.use_cache = use_cache
        self.robots, self.robots_err, self.robots_lock = {}, {}, {}
        self.host_lock, self.host_next = {}, {}
        self.client = httpx.AsyncClient(
            headers={'User-Agent': user_agent, 'Accept': 'text/html,application/xhtml+xml'},
            timeout=timeout, follow_redirects=True, max_redirects=5, transport=transport)

    async def close(self):
        await self.client.aclose()

    async def _polite(self, host):
        lock = self.host_lock.setdefault(host, asyncio.Lock())
        await lock.acquire()
        wait = self.host_next.get(host, 0) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        return lock

    def _release(self, host, lock):
        rp = self.robots.get(f'https://{host}') or self.robots.get(f'http://{host}')
        cd = rp.crawl_delay(self.ua) if rp else None
        self.host_next[host] = time.monotonic() + max(self.delay, float(cd or 0))
        lock.release()

    async def allowed(self, url):
        """Return None if the URL may be fetched, else why not:
        robots_disallowed (the site said no), robots_server_error:<status>, or
        robots_unreachable:<error> (robots.txt couldn't be loaded, so we don't crawl the site)."""
        p = urlparse(url)
        host = f'{p.scheme}://{p.netloc.lower()}'          # robots.txt is per scheme+host
        async with self.robots_lock.setdefault(host, asyncio.Lock()):
            if host not in self.robots:
                await self._load_robots(host)
        if self.robots[host].can_fetch(self.ua, url):
            return None
        return self.robots_err.get(host, 'robots_disallowed')

    async def _load_robots(self, host):
        rp = RobotFileParser()
        for attempt in range(2):                     # one retry: most failures are transient
            try:
                async with self.sem:
                    r = await self.client.get(f'{host}/robots.txt')
            except httpx.HTTPError as e:
                self.robots_err[host] = f'robots_unreachable:{type(e).__name__}'
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                rp.disallow_all = True               # still unreachable => don't crawl
                break
            self.robots_err.pop(host, None)
            if r.status_code >= 500:
                rp.disallow_all = True               # RFC 9309: server error => assume disallow
                self.robots_err[host] = f'robots_server_error:{r.status_code}'
            elif r.status_code >= 400:
                rp.allow_all = True                  # no robots.txt => allowed
            else:
                rp.parse(r.text.splitlines())
            break
        self.robots[host] = rp

    def _cached(self, url):
        row = self.db.execute('SELECT status, html, final_url FROM pages WHERE url=?', (url,)).fetchone()
        if row and row[1] is not None:
            return row[0], zlib.decompress(row[1]).decode('utf-8', 'replace'), row[2] or url
        return None

    async def fetch(self, url):
        """Return (status, html|None, final_url, error|None)."""
        if self.use_cache and (c := self._cached(url)):
            return c[0], c[1], c[2], None
        if denied := await self.allowed(url):
            return None, None, url, denied
        host = urlparse(url).netloc.lower()
        for attempt in range(3):
            lock = await self._polite(host)
            if self.use_cache and (c := self._cached(url)):   # filled while we waited
                self._release(host, lock)
                return c[0], c[1], c[2], None
            try:
                async with self.sem:
                    async with self.client.stream('GET', url) as r:
                        ctype = r.headers.get('content-type', '')
                        status = r.status_code
                        body = b''
                        if status == 200 and 'html' in ctype:
                            async for chunk in r.aiter_bytes():
                                body += chunk
                                if len(body) > MAX_BYTES:
                                    break
                        final = str(r.url)
                        retry_after = r.headers.get('retry-after')
            except httpx.HTTPError as e:
                self._release(host, lock)
                err = f'{type(e).__name__}'
                if attempt == 2:
                    self._store(url, host, None, '', None, err)
                    return None, None, url, err
                await asyncio.sleep(2 ** attempt)
                continue
            self._release(host, lock)
            if status in (429, 503) and attempt < 2:
                await asyncio.sleep(min(int(retry_after) if (retry_after or '').isdigit() else 5 * 2 ** attempt, 60))
                continue
            html = body.decode(r.encoding or 'utf-8', 'replace') if body else None
            self._store(url, host, status, ctype, html, None if html else f'http_{status}_{ctype[:30]}', final)
            return status, html, final, None if html else f'http_{status}'
        return None, None, url, 'retries_exhausted'

    def _store(self, url, host, status, ctype, html, err, final=None):
        self.db.execute('INSERT OR REPLACE INTO pages (url, host, status, content_type, fetched_at, html, '
                        'error, final_url) VALUES (?,?,?,?,?,?,?,?)',
                        (url, host, status, ctype, now(),
                         zlib.compress(html.encode()) if html else None, err, final))
        self.db.commit()

    async def crawl_site(self, uid, website):
        guessed = not re.match(r'^https?://', website, re.I)
        start = 'https://' + website if guessed else website
        status, html, final, err = await self.fetch(start)
        if html is None and guessed:                       # bare domain: fall back to http
            status, html, final, err2 = await self.fetch('http://' + website)
            err = f'{err}; http: {err2}' if err2 and err2 != err else err
        if html is None:
            return self._save(uid, website, 'failed', 0, [], [], None, None, {}, {}, {}, err)

        base = domain_of(final)
        # parsing is CPU-heavy: keep it off the event loop so other sites' timeouts stay accurate
        first = await asyncio.to_thread(extract, html, final)
        extractions = [(final, first)]
        cands = []
        for link in dict.fromkeys(first['links']):
            if domain_of(link) != base or re.search(r'\.(pdf|jpe?g|png|gif|zip|docx?)$', link, re.I):
                continue
            score = sum(h in link.lower() for h in LINK_HINTS)
            if score:
                cands.append((-score, len(link), link.split('#')[0]))
        for _, _, link in sorted(set(cands))[: self.max_pages - 1]:
            _, h, f, _ = await self.fetch(link)
            if h:
                extractions.append((f, await asyncio.to_thread(extract, h, f)))

        em, ph, year, bed, socials, jsonld, evidence = summarize(extractions, base)
        return self._save(uid, website, 'ok', len(extractions), em, ph, year, bed, socials, jsonld,
                          evidence, None)

    def _save(self, uid, website, status, n, em, ph, year, beds, socials, jsonld, evidence, err):
        self.db.execute('INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (uid, website, status, n, json.dumps(em), json.dumps(ph), year, beds,
                         json.dumps(socials), json.dumps(jsonld), json.dumps(evidence), now(), err))
        self.db.commit()
        return status


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def open_db(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    if 'final_url' not in {r[1] for r in db.execute('PRAGMA table_info(pages)')}:
        db.execute('ALTER TABLE pages ADD COLUMN final_url TEXT')   # caches made before it existed
    return db


def select_targets(df, only_missing=True):
    site = df.officialWebsite.where(~df.officialWebsite_domain.map(is_non_official))
    t = df.assign(site=site).dropna(subset=['site'])
    if only_missing:
        flags = t.dq_flags.fillna('')
        need = (t.email.isna() | t.yearEstablished.isna() | t.officialPhone.isna()
                | flags.str.contains('phone_shared|email_obfuscated|website_not_official'))
        t = t[need]
    # facilities sharing a website are each recorded, but pages are fetched once (page cache)
    return t[['unique_id', 'site']]


async def crawl_all(c, todo, concurrency, log=print):
    """Crawl [(unique_id, site), ...] with at most `concurrency` sites in progress at once.
    The cap covers the whole site visit, robots.txt included, so a big run can't open thousands
    of connections at start-up (which made most robots.txt requests fail on the first run)."""
    slots = asyncio.Semaphore(concurrency)
    status, total = Counter(), len(todo)

    async def one(uid, site):
        async with slots:
            try:
                status[await c.crawl_site(uid, site)] += 1
            except Exception as e:  # never let one bad site kill the run
                c._save(uid, site, 'error', 0, [], [], None, None, {}, {}, {}, repr(e)[:300])
                status['error'] += 1
        n = sum(status.values())
        if n % 25 == 0 or n == total:
            log(f'  {n}/{total} {dict(status)}')

    await asyncio.gather(*(one(u, s) for u, s in todo))
    return status


async def run_crawl(args):
    df = pd.read_csv(args.input, dtype=str, low_memory=False)
    targets = select_targets(df, only_missing=not args.all)
    db = open_db(args.db)
    if args.refresh:
        done = set()
    elif args.retry_failed:      # keep the successes, try every failed or errored facility again
        done = {r[0] for r in db.execute("SELECT unique_id FROM results WHERE status='ok'")}
    else:
        done = {r[0] for r in db.execute('SELECT unique_id FROM results')}
    todo = targets[~targets.unique_id.isin(done)]
    if args.limit:
        todo = todo.head(args.limit)
    print(f'{len(targets)} eligible facilities, {len(done)} skipped as already crawled, crawling {len(todo)}')
    if args.dry_run:
        db.close()
        return
    ua = f'FacilityDataBot/1.0 (+research; contact: {args.contact})'
    c = Crawler(db, ua, args.delay, args.concurrency, args.max_pages, args.timeout)
    started = now()
    try:
        await crawl_all(c, list(todo.itertuples(index=False)), args.concurrency,
                        log=lambda m: print(m, flush=True))
        errs = Counter(r[0] for r in db.execute(
            "SELECT error FROM results WHERE status!='ok' AND crawled_at>=?", (started,)))
        if errs:
            print('failure reasons this run:')
            for err, n in errs.most_common(10):
                print(f'  {n:6d}  {err}')
    finally:
        await c.close()
        db.close()


def run_reextract(args):
    """Re-run extraction on the cached pages of every successful crawl, with no network, and
    write the results like `export` does. Use it after changing the extraction rules."""
    db = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)        # read-only: the cache is untouched
    has_final = 'final_url' in {r[1] for r in db.execute('PRAGMA table_info(pages)')}
    by_domain = {}
    for url, html, final in db.execute(
            f"SELECT url, html, {'final_url' if has_final else 'NULL'} FROM pages "
            'WHERE html IS NOT NULL ORDER BY fetched_at'):
        page = final or url
        by_domain.setdefault(domain_of(page), []).append((page, html))
    out = pd.read_sql('SELECT * FROM results', db)
    db.close()
    memo, changed = {}, Counter()

    def pages_of(d):
        for page, html in by_domain.get(d, []):
            if page not in memo:
                memo[page] = extract(zlib.decompress(html).decode('utf-8', 'replace'), page)
            yield page, memo[page]

    for i, r in out[out.status == 'ok'].iterrows():
        # the site's own domain, plus any domain it redirected to (seen in the evidence URLs)
        doms = list(dict.fromkeys([domain_of(r.website)] + [domain_of(u) for u in
                                   json.loads(r.evidence or '{}').values() if u]))
        ex = [p for d in doms for p in pages_of(d)]
        if not ex:
            continue
        em, ph, year, bed, socials, jsonld, evidence = summarize(ex, doms[0])
        new = {'emails': json.dumps(em), 'phones': json.dumps(ph), 'year_established': year,
               'beds': bed, 'socials': json.dumps(socials), 'jsonld': json.dumps(jsonld),
               'evidence': json.dumps(evidence), 'pages_fetched': len(ex)}
        for k in ('beds', 'year_established'):
            old = None if pd.isna(out.at[i, k]) else int(out.at[i, k])
            changed[k] += old != new[k]
        changed['first email'] += (json.loads(r.emails or '[]')[:1] != em[:1])
        for k, v in new.items():
            out.at[i, k] = v
    out['beds'] = out.beds.astype('Int64')
    out['year_established'] = out.year_established.astype('Int64')
    out.to_csv(args.out, index=False)
    print(f'wrote {len(out)} rows to {args.out}; re-extracted {int((out.status == "ok").sum())} '
          f'successful sites; values changed: {dict(changed)}')


def run_export(args):
    db = sqlite3.connect(args.db)
    out = pd.read_sql('SELECT * FROM results', db)
    out.to_csv(args.out, index=False)
    print(f'wrote {len(out)} rows to {args.out}; status: {out.status.value_counts().to_dict()}')


def _loads(v, default):
    return json.loads(v) if isinstance(v, str) and v else default


def skip_reason(name, site, landed, shared, city=None):
    """Why the crawl's values can't be copied onto this facility, or None if they can.
    site: the domain the facility lists; landed: the URLs the values were found on."""
    if shared:
        return 'shared_website'
    if not site_matches_name(name, site, city):
        return 'name_mismatch'
    for u in landed:
        d = domain_of(u) if isinstance(u, str) else None
        if d and d != site and not d.endswith('.' + site) and not site.endswith('.' + d) \
                and not site_matches_name(name, d, city):
            return 'redirected_elsewhere'
    return None


def run_merge(args):
    """Fill ONLY empty fields; never overwrite. Every filled value carries provenance.

    Nothing is copied from a site that can't be tied to the facility (see skip_reason):
      * shared_website: a domain listed by --chain-threshold or more facilities is usually a
        chain's head office (or one branch's site listed for its namesakes), so its email, phone,
        founding year, bed count and Facebook page may describe another facility. Counted by
        domain, so x.com/delhi and x.com/pune count together.
      * name_mismatch: the listed domain doesn't carry the facility's name (Ayesha Clinic listed
        with jehangirhospital.com).
      * redirected_elsewhere: the site forwarded to a domain that doesn't carry the name either
        (an expired domain now pointing at an unrelated clinic).
    The values found stay in enrichment.csv and in crawl_phones_found / crawl_socials_found."""
    df = pd.read_csv(args.input, dtype=str, low_memory=False)
    en = pd.read_csv(args.enrichment, dtype=str).set_index('unique_id')
    filled_fields, sources, crawl_phones, crawl_socials, reasons = [], [], [], [], []
    counts, skipped = Counter(), Counter()
    site = df.officialWebsite_domain.fillna(df.officialWebsite.map(domain_of))
    site_n = site.map(site.value_counts()).fillna(0)
    city = df['address_city'] if 'address_city' in df else pd.Series(None, index=df.index)
    for i, row in df.iterrows():
        filled, src = [], {}
        e = en.loc[row.unique_id] if row.unique_id in en.index else None
        if e is None or e.status != 'ok':
            filled_fields.append(None); sources.append(None)
            crawl_phones.append(None); crawl_socials.append(None); reasons.append(None)
            continue
        ev = _loads(e.evidence, {})
        emails, phones, socials = _loads(e.emails, []), _loads(e.phones, []), _loads(e.socials, {})
        found_phones, year, beds, socials_fill = phones, e.year_established, e.beds, socials
        # row['name'], not row.name: on an iterrows() row, .name is the index label
        why = skip_reason(row['name'], site[i], ev.values(), site_n[i] >= args.chain_threshold, city[i])
        if why:
            emails, phones, year, beds, socials_fill = [], [], None, None, {}
            skipped[why] += 1
        if missing(row.email) and emails:
            df.at[i, 'email'] = emails[0]; filled.append('email'); src['email'] = ev.get('email')
        if missing(row.officialPhone) and phones:
            df.at[i, 'officialPhone'] = phones[0]
            df.loc[i, 'officialPhone_type'] = normalize_phone(phones[0])[1]
            filled.append('officialPhone'); src['officialPhone'] = ev.get('phone')
        if missing(row.yearEstablished) and not missing(year):
            df.at[i, 'yearEstablished'] = str(int(float(year)))
            filled.append('yearEstablished'); src['yearEstablished'] = ev.get('year')
        if missing(row.capacity) and not missing(beds):
            df.at[i, 'capacity'] = str(int(float(beds)))
            filled.append('capacity'); src['capacity'] = ev.get('beds')
        if missing(row.facebookLink) and socials_fill.get('facebook'):
            df.at[i, 'facebookLink'] = socials_fill['facebook']; filled.append('facebookLink')
            src['facebookLink'] = e.website
        counts.update(filled)
        filled_fields.append(';'.join(filled) or None)
        sources.append(json.dumps(src) if src else None)
        crawl_phones.append(json.dumps(found_phones) if found_phones else None)
        crawl_socials.append(json.dumps(socials) if socials else None)
        reasons.append(why)
    df['crawl_filled_fields'] = filled_fields
    df['crawl_sources'] = sources
    df['crawl_phones_found'] = crawl_phones
    df['crawl_socials_found'] = crawl_socials
    df['crawl_skip_reason'] = reasons
    df.to_csv(args.out, index=False)
    print(f'wrote {args.out}; fields filled: {dict(counts)}; '
          f'facilities left unfilled: {dict(skipped)}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('crawl')
    c.add_argument('--input', required=True)
    c.add_argument('--db', default='crawl.sqlite')
    c.add_argument('--contact', required=True, help='email/URL put in the User-Agent')
    c.add_argument('--limit', type=int)
    c.add_argument('--concurrency', type=int, default=8)
    c.add_argument('--delay', type=float, default=2.0, help='seconds between hits on one host')
    c.add_argument('--max-pages', type=int, default=4)
    c.add_argument('--timeout', type=float, default=15.0)
    c.add_argument('--all', action='store_true', help='crawl every eligible site, not just gaps')
    c.add_argument('--refresh', action='store_true', help='re-crawl facilities already done')
    c.add_argument('--retry-failed', action='store_true',
                   help='keep successful results, re-crawl every facility that failed or errored')
    c.add_argument('--dry-run', action='store_true')
    e = sub.add_parser('export'); e.add_argument('--db', default='crawl.sqlite'); e.add_argument('--out', required=True)
    x = sub.add_parser('reextract', help='re-run extraction on cached pages (no network), write like export')
    x.add_argument('--db', default='crawl.sqlite'); x.add_argument('--out', required=True)
    m = sub.add_parser('merge')
    m.add_argument('--input', required=True); m.add_argument('--enrichment', required=True)
    m.add_argument('--out', required=True)
    m.add_argument('--chain-threshold', type=int, default=3,
                   help='fill nothing from a website domain shared by this many facilities (a chain site)')
    args = ap.parse_args()
    if args.cmd == 'crawl':
        asyncio.run(run_crawl(args))
    elif args.cmd == 'export':
        run_export(args)
    elif args.cmd == 'reextract':
        run_reextract(args)
    else:
        run_merge(args)


if __name__ == '__main__':
    main()
