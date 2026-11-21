"""Tests for tools/facility_crawler.py on made-up pages and a mocked network (no real sites).

Run:  pip install pandas numpy phonenumbers httpx beautifulsoup4 pytest && pytest -q
"""

import asyncio
import importlib.util
import sqlite3
from argparse import Namespace
from pathlib import Path

import pandas as pd
import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("bs4")

_spec = importlib.util.spec_from_file_location(
    "facility_crawler", Path(__file__).resolve().parents[1] / "tools" / "facility_crawler.py")
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


# ---- extraction rules ------------------------------------------------------ #
@pytest.mark.parametrize(
    "text, expected",
    [
        # history vs today (real wording from a cached hospital page)
        ("Our journey began with a humble opening in 1997 with 10 beds and initially 22 doctors. "
         "Today, we have a total of 324 beds with one of the largest Intensive Care Units.", [324]),
        ("Starting as a modest 22-bed pediatric nursing home in Mumbai, we've grown.", []),
        ("The hospital started in 1970 with 100 patient beds has now grown to a well known 1100 beds "
         "multi specialty hospital.", [1100]),
        ("Mega Hospital: by starting six institutions and Mega Hospital of 1300 beds.", [1300]),
        # a timeline page: years followed by "Beds Increased" are not bed counts
        ("1990 Beds Increased to 675 & Urology started. 2000 Beds Increased to 1100. "
         "Bed strength increased to 1625 beds.", [1625]),
        # units, not the facility
        ("We have a 10 bedded ICU and a 6-bed dialysis unit.", []),
        ("Our NICU with 12 beds is staffed 24x7.", []),
        ("Critical care: 40 beds.", []),
        ("A 500 bed hospital, 50 ICU beds and 24x7 emergency and 300 beds.", [500, 300]),
        # chain totals
        ("Over 4,290 beds across 18 hospitals.", []),
        ("A network of 1,440 beds.", []),
        # thousands separator, range limits
        ("A 1,300 bed tertiary care hospital.", [1300]),
        ("A 2 bed clinic, 12,000 beds nationally.", []),
    ],
)
def test_bed_counts(text, expected):
    assert fc.bed_counts(text) == expected


def test_rank_emails_prefers_the_sites_own_address():
    found = ["123vastu@gmail.com", "drbagchiivf@gmail.com"]
    assert fc.rank_emails(found, "drbagchiivf.com")[0] == "drbagchiivf@gmail.com"
    found = ["dev@agency.com", "help@gmail.com", "info@sitename.in"]
    assert fc.rank_emails(found, "sitename.in") == ["info@sitename.in", "dev@agency.com", "help@gmail.com"]


def test_extract_uses_mailto_cloudflare_and_text():
    html = """<html><body>
      <a href="mailto:Info@Clinic.in">mail</a>
      <a href="/cdn-cgi/l/email-protection#{cf}">hidden</a>
      <p>Call 98765 43210. Established in 1998. A 120-bed hospital with a 10 bedded ICU.</p>
      <a href="https://facebook.com/clinic?ref=x">fb</a>
    </body></html>"""
    key = 0x42
    cf = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in "desk@clinic.in")
    x = fc.extract(html.format(cf=cf), "https://clinic.in/")
    em, ph, year = fc.clean_found(x["emails"], x["phones"], x["years"], "clinic.in")
    assert em[:2] == ["info@clinic.in", "desk@clinic.in"]
    assert ph == ["+919876543210"] and year == 1998
    assert x["beds"] == [120] and x["socials"] == {"facebook": "https://facebook.com/clinic"}


# ---- merge ----------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name, domain, expected",
    [
        ("Amrita Hospital", "amritahospital.org", True),
        ("Dr Nawale's DentoCare", "drnawaledentocare.com", True),
        ("Netaji Subhas Medical College & Hospital", "nsmch.com", True),   # initials
        ("Holy Family Hospital, Jaipur", "cblmhfh.com", True),              # initials
        ("Hero DMC Heart Institute", "dmch.edu", True),
        # real mismatches from the first crawl: the facility is listed with another site
        ("Ayesha Clinic", "jehangirhospital.com", False),
        ("Pooja Hospital", "poonahospital.org", False),
        ("Tripathi Hospital", "normanendoassoc.org", False),
        ("SMS Hospital", "ssdhospital.in", False),
        ("City Hospital", "cityhospital.in", False),                       # nothing distinctive
        (None, "x.in", False),
    ],
)
def test_site_matches_name(name, domain, expected):
    assert fc.site_matches_name(name, domain) is expected


def _merge(tmp_path, facilities, enrichment):
    inp, en, out = tmp_path / "in.csv", tmp_path / "en.csv", tmp_path / "out.csv"
    pd.DataFrame(facilities).to_csv(inp, index=False)
    pd.DataFrame(enrichment).to_csv(en, index=False)
    fc.run_merge(Namespace(input=inp, enrichment=en, out=out, chain_threshold=2))
    return pd.read_csv(out, dtype=str).set_index("unique_id")


