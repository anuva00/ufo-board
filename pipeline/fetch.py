"""Fetch source text for every case in data/cases.json.

Writes data/sources/<slug>.json (metadata + text) and <slug>.txt (text only,
what extraction agents read). Skips cases already fetched; pass --force to redo.
"""
import json, re, sys, time, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from wiki import api

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "sources"


def slugify(title):
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fetch(title):
    d = api({"action": "query", "redirects": 1, "titles": title,
             "prop": "extracts|revisions|coordinates|info",
             "explaintext": 1, "exsectionformat": "wiki",
             "rvprop": "ids|timestamp", "inprop": "url"})
    p = d["query"]["pages"][0]
    if p.get("missing"):
        raise SystemExit(f"missing page: {title}")
    rev = p["revisions"][0]
    co = (p.get("coordinates") or [{}])[0]
    return {
        "slug": slugify(p["title"]), "title": p["title"],
        "url": f"https://en.wikipedia.org/w/index.php?title={p['title'].replace(' ', '_')}&oldid={rev['revid']}",
        "revision_id": rev["revid"], "revision_time": rev["timestamp"],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license": "CC BY-SA 4.0 (Wikipedia contributors)",
        "lat": co.get("lat"), "lon": co.get("lon"),
        "text": p["extract"],
    }


def main():
    force = "--force" in sys.argv
    SRC.mkdir(parents=True, exist_ok=True)
    for title in json.loads((ROOT / "data" / "cases.json").read_text()):
        out = SRC / f"{slugify(title)}.json"
        if out.exists() and not force:
            continue
        doc = fetch(title)
        out = SRC / f"{doc['slug']}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        out.with_suffix(".txt").write_text(doc["text"])
        print(f"{len(doc['text']):7d} chars  {doc['slug']}", flush=True)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
