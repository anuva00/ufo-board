"""Turn downloaded government documents into source files that extraction
agents can read (data/sources/<slug>.json + .txt).

A long document is split into parts of at most PART chars on paragraph
boundaries; every part is its own source but shares one case file, so the
board shows them as one document.
"""
import html, json, re, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, SRC = ROOT / "data/raw", ROOT / "data/sources"
PART = 52000
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
PD = "Public domain (US Government work)"

# slug, title, kind, publisher, date, case_slug (None = the document is its own file), where the text comes from
DOCS = [
    ("hearing-2023-07-26-house-oversight-uap", "House Oversight hearing: Unidentified Anomalous Phenomena: Implications on National Security, Public Safety, and Government Transparency",
     "hearing", "US House Committee on Oversight and Accountability", "2023-07-26", None, ("govinfo", "CHRG-118hhrg53022")),
    ("hearing-2024-11-13-house-oversight-uap", "House Oversight joint hearing: Unidentified Anomalous Phenomena: Exposing the Truth",
     "hearing", "US House Committee on Oversight and Accountability", "2024-11-13", None, ("govinfo", "CHRG-118hhrg57440")),
    ("hearing-2025-09-09-house-oversight-uap", "House Oversight hearing: Restoring Public Trust Through UAP Transparency and Whistleblower Protection",
     "hearing", "US House Committee on Oversight and Government Reform", "2025-09-09", None, ("govinfo", "CHRG-119hhrg61718")),
    ("hearing-2023-04-19-senate-armed-services-aaro", "Senate Armed Services hearing: The Mission, Activities, Oversight, and Budget of the All-Domain Anomaly Resolution Office",
     "hearing", "US Senate Committee on Armed Services", "2023-04-19", None, ("govinfo", "CHRG-118shrg60390")),
    ("aaro-historical-record-report-vol-1", "AARO Historical Record Report Volume 1", "government report", "All-domain Anomaly Resolution Office", "2024-02", None, ("nara", 493468580, 499915937)),
    ("odni-preliminary-assessment-2021", "Preliminary Assessment: Unidentified Aerial Phenomena", "government report", "Office of the Director of National Intelligence", "2021-06-25", None, ("nara", 493468579, 493495781)),
    ("odni-annual-report-2022", "2022 Annual Report on Unidentified Aerial Phenomena", "government report", "Office of the Director of National Intelligence", "2023-01", None, ("nara", 493468579, 493495782)),
    ("aaro-annual-report-fy2023", "Fiscal Year 2023 Consolidated Annual Report on Unidentified Anomalous Phenomena", "government report", "All-domain Anomaly Resolution Office", "2023-10", None, ("nara", 493468579, 493470301)),
    ("aaro-annual-report-fy2024", "Fiscal Year 2024 Consolidated Annual Report on Unidentified Anomalous Phenomena", "government report", "All-domain Anomaly Resolution Office", "2024-11", None, ("nara", 493468579, 493470289)),
    ("aaro-gofast-case-resolution", "AARO Go Fast Case Resolution Card with Appendix", "case resolution", "All-domain Anomaly Resolution Office", None, "pentagon-ufo-videos", ("nara", 493468580, 499915940)),
    ("aaro-puerto-rico-case-resolution", "AARO Puerto Rico Case Resolution Card", "case resolution", "All-domain Anomaly Resolution Office", None, None, ("nara", 493468580, 499915942)),
    ("aaro-eglin-case-resolution", "AARO Pensacola Case Resolution (Eglin)", "case resolution", "All-domain Anomaly Resolution Office", None, None, ("nara", 493468580, 499915950)),
    ("aaro-southeast-asia-triangles-case-resolution", "AARO Case Resolution of 'Southeast Asia Triangles'", "case resolution", "All-domain Anomaly Resolution Office", None, None, ("nara", 493468580, 499915951)),
    ("aaro-atmospheric-wakes-case-resolution", "AARO Case Resolution of 'Atmospheric Wakes'", "case resolution", "All-domain Anomaly Resolution Office", None, None, ("nara", 493468580, 499915949)),
    ("aaro-western-united-states-case-resolution", "AARO Case Resolution of 'Western United States'", "case resolution", "All-domain Anomaly Resolution Office", None, None, ("nara", 493468580, 499915952)),
    ("ornl-metallic-specimen-analysis", "ORNL Analysis of a Metallic Specimen", "government report", "Oak Ridge National Laboratory for AARO", None, None, ("nara", 493468580, 499915944)),
]
_series = {}


def nara_text(series, naid):
    if series not in _series:
        req = urllib.request.Request(f"https://catalog.archives.gov/proxy/records/parentNaId/{series}?page=1&limit=100",
                                     headers={"User-Agent": "ufo-board-research/0.2"})
        hits = json.loads(urllib.request.urlopen(req, timeout=180).read())["body"]["hits"]["hits"]
        _series[series] = {h["_source"]["record"]["naId"]: h["_source"]["record"] for h in hits}
    rec = _series[series][naid]
    text = "\n\n".join((o.get("completeExtractedText") or "").strip() for o in rec.get("digitalObjects") or [])
    return text, f"https://catalog.archives.gov/id/{naid}"


def govinfo_text(pkg):
    s = (RAW / "hearings" / f"{pkg}.htm").read_text(encoding="utf8", errors="ignore")
    body = re.search(r"<pre>(.*)</pre>", s, flags=re.S).group(1)
    return html.unescape(re.sub(r"<[^>]+>", "", body)), f"https://www.govinfo.gov/content/pkg/{pkg}/html/{pkg}.htm"


def split(text):
    """Pack whole lines into parts of at most PART chars, preferring to cut at
    a blank line; a short tail is folded into the previous part."""
    parts, cur, last_blank = [], [], None
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > PART and cur:
            cut = last_blank if last_blank and last_blank > len(cur) // 2 else len(cur)
            parts.append("\n".join(cur[:cut]))
            cur = cur[cut:]
            size, last_blank = sum(len(l) + 1 for l in cur), None
        cur.append(line)
        size += len(line) + 1
        if not line.strip():
            last_blank = len(cur)
    tail = "\n".join(cur)
    if parts and len(tail.strip()) < 3000:
        parts[-1] += "\n" + tail
    elif tail.strip():
        parts.append(tail)
    return [p.strip("\n") for p in parts]


def main():
    for slug, title, kind, publisher, date, case_slug, origin in DOCS:
        text, url = govinfo_text(origin[1]) if origin[0] == "govinfo" else nara_text(origin[1], origin[2])
        text = re.sub(r"[ \t]+\n", "\n", text).strip()
        parts = split(text)
        for i, part in enumerate(parts, 1):
            s = slug if len(parts) == 1 else f"{slug}-p{i}"
            doc = {"slug": s, "title": title if len(parts) == 1 else f"{title} (part {i} of {len(parts)})", "url": url,
                   "revision_id": None, "fetched_at": NOW, "license": PD, "lat": None, "lon": None,
                   "kind": kind, "publisher": publisher, "date": date,
                   "case_slug": case_slug or slug, "case_name": None if case_slug else title,
                   "case_kind": "case" if (case_slug or kind == "case resolution") else "document",
                   "text": part}
            (SRC / f"{s}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
            (SRC / f"{s}.txt").write_text(part)
            print(f"{len(part):7d}  {s}")


if __name__ == "__main__":
    main()
