"""Build the wide layer: every individual sighting report from the bulk
datasets, normalised to date / place / coordinates / shape / summary.

Reads data/raw/ (see README for how each file is fetched) and writes
  ui/reports.json        columnar arrays for the map and timeline
  ui/rep/<n>.json        report details in chunks of CHUNK, loaded on demand

No language model is involved. Coordinates are city-level at best; a report
whose place cannot be matched to a town is kept for the timeline but not mapped.
"""
import csv, html, json, re, statistics, sys, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

csv.field_size_limit(10**8)
ROOT = Path(__file__).resolve().parent.parent
RAW, UI = ROOT / "data/raw", ROOT / "ui"
CHUNK = 4000

DATASETS = [
    {"id": "nuforc", "name": "NUFORC public reports", "who": "National UFO Reporting Center (civilian hotline), 2014 geocoded snapshot",
     "url": "https://github.com/planetsig/ufo-reports", "gov": False},
    {"id": "bluebook", "name": "Project Blue Book case files", "who": "US Air Force, held by the US National Archives",
     "url": "https://catalog.archives.gov/id/597821", "gov": True},
    {"id": "geipan", "name": "GEIPAN case files", "who": "French space agency CNES",
     "url": "https://www.geipan.fr/fr/recherche/cas", "gov": True},
    {"id": "moduk", "name": "UK Ministry of Defence UFO reports", "who": "UK Ministry of Defence, 1997 to 2009",
     "url": "https://www.gov.uk/government/publications/ufo-reports-in-the-uk", "gov": True},
    {"id": "faa", "name": "FAA pilot and controller reports", "who": "US Federal Aviation Administration, National Archives RG 615",
     "url": "https://catalog.archives.gov/id/493468575", "gov": True},
]
DS = {d["id"]: i for i, d in enumerate(DATASETS)}
SHAPES = ["light", "disc", "triangle", "sphere", "cylinder", "oval", "fireball", "formation", "boomerang", "other", "unknown"]
SH = {s: i for i, s in enumerate(SHAPES)}
NUFORC_SHAPE = {"light": "light", "flash": "light", "disk": "disc", "triangle": "triangle", "delta": "triangle", "pyramid": "triangle",
                "circle": "sphere", "sphere": "sphere", "round": "sphere", "dome": "sphere", "cylinder": "cylinder", "cigar": "cylinder",
                "oval": "oval", "egg": "oval", "teardrop": "oval", "fireball": "fireball", "flare": "fireball", "formation": "formation",
                "chevron": "boomerang", "crescent": "boomerang", "rectangle": "other", "diamond": "other", "cross": "other",
                "cone": "other", "changing": "other", "hexagon": "other", "other": "other"}
KEYWORDS = [("triangle", r"triang|delta[- ]shaped|pyramid"), ("disc", r"\bdis[ck]s?\b|saucer|soucoupe|disque|frisbee"),
            ("cylinder", r"cigar|cylind|tube[- ]shaped|rocket[- ]shaped|sausage"), ("fireball", r"fire ?ball|boule de feu|ball of fire|flame"),
            ("boomerang", r"boomerang|chevron|v[- ]shaped|crescent"), ("oval", r"\boval|egg[- ]shaped|ovale|ovo[iï]de|teardrop"),
            ("sphere", r"spher|\borbs?\b|\bballs?\b|globe|\bround\b|circular|boule|sph[eè]re|circle"),
            ("formation", r"formation|\bcluster\b"), ("light", r"light|lumi[eè]re|lumineu|glow|bright|star[- ]like|point brillant|lueur")]