def test_merge_fills_only_empty_fields_and_skips_sites_it_cant_tie_to_the_facility(tmp_path):
    base = dict(email=None, officialPhone=None, yearEstablished=None, capacity=None, facebookLink=None)
    facilities = [
        dict(unique_id="own", name="Own Clinic", officialWebsite="ownclinic.in",
             officialWebsite_domain="ownclinic.in", **base),
        dict(unique_id="kept", name="Kept Hospital", officialWebsite="kept.in", officialWebsite_domain="kept.in",
             **{**base, "capacity": "75", "email": "old@kept.in"}),
        # Lilavati Mumbai's site listed for both Mumbai and Nashik: can't tell whose figures they are
        *[dict(unique_id=f"lila{i}", name="Lilavati Hospital", officialWebsite=f"lilavatihospital.com/{i}",
               officialWebsite_domain="lilavatihospital.com", **base) for i in range(2)],
        dict(unique_id="ayesha", name="Ayesha Clinic", officialWebsite="jehangirhospital.com",
             officialWebsite_domain="jehangirhospital.com", **base),
        # tripathihospital.com now forwards to an unrelated clinic's domain
        dict(unique_id="tripathi", name="Tripathi Hospital", officialWebsite="tripathihospital.com",
             officialWebsite_domain="tripathihospital.com", **base),
        # amritahospital.org forwards to amritahospitals.org: still theirs
        dict(unique_id="amrita", name="Amrita Hospital", officialWebsite="amritahospital.org",
             officialWebsite_domain="amritahospital.org", **base),
    ]
    found = dict(status="ok", emails='["new@x.in"]', phones='["+919876543210"]', year_established="1990",
                 beds="600", socials='{"facebook": "https://facebook.com/x"}')
    landed = {"tripathi": "https://www.normanendoassoc.org/book", "amrita": "https://www.amritahospitals.org/"}
    enrichment = [dict(unique_id=f["unique_id"], website=f["officialWebsite"], **found,
                       evidence='{"email": "%s"}' % landed.get(f["unique_id"], "https://" + f["officialWebsite"]))
                  for f in facilities]
    out = _merge(tmp_path, facilities, enrichment)
    assert out.loc["amrita", "email"] == "new@x.in"

    assert out.loc["own", "capacity"] == "600" and out.loc["own", "email"] == "new@x.in"
    assert out.loc["own", "crawl_filled_fields"] == "email;officialPhone;yearEstablished;capacity;facebookLink"
    assert out.loc["kept", "capacity"] == "75" and out.loc["kept", "email"] == "old@kept.in"  # never overwritten
    fields = ["email", "officialPhone", "yearEstablished", "capacity", "facebookLink"]
    for uid, reason in [("lila0", "shared_website"), ("lila1", "shared_website"), ("ayesha", "name_mismatch"),
                        ("tripathi", "redirected_elsewhere")]:
        row = out.loc[uid]   # nothing copied, but what was found is kept
        assert row[fields].isna().all() and row.crawl_skip_reason == reason
        assert "+919876543210" in row.crawl_phones_found


# ---- crawling (mocked network) --------------------------------------------- #
def _run_crawl(handler, sites, concurrency, tmp_path):
    db = fc.open_db(str(tmp_path / "crawl.sqlite"))

    async def go():
        c = fc.Crawler(db, "test-bot", delay=0, concurrency=concurrency, max_pages=2, timeout=5,
                       transport=httpx.MockTransport(handler))
        try:
            return await fc.crawl_all(c, sites, concurrency, log=lambda m: None)
        finally:
            await c.close()

    status = asyncio.run(go())
    return status, db


def test_crawl_caps_concurrency_including_robots(tmp_path):
    inflight, peak = 0, 0

    async def handler(request):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.005)
        inflight -= 1
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p>Email info@site.in. A 50 bed hospital.</p>")

    sites = [(f"id{i}", f"site{i}.in") for i in range(300)]
    status, db = _run_crawl(handler, sites, 4, tmp_path)
    assert status == {"ok": 300}
    assert peak <= 4                      # the first version started ~8,000 robots.txt requests at once
    assert db.execute("SELECT COUNT(*) FROM results WHERE beds=50").fetchone()[0] == 300


def test_crawl_labels_why_a_site_was_skipped(tmp_path):
    def handler(request):
        host, path = request.url.host, request.url.path
        if host == "down.in":
            raise httpx.ConnectTimeout("timed out", request=request)
        if host == "private.in" and path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        if host == "broken.in" and path == "/robots.txt":
            return httpx.Response(503)
        if path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hello</p>")

    sites = [("a", "https://down.in"), ("b", "https://private.in"), ("c", "https://broken.in"),
             ("d", "https://fine.in")]
    status, db = _run_crawl(handler, sites, 2, tmp_path)
    errors = dict(db.execute("SELECT unique_id, error FROM results"))
    assert errors == {"a": "robots_unreachable:ConnectTimeout", "b": "robots_disallowed",
                      "c": "robots_server_error:503", "d": None}
    assert status == {"failed": 3, "ok": 1}


def test_reextract_reruns_rules_on_cached_pages(tmp_path):
    db_path = tmp_path / "crawl.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(fc.SCHEMA)          # a cache from the first version: no final_url column
    html = "<p>We began in 1997 with 10 beds. Today we have 324 beds. Mail 123vastu@gmail.com or " \
           "info@lila.com</p>"
    db.execute("INSERT INTO pages VALUES (?,?,?,?,?,?,?)",
               ("https://lila.com", "lila.com", 200, "text/html", "t", fc.zlib.compress(html.encode()), None))
    db.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("L", "lila.com", "ok", 1, '["123vastu@gmail.com"]', "[]", None, 10, "{}", "{}",
                '{"beds": "https://lila.com"}', "t", None))
    db.commit()
    db.close()
    fc.run_reextract(Namespace(db=str(db_path), out=tmp_path / "en.csv"))
    row = pd.read_csv(tmp_path / "en.csv").iloc[0]
    assert row.beds == 324 and row.emails.startswith('["info@lila.com"')
