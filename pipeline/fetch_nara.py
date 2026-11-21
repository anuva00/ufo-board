"""Fetch record descriptions from the US National Archives catalog for the
series in its UAP collection, into data/raw/nara/<series>.jsonl.

  python3 pipeline/fetch_nara.py bluebook      Project Blue Book case files (10,000+ file units)
  python3 pipeline/fetch_nara.py rg615         agency transfers under the 2024 NDAA (FAA, OSD, ODNI, NRC)

Uses the catalog website's own public JSON endpoint, one request per second.
Each line keeps only what the board needs: identifier, title, dates, the
record's own notes, and the text NARA extracted from the first documents.
"""
import json, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/raw/nara"
API = "https://catalog.archives.gov/proxy/records/parentNaId/{naid}?page={page}&limit={limit}{extra}"
UA = "ufo-board-research/0.2 (evidence-board research tool)"

SERIES = {
    "bluebook": [(597821, "Project Blue Book case files (sanitized version)")],
    "rg615": [(493468575, "Federal Aviation Administration"), (493468580, "Office of the Secretary of Defense"),
              (493468579, "Office of the Director of National Intelligence"), (488808322, "Nuclear Regulatory Commission")],
}


def get(url, tries=5):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def date(d):
    return (d or {}).get("logicalDate")


def compact(rec, series_naid, series_name):
    objs = rec.get("digitalObjects") or []
    texts = [o.get("completeExtractedText") or o.get("extractedText") or "" for o in objs]
    text = "\n\n".join(t.strip() for t in texts if t.strip())
    return {
        "naId": rec.get("naId"), "series": series_naid, "series_name": series_name,
        "level": rec.get("levelOfDescription"), "title": rec.get("title"),
        "date_start": date(rec.get("coverageStartDate")) or date(rec.get("inclusiveStartDate")) or date(rec.get("productionDates", [{}])[0] if rec.get("productionDates") else None),
        "date_end": date(rec.get("coverageEndDate")) or date(rec.get("inclusiveEndDate")),
        "scope": rec.get("scopeAndContentNote"),
        "ids": [v.get("number") for v in rec.get("variantControlNumbers") or []],
        "n_objects": len(objs),
        "object_url": objs[0].get("objectUrl") if objs else None,
        "object_type": objs[0].get("objectType") if objs else None,
        "text": text[:60000],
        "url": f"https://catalog.archives.gov/id/{rec.get('naId')}",
    }


def fetch_series(naid, name, limit=100):
    out = OUT / f"{naid}.jsonl"
    seen = set()
    if out.exists():
        seen = {json.loads(l)["naId"] for l in out.read_text().splitlines() if l.strip()}
    total = None
    # The endpoint refuses to page past 10,000 records, so a long series is
    # read twice, in ascending and descending title order.
    for extra in ("", "&sort=title:asc", "&sort=title:desc"):
        page = 1
        while page <= 10000 // limit:
            try:
                d = get(API.format(naid=naid, page=page, limit=limit, extra=extra))
            except Exception as e:
                print(f"  stop {naid}{extra} page {page}: {e}", flush=True)
                break
            hits = d["body"]["hits"]
            total = total or hits["total"]["value"]
            rows = [compact(h["_source"]["record"], naid, name) for h in hits["hits"]]
            new = [r for r in rows if r["naId"] not in seen]
            with out.open("a") as f:
                for r in new:
                    seen.add(r["naId"])
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"  {naid}{extra} page {page}: +{len(new)} (have {len(seen)})", flush=True)
            if len(rows) < limit:
                break
            page += 1
            time.sleep(1)
        if total is not None and (len(seen) >= total and hits["total"]["relation"] == "eq"):
            break
        if total is not None and total < 10000:
            break
    print(f"{naid} {name}: {len(seen)} records", flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for key in sys.argv[1:] or SERIES:
        for naid, name in SERIES[key]:
            fetch_series(naid, name)