KEYWORDS = [(s, re.compile(p, re.I)) for s, p in KEYWORDS]
MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}
MON3 = {m[:3]: i for m, i in MONTHS.items()}
STATES = {"alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co", "connecticut": "ct",
          "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia",
          "kansas": "ks", "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi",
          "minnesota": "mn", "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv", "new hampshire": "nh",
          "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc", "north dakota": "nd", "ohio": "oh",
          "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc", "south dakota": "sd",
          "tennessee": "tn", "texas": "tx", "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
          "wisconsin": "wi", "wyoming": "wy", "district of columbia": "dc", "washington dc": "dc", "puerto rico": "pr"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def shape_from_text(text):
    for s, rx in KEYWORDS:
        if rx.search(text or ""):
            return s
    return "unknown"


def clean(text, n=260):
    t = html.unescape(html.unescape(text or "")).replace("&#44", ",").replace("\ufffd", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


class Gazetteer:
    def __init__(self, nuforc_rows):
        us, st = defaultdict(list), defaultdict(list)
        for r in nuforc_rows:
            if r["country"] == "us" and r["state"] and r["lat"] is not None:
                us[(norm(re.sub(r"\(.*?\)", "", r["city"])), r["state"])].append((r["lat"], r["lon"]))
                st[r["state"]].append((r["lat"], r["lon"]))
        med = lambda pts: (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
        self.us = {k: med(v) for k, v in us.items()}
        import geonamescache
        gc = geonamescache.GeonamesCache()
        self.world = defaultdict(list)
        for c in gc.get_cities().values():
            for name in {c["name"], *c.get("alternatenames", [])[:0]}:
                self.world[norm(name)].append((c["population"], c["countrycode"], c["latitude"], c["longitude"], c.get("admin1code")))
        self.countries = {norm(c["name"]): c["iso"] for c in gc.get_countries().values()}
        self.countries.update({"england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB", "great britain": "GB",
                               "korea": "KR", "north korea": "KP", "south korea": "KR", "west germany": "DE", "germany": "DE",
                               "french morocco": "MA", "formosa": "TW", "soviet union": "RU", "ussr": "RU", "okinawa": "JP",
                               "newfoundland": "CA", "labrador": "CA", "canal zone": "PA", "philippine islands": "PH", "persia": "IR"})

    def us_city(self, city, state):
        hit = self.us.get((norm(city), state))
        if hit:
            return hit
        cands = [c for c in self.world.get(norm(city), []) if c[1] == "US" and (c[4] or "").lower() == state]
        return (max(cands)[2], max(cands)[3]) if cands else None

    def city(self, name, iso=None):
        cands = [c for c in self.world.get(norm(name), []) if iso is None or c[1] == iso]
        return (max(cands)[2], max(cands)[3]) if cands else None


def load_nuforc():
    rows = []
    with open(RAW / "nuforc/planetsig.csv", newline="", encoding="utf8", errors="replace") as f:
        for r in csv.reader(f):
            if len(r) < 11:
                continue
            m = re.match(r"(\d+)/(\d+)/(\d{4})", r[0])
            if not m:
                continue
            try:
                lat, lon = float(r[9]), float(r[10])
            except ValueError:
                lat = lon = None
            if lat == 0 and lon == 0:
                lat = lon = None
            place = ", ".join(x for x in (r[1].title(), r[2].upper(), r[3].upper() if r[3] not in ("us", "") else "") if x)
            rows.append({"ds": "nuforc", "y": int(m[3]), "m": int(m[1]), "d": int(m[2]), "lat": lat, "lon": lon,
                         "city": r[1], "state": r[2], "country": r[3], "place": place,
                         "shape": NUFORC_SHAPE.get(r[4].strip().lower(), "unknown"), "sum": clean(r[7]), "ref": ""})
    return rows


def load_bluebook(gaz):
    rows, stats = [], Counter()
    for line in open(RAW / "nara/597821.jsonl", encoding="utf8"):
        r = json.loads(line)
        title = r["title"] or ""
        m = re.search(r",?\s*(?:(January|February|March|April|May|June|July|August|September|October|November|December)\s+)?((?:19)\d{2})\s*$", title)
        if not m:
            stats["no date"] += 1
            continue
        loc = title[: m.start()].strip(" ,")
        toks = [t.strip() for t in loc.split(",") if t.strip()]
        pos, country = None, ""
        if len(toks) >= 2 and norm(toks[1]) in STATES:
            pos, country = gaz.us_city(toks[0], STATES[norm(toks[1])]), "US"
        elif len(toks) >= 2 and norm(toks[1]) in gaz.countries:
            country = gaz.countries[norm(toks[1])]
            pos = gaz.city(toks[0], country)
        elif toks and norm(toks[0]) in STATES:
            country = "US"
        elif toks and norm(toks[0]) in gaz.countries:
            country = gaz.countries[norm(toks[0])]
        stats["mapped" if pos else "not mapped"] += 1
        rows.append({"ds": "bluebook", "y": int(m[2]), "m": MONTHS.get((m[1] or "").lower(), 0), "d": 0,
                     "lat": pos[0] if pos else None, "lon": pos[1] if pos else None, "place": loc, "country": country,
                     "shape": "unknown", "sum": "", "ref": str(r["naId"])})
    print("  bluebook:", dict(stats))
    return rows


def load_geipan():
    import openpyxl
    ws = openpyxl.load_workbook(RAW / "geipan/cas.xlsx", read_only=True).active
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    rows = []
    for vals in it:
        r = dict(zip(hdr, vals))
        dm = re.match(r"(\d\d|--)/(\d\d|--)/(\d{4})", str(r.get("Observation date") or ""))
        year = int(dm[3]) if dm else int(r["Année"]) if str(r.get("Année") or "").isdigit() else None
        if not year:
            continue
        try:
            lat, lon = float(r["Latitude"]), float(r["Longitude"])
        except (TypeError, ValueError):
            lat = lon = None
        text = " ".join(str(r.get(k) or "") for k in ("Identification", "Détails"))
        title = re.sub(r"\s*\(\d+\w*\)\s*[\d.\-]+\s*$", "", str(r.get("Titre du Cas") or "")).title()
        rows.append({"ds": "geipan", "y": year, "m": int(dm[2]) if dm and dm[2].isdigit() else 0, "d": int(dm[1]) if dm and dm[1].isdigit() else 0,
                     "lat": lat, "lon": lon, "place": ", ".join(x for x in (title, str(r.get("Department") or "")) if x), "country": "FR",
                     "shape": shape_from_text(text), "cls": str(r.get("Classification") or "")[:2],
                     "sum": clean(f"[GEIPAN class {r.get('Classification')}] " + str(r.get("Identification") or r.get("Détails") or "")),
                     "ref": str(r["ID Etude de Cas"])})
    return rows


def load_moduk(gaz):
    import pdfplumber
    rows, stats = [], Counter()
    urls = dict(l.split("\t") for l in (RAW / "moduk/urls.txt").read_text().split("\n") if l.strip())
    for pdf_path in sorted((RAW / "moduk").glob("*.pdf")):
        year = int(pdf_path.stem)
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    for row in table:
                        cells = [(c or "").replace("\n", " ").strip() for c in row]
                        if len(cells) < 6 or cells[0].lower().startswith("date") or not cells[-1]:
                            continue
                        dm = re.match(r"(?:(\d{1,2})[- ])?([A-Za-z]{3})[a-z]*[- ](\d{2,4})", cells[0])
                        mon = MON3.get(dm[2].lower(), 0) if dm else 0
                        day = int(dm[1]) if dm and dm[1] else 0
                        town, area = cells[2], cells[3]
                        pos = None
                        if town and town.lower() not in ("not given", "unknown", ""):
                            pos = gaz.city(re.split(r"[/,(]| nr | near ", town, flags=re.I)[0].replace("Nr ", "").strip(), "GB")
                        stats["mapped" if pos else "not mapped"] += 1
                        occ = cells[4] if cells[4] and "where" not in cells[4].lower() else ""
                        rows.append({"ds": "moduk", "y": year, "m": mon, "d": day, "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
                                     "place": ", ".join(x for x in (town, area) if x and x.lower() != "not given"), "country": "GB",
                                     "shape": shape_from_text(cells[-1]), "sum": clean((f"[{occ}] " if occ else "") + cells[-1]),
                                     "ref": urls.get(str(year), "")})
    print("  moduk:", dict(stats))
    return rows


BEARINGS = {b: i * 22.5 for i, b in enumerate("N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}


def offset(lat, lon, nm, bearing):
    """Move nm nautical miles from (lat, lon) along a compass bearing."""
    import math
    d, b = nm / 3440.065, math.radians(bearing)
    la, lo = math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(b))
    lo2 = lo + math.atan2(math.sin(b) * math.sin(d) * math.cos(la), math.cos(d) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)


def load_faa():
    """Two report templates: Washington Operations Center notices (with
    coordinates) and Skywatch incident reports (position given as a distance
    and bearing from a three-letter airport or navaid identifier)."""
    import airportsdata
    iata = airportsdata.load("IATA")
    rows, stats = [], Counter()
    for line in open(RAW / "nara/493468575.jsonl", encoding="utf8"):
        r = json.loads(line)
        t = r["text"]
        dm = re.search(r"Date:\s*(?:\d{1,2}:\d{2}\s+)?(\d{1,2})/(\d{1,2})/(\d{4})", t)
        if not dm:
            stats["no date"] += 1
            continue
        lat = lon = None
        place = ""
        if "REMARKS" in t:
            desc = t.split("REMARKS", 1)[1].split("SKYWATCH INCIDENT REPORT")[0]
            call = re.search(r"Callsign:\s*(\S+)", t)
            ac = re.search(r"Aircraft:\s*(\S+)", t)
            fix = re.search(r"(\d+)\s*NM\s*([NSEW]{1,3})\s*of\s*([A-Z]{3})\b", desc) or re.search(r"()()(?:over|at|near|of)\s+([A-Z]{3})\b", desc)
            if fix and fix[3] in iata and iata[fix[3]]["country"] in ("US", "CA", "MX", "PR", "BS"):
                a = iata[fix[3]]
                lat, lon = offset(a["lat"], a["lon"], float(fix[1]), BEARINGS.get(fix[2], 0)) if fix[1] else (a["lat"], a["lon"])
                place = f"{fix[1]} NM {fix[2]} of {fix[3]} ({a['city']}, {a['subd']})" if fix[1] else f"near {fix[3]} ({a['city']}, {a['subd']})"
            desc = (f"[{call[1]} {ac[1] if ac else ''}] " if call else "") + desc
        else:
            co = re.findall(r"L(?:at|ong)itude:\s*(-?\d+\.\d+)", t)
            if len(co) >= 2:
                lat, lon = float(co[0]), float(co[1])
            desc = t.split("DESCRIPTION", 1)[-1]
            desc = re.sub(r"^\s*PRELIM INFO FROM FAA OPS:\s*", "", desc.strip())
            pm = re.match(r"([A-Z .'\-]+),\s*([A-Z]{2})\s*/", desc)
            place = f"{pm[1].title()}, {pm[2]}" if pm else ""
        if lat is not None and (abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0)):
            lat = lon = None
        stats["mapped" if lat is not None else "not mapped"] += 1
        rows.append({"ds": "faa", "y": int(dm[3]), "m": int(dm[1]), "d": int(dm[2]), "lat": lat, "lon": lon,
                     "place": place, "country": "US", "shape": shape_from_text(desc), "sum": clean(desc, 420), "ref": str(r["naId"])})
    print("  faa:", dict(stats))
    return rows


def main():
    print("loading datasets")
    nuforc = load_nuforc()
    gaz = Gazetteer(nuforc)
    rows = nuforc + load_bluebook(gaz) + load_geipan() + load_moduk(gaz) + load_faa()
    rows = [r for r in rows if 1900 <= r["y"] <= 2026]
    rows.sort(key=lambda r: (r["y"], r["m"], r["d"], r["ds"]))
    counts = Counter(r["ds"] for r in rows)
    mapped = Counter(r["ds"] for r in rows if r["lat"] is not None)
    for d in DATASETS:
        d["n"], d["mapped"] = counts[d["id"]], mapped[d["id"]]
        print(f"  {d['id']:9s} {d['n']:6d} reports, {d['mapped']:6d} mapped")
    cols = {"ds": [DS[r["ds"]] for r in rows], "y": [r["y"] for r in rows], "m": [r["m"] for r in rows],
            "lat": [None if r["lat"] is None else round(r["lat"], 2) for r in rows],
            "lon": [None if r["lon"] is None else round(r["lon"], 2) for r in rows],
            "sh": [SH[r["shape"]] for r in rows]}
    UI.mkdir(exist_ok=True)
    (UI / "reports.json").write_text(json.dumps({"datasets": DATASETS, "shapes": SHAPES, "chunk": CHUNK, "n": len(rows), **cols}, separators=(",", ":")))
    rep = UI / "rep"
    rep.mkdir(exist_ok=True)
    for old in rep.glob("*.json"):
        old.unlink()
    for i in range(0, len(rows), CHUNK):
        part = [[r["place"], r["sum"], r["ref"], r["d"]] for r in rows[i:i + CHUNK]]
        (rep / f"{i // CHUNK}.json").write_text(json.dumps(part, ensure_ascii=False, separators=(",", ":")))
    size = (UI / "reports.json").stat().st_size
    print(f"{len(rows)} reports -> ui/reports.json ({size // 1024} KB) + {len(list(rep.glob('*.json')))} detail chunks "
          f"({sum(f.stat().st_size for f in rep.glob('*.json')) // 1024} KB)")


if __name__ == "__main__":
    main()
