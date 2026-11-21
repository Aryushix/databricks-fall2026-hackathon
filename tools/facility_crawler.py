#!/usr/bin/env python3
r"""
facility_crawler.py - enrich the cleaned facility dataset from each facility's OWN website.

Runs on your LAPTOP, not on Databricks (Free Edition blocks most outbound web traffic).
Uses the phone / email / domain helpers from src/hackathon/cleaning.py.

    pip install -r tools/requirements.txt

    python tools/facility_crawler.py crawl  --input facilities_cleaned.csv --db crawl.sqlite \
                                            --contact "you@example.com" --limit 200
    python tools/facility_crawler.py crawl  ... --retry-failed      # re-crawl only the sites that failed
    python tools/facility_crawler.py export --db crawl.sqlite --out enrichment.csv
    python tools/facility_crawler.py merge  --input facilities_cleaned.csv \
                                            --enrichment enrichment.csv --out facilities_enriched.csv

Then upload facilities_enriched.csv to /Volumes/workspace/hackathon/raw/ and run
notebooks/01_clean_facilities: it loads the enriched file in place of the raw data.

What it collects per facility: emails (incl. Cloudflare-obfuscated ones), phones, year
established, bed count, social links, schema.org JSON-LD (telephone, email, foundingDate, geo).

Politeness / safety
  * robots.txt honoured per host, Crawl-delay honoured. An unreachable robots.txt means the host is
    skipped and recorded as robots_unreachable_<error> (retry later with --retry-failed); a
    robots.txt that forbids us is recorded as robots_disallowed
  * one request at a time per host + configurable delay; global concurrency cap
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
BEDS_RE = re.compile(r'\b(\d{1,4})\s*(?:\+\s*)?[- ]?(?:bedded|beds?)\b', re.I)
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
    out['beds'] += [int(b) for b in BEDS_RE.findall(text) if 5 <= int(b) <= 5000]

    for link in out['links']:
        d = domain_of(link) or ''
        for dom, name in SOCIAL.items():
            if d == dom or d.endswith('.' + dom):
                out['socials'].setdefault(name, link.split('?')[0])
    return out


def clean_found(emails, phones, years):
    em = []
    for e in emails:
        e = e.strip().lower().strip('.,;:')
        if EMAIL_RE.match(e) and not JUNK_EMAIL.search(e) and e not in em:
            em.append(e)
    ph = []
    for p in phones:
        n, t = normalize_phone(p)
        if t in ('mobile', 'landline', 'mobile_or_landline', 'toll_free', 'shared_cost') and n not in ph:
            ph.append(n)
    this_year = datetime.now().year
    yrs = [y for y in years if 1800 <= y <= this_year]
    year = Counter(yrs).most_common(1)[0][0] if yrs else None
    return em, ph, year


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
class Crawler:
    def __init__(self, db, user_agent, delay, concurrency, max_pages, timeout, use_cache=True):
        self.db = db
        self.ua = user_agent
        self.delay = delay
        self.sem = asyncio.Semaphore(concurrency)
        self.max_pages = max_pages
        self.use_cache = use_cache
        self.robots, self.robots_lock, self.host_lock, self.host_next = {}, {}, {}, {}
        self.robots_error = {}
        self.client = httpx.AsyncClient(
            headers={'User-Agent': user_agent, 'Accept': 'text/html,application/xhtml+xml'},
            timeout=timeout, follow_redirects=True, max_redirects=5)

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
        p = urlparse(url)
        host = f'{p.scheme}://{p.netloc.lower()}'          # robots.txt is per scheme+host
        async with self.robots_lock.setdefault(host, asyncio.Lock()):
            if host not in self.robots:
                await self._load_robots(p, host)
        return self.robots[host].can_fetch(self.ua, url)

    async def _load_robots(self, p, host):
        rp = RobotFileParser()
        err = None
        for attempt in range(3):
            try:
                async with self.sem:                     # robots.txt counts against --concurrency too
                    r = await self.client.get(f'{host}/robots.txt')
                if r.status_code >= 500:
                    rp.disallow_all = True               # RFC 9309: server error => assume disallow
                elif r.status_code >= 400:
                    rp.allow_all = True                  # no robots.txt => allowed
                else:
                    rp.parse(r.text.splitlines())
                err = None
                break
            except httpx.HTTPError as e:
                err = type(e).__name__
                await asyncio.sleep(2 ** attempt)
        if err:
            rp.disallow_all = True                       # unreachable => don't crawl
            self.robots_error[host] = err
        self.robots[host] = rp

    def _cached(self, url):
        row = self.db.execute('SELECT status, html, error FROM pages WHERE url=?', (url,)).fetchone()
        if row and row[1] is not None:
            return row[0], zlib.decompress(row[1]).decode('utf-8', 'replace'), url
        return None

    async def fetch(self, url):
        """Return (status, html|None, final_url, error|None)."""
        if self.use_cache and (c := self._cached(url)):
            return c[0], c[1], c[2], None
        if not await self.allowed(url):
            p = urlparse(url)
            err = self.robots_error.get(f'{p.scheme}://{p.netloc.lower()}')
            return None, None, url, f'robots_unreachable_{err}' if err else 'robots_disallowed'
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
            self._store(url, host, status, ctype, html, None if html else f'http_{status}_{ctype[:30]}')
            return status, html, final, None if html else f'http_{status}'
        return None, None, url, 'retries_exhausted'

    def _store(self, url, host, status, ctype, html, err):
        self.db.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?)',
                        (url, host, status, ctype, now(),
                         zlib.compress(html.encode()) if html else None, err))
        self.db.commit()

    async def crawl_site(self, uid, website):
        guessed = not re.match(r'^https?://', website, re.I)
        start = 'https://' + website if guessed else website
        status, html, final, err = await self.fetch(start)
        if html is None and guessed:                       # bare domain: fall back to http
            status, html, final, err = await self.fetch('http://' + website)
        if html is None:
            return self._save(uid, website, 'failed', 0, [], [], None, None, {}, {}, {}, err)

        base = domain_of(final)
        pages = [(final, html)]
        first = extract(html, final)
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
                pages.append((f, h))

        emails, phones, years, beds, socials, jsonld, evidence = [], [], [], [], {}, {}, {}
        for url, h in pages:
            x = first if url == final else extract(h, url)
            for field, vals in (('email', x['emails']), ('phone', x['phones']),
                                ('year', x['years']), ('beds', x['beds'])):
                if vals:
                    evidence.setdefault(field, url)
            emails += x['emails']; phones += x['phones']; years += x['years']; beds += x['beds']
            for k, v in x['socials'].items():
                socials.setdefault(k, v)
            jsonld.update(x['jsonld'])
        em, ph, year = clean_found(emails, phones, years)
        bed = Counter(beds).most_common(1)[0][0] if beds else None
        return self._save(uid, website, 'ok', len(pages), em, ph, year, bed, socials, jsonld,
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


async def run_crawl(args):
    df = pd.read_csv(args.input, dtype=str, low_memory=False)
    targets = select_targets(df, only_missing=not args.all)
    db = open_db(args.db)
    done = set() if args.refresh else {
        uid for uid, st in db.execute('SELECT unique_id, status FROM results')
        if not (args.retry_failed and st != 'ok')}
    todo = targets[~targets.unique_id.isin(done)]
    if args.limit:
        todo = todo.head(args.limit)
    print(f'{len(targets)} eligible facilities, {len(done)} already crawled, crawling {len(todo)}')
    if args.dry_run:
        return
    ua = f'FacilityDataBot/1.0 (+research; contact: {args.contact})'
    c = Crawler(db, ua, args.delay, args.concurrency, args.max_pages, args.timeout)
    status = Counter()

    async def one(uid, site):
        try:
            status[await c.crawl_site(uid, site)] += 1
        except Exception as e:  # never let one bad site kill the run
            c._save(uid, site, 'error', 0, [], [], None, None, {}, {}, {}, repr(e)[:300])
            status['error'] += 1
        n = sum(status.values())
        if n % 25 == 0 or n == len(todo):
            print(f'  {n}/{len(todo)} {dict(status)}', flush=True)

    try:
        await asyncio.gather(*(one(u, s) for u, s in todo.itertuples(index=False)))
    finally:
        await c.close()
        db.close()


def run_export(args):
    db = sqlite3.connect(args.db)
    out = pd.read_sql('SELECT * FROM results', db)
    out.to_csv(args.out, index=False)
    print(f'wrote {len(out)} rows to {args.out}; status: {out.status.value_counts().to_dict()}')


def run_merge(args):
    """Fill ONLY empty fields; never overwrite. Every filled value carries provenance."""
    df = pd.read_csv(args.input, dtype=str, low_memory=False)
    en = pd.read_csv(args.enrichment, dtype=str).set_index('unique_id')
    filled_fields, sources, crawl_phones, crawl_socials = [], [], [], []
    counts = Counter()
    # a website shared by many facilities is usually a chain's HQ site: its contact details
    # are recorded in crawl_* columns but not copied into the facility's own fields
    site_n = df.officialWebsite.map(df.officialWebsite.value_counts())
    for i, row in df.iterrows():
        filled, src = [], {}
        e = en.loc[row.unique_id] if row.unique_id in en.index else None
        if e is None or e.status != 'ok':
            filled_fields.append(None); sources.append(None)
            crawl_phones.append(None); crawl_socials.append(None)
            continue
        ev = json.loads(e.evidence or '{}')
        emails, phones = json.loads(e.emails or '[]'), json.loads(e.phones or '[]')
        socials = json.loads(e.socials or '{}')
        found_phones = phones
        if site_n[i] >= args.chain_threshold:
            emails, phones = [], []
        if missing(row.email) and emails:
            df.at[i, 'email'] = emails[0]; filled.append('email'); src['email'] = ev.get('email')
        if missing(row.officialPhone) and phones:
            df.at[i, 'officialPhone'] = phones[0]
            df.at[i, 'officialPhone_type'] = normalize_phone(phones[0])[1]
            filled.append('officialPhone'); src['officialPhone'] = ev.get('phone')
        if missing(row.yearEstablished) and not missing(e.year_established):
            df.at[i, 'yearEstablished'] = str(int(float(e.year_established)))
            filled.append('yearEstablished'); src['yearEstablished'] = ev.get('year')
        if missing(row.capacity) and not missing(e.beds):
            df.at[i, 'capacity'] = str(int(float(e.beds)))
            filled.append('capacity'); src['capacity'] = ev.get('beds')
        if missing(row.facebookLink) and socials.get('facebook'):
            df.at[i, 'facebookLink'] = socials['facebook']; filled.append('facebookLink')
            src['facebookLink'] = e.website
        counts.update(filled)
        filled_fields.append(';'.join(filled) or None)
        sources.append(json.dumps(src) if src else None)
        crawl_phones.append(json.dumps(found_phones) if found_phones else None)
        crawl_socials.append(json.dumps(socials) if socials else None)
    df['crawl_filled_fields'] = filled_fields
    df['crawl_sources'] = sources
    df['crawl_phones_found'] = crawl_phones
    df['crawl_socials_found'] = crawl_socials
    df.to_csv(args.out, index=False)
    print(f'wrote {args.out}; fields filled: {dict(counts)}')


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
    c.add_argument('--retry-failed', action='store_true', help='re-crawl facilities whose crawl failed')
    c.add_argument('--dry-run', action='store_true')
    e = sub.add_parser('export'); e.add_argument('--db', default='crawl.sqlite'); e.add_argument('--out', required=True)
    m = sub.add_parser('merge')
    m.add_argument('--input', required=True); m.add_argument('--enrichment', required=True)
    m.add_argument('--out', required=True)
    m.add_argument('--chain-threshold', type=int, default=3,
                   help='skip email/phone fill when this many facilities share the website')
    args = ap.parse_args()
    if args.cmd == 'crawl':
        asyncio.run(run_crawl(args))
    elif args.cmd == 'export':
        run_export(args)
    else:
        run_merge(args)


if __name__ == '__main__':
    main()
