"""Phase 3: parallel runs match single-process runs exactly; query-mode CLI; JSON run log. SYNTHETIC data only."""

import gzip
import json
import sqlite3
import zlib

import pandas as pd
import pytest
from test_verification_pipeline import FACILITIES

from hackathon.verification.__main__ import main
from hackathon.verification.pipeline import Verifier

pytest.importorskip("bs4")


def _crawl_db(path):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE pages (url TEXT PRIMARY KEY, host TEXT, status INTEGER, content_type TEXT,
                        fetched_at TEXT, html BLOB, error TEXT, final_url TEXT);
                        CREATE TABLE results (unique_id TEXT PRIMARY KEY, website TEXT, status TEXT);""")
    html = ("<html><main><h1>Kaveri Heart Hospital</h1><p>Our cath lab performs cardiac catheterization "
            "around the clock.</p></main></html>")
    db.execute("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)", ("https://kaverihearthospital.example/", "kaverihearthospital.example",
               200, "text/html", "2026-11-21T00:00:00+00:00", zlib.compress(html.encode()), None, None))
    db.execute("INSERT INTO results VALUES (?,?,?)", ("confirmed", "https://kaverihearthospital.example", "ok"))
    db.commit()
    db.close()


def _frame():
    df = pd.DataFrame(FACILITIES).astype(object)
    return df.where(df.notna(), None)


def _strip(recs):
    return [{k: v for k, v in r.items() if k != "run_timestamp"} for r in recs]


def test_parallel_run_is_identical_to_single_process(tmp_path):
    _crawl_db(tmp_path / "crawl.sqlite")
    serial = list(Verifier().build(_frame(), tmp_path / "crawl.sqlite").run())
    v = Verifier(workers=2, min_parallel_claims=0).build(_frame(), tmp_path / "crawl.sqlite")
    assert v._tag_workers(10) == 2
    parallel = list(v.run())
    assert len(serial) == len(FACILITIES) * 2 and _strip(parallel) == _strip(serial)


def test_cli_run_with_workers_and_json_log_then_query_modes(tmp_path, capsys):
    _crawl_db(tmp_path / "crawl.sqlite")
    _frame().to_csv(tmp_path / "facilities.csv", index=False)
    outs = {}
    for w in (1, 2):
        out = tmp_path / f"out{w}"
        assert main(["run", "--facilities", str(tmp_path / "facilities.csv"), "--crawl-db", str(tmp_path / "crawl.sqlite"),
                     "--out-dir", str(out), "--workers", str(w), "--log-format", "json"]) == 0
        outs[w] = [json.loads(x) for x in (out / "records.jsonl").read_text().splitlines()]
        events = [json.loads(x)["event"] for x in (out / "run_log.jsonl").read_text().splitlines() if '"event"' in x]
        assert events[0] == "run_start" and events[-1] == "run_done" and {"ledger", "tagging", "build", "verify"} <= set(events)
        assert "Kaveri" not in (out / "run_log.jsonl").read_text()          # logs carry counts, never claim text
    assert _strip(outs[1]) == _strip(outs[2])
    assert json.loads((tmp_path / "out1" / "summary.json").read_text()) == json.loads((tmp_path / "out2" / "summary.json").read_text())

    gz = tmp_path / "records.jsonl.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        fh.write((tmp_path / "out1" / "records.jsonl").read_text())
    capsys.readouterr()
    assert main(["profile", "--records", str(gz), "--hospital-id", "confirmed"]) == 0
    prof = json.loads(capsys.readouterr().out)
    assert [p["treatment"] for p in prof] == ["cardiac_catheterization"] and prof[0]["status"] == "CONFIRMED"
    assert main(["rank", "--records", str(gz), "--treatment", "cardiac_catheterization", "--lat", "18.5", "--lon", "73.8",
                 "--radius-km", "50"]) == 0
    ranked = json.loads(capsys.readouterr().out)
    assert [r["status"] for r in ranked] == ["CONFIRMED", "LIKELY", "LIKELY"] and ranked[0]["straight_line_distance_km"] == 0.0
    assert main(["show", "--records", str(gz), "--hospital-id", "likely", "--treatment", "cardiac_catheterization"]) == 0
    assert json.loads(capsys.readouterr().out)["verification_status"] == "LIKELY"
