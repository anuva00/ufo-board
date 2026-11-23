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
    {"id": "hatch", "name": "Larry Hatch *U* database", "who": "Researcher's catalogue of reports from books, periodicals and files, mostly 1940s to 1990s; via the UAPDrop open dataset (CC BY 4.0)",
     "url": "https://www.uapdrop.com/data.html", "gov": False},
    {"id": "cadors", "name": "Transport Canada CADORS reports", "who": "Pilot and air-traffic-control reports filed as 'CIRVIS/UFO' in Canada's civil aviation occurrence system, 2000 onwards (Open Government Licence – Canada)",
     "url": "https://open.canada.ca/data/en/dataset/a348c1d1-2392-4595-b5e2-c6a244a7e87f", "gov": True},
    {"id": "pursue", "name": "PURSUE release incidents", "who": "Incidents described in the 2026 US government PURSUE release documents; dates, places and summaries are machine-read by uapinsight.com (CC BY 4.0)",
     "url": "https://uapinsight.com/pursue/data", "gov": True},
]
# which colour group each dataset is drawn in on the dashboard: 0 civilian, 1 US government, 2 other governments
CLASS = {"nuforc": 0, "hatch": 0, "bluebook": 1, "faa": 1, "pursue": 1, "geipan": 2, "moduk": 2, "cadors": 2}
for d in DATASETS:
    d["cls"] = CLASS[d["id"]]
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


NUMWORD = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
           "couple": 2, "both": 2, "several": 3, "many": 3, "multiple": 3, "numerous": 3, "dozen": 12, "dozens": 24, "hundreds": 99, "thousands": 99}
WHO = r"(?:other\s+|more\s+|independent\s+|separate\s+|adult\s+)?(?:witness(?:es)?|people|persons|observers|of us|adults|friends|family members|officers|police officers|pilots|neighbou?rs|co-?workers|students|children|kids|men|women|residents|motorists|drivers|passengers|crew members|airmen|soldiers|callers|reports from)"
W_NUM = re.compile(r"\b(\d{1,4}|" + "|".join(NUMWORD) + r")\s+(?:of\s+)?" + WHO + r"\b", re.I)
W_PAIR = re.compile(r"\b(?:my|me and my|i and my)\s+(?:wife|husband|friend|son|daughter|brother|sister|mother|father|mom|dad|girlfriend|boyfriend|partner|fiance|neighbou?r|cousin|co-?worker)\b|\b(?:wife|husband|friend|son|daughter|brother|sister|girlfriend|boyfriend) and (?:i|me)\b", re.I)


def witnesses(text):
    """Number of witnesses a summary states, or 0 when it does not say.
    Vague words ("several", "many") count as 3; "my wife and I" as 2."""
    best = 0
    for m in W_NUM.finditer(text or ""):
        v = m[1].lower()
        n = int(v) if v.isdigit() else NUMWORD[v]
        if re.search(r"of us|other|more", m[0], re.I) and not re.search(r"of us", m[0], re.I):
            n += 1                                   # "two other people" includes the reporter
        best = max(best, min(n, 99))
    if not best and W_PAIR.search(text or ""):
        best = 2
    return best


def clean(text, n=260):
    t = html.unescape(html.unescape(text or "")).replace("&#44", ",").replace("\ufffd", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


ABBR = {"ala": "al", "alas": "ak", "ariz": "az", "ark": "ar", "cal": "ca", "calif": "ca", "colo": "co", "col": "co", "conn": "ct", "del": "de",
        "d c": "dc", "fla": "fl", "ida": "id", "ill": "il", "ills": "il", "ind": "in", "kan": "ks", "kans": "ks", "ken": "ky", "mass": "ma",
        "mich": "mi", "minn": "mn", "miss": "ms", "mont": "mt", "neb": "ne", "nebr": "ne", "nev": "nv", "n h": "nh", "n j": "nj",
        "n m": "nm", "n mex": "nm", "new mex": "nm", "n y": "ny", "n c": "nc", "n car": "nc", "n d": "nd", "n dak": "nd", "okla": "ok",
        "ore": "or", "oreg": "or", "penn": "pa", "penna": "pa", "r i": "ri", "s c": "sc", "s car": "sc", "s d": "sd", "s dak": "sd",
        "tenn": "tn", "tex": "tx", "wash": "wa", "w va": "wv", "wis": "wi", "wisc": "wi", "wyo": "wy", "t h": "hi"}
STATE_OF = {**STATES, **ABBR, **{v: v for v in STATES.values()}}
BASE_WORDS = re.compile(r"\b(afb|afs|air force base|air force station|air base|ab|aaf|nas|naas|mcas|rcaf station|raf station|raf|airport|municipal airport|"
                        r"field|fld|airfield|army air field|station|sta|base|radar site|range|proving ground|test site|arsenal|depot)\b", re.I)
LEAD = re.compile(r"^(?:(?:approx(?:imately)?|about|appx)\.?\s*)?(?:\d+(?:\.\d+)?\s*(?:mi(?:les?)?|nm|n\.m\.|km|naut(?:ical)? mi(?:les)?)\.?\s*)?"
                  r"(?:(?:n|s|e|w|ne|nw|se|sw|nne|nnw|ene|ese|sse|ssw|wsw|wnw|north|south|east|west|northeast|northwest|southeast|southwest|"
                  r"near|nr|vicinity|vic|off|over|between|outside|above|area)\.?\s+(?:of\s+)?)+", re.I)
# "60.37N 11.02W", "16-32N 119-31 E" (degrees and minutes with a separator) or "1834N 14257E" (packed)
COORD = re.compile(r"\b(?:(\d{1,2})[.\-\s](\d{1,2})|(\d{2})(\d{2}))\s*([NS])[\s,/-]+(?:(\d{1,3})[.\-\s](\d{1,2})|(\d{2,3})(\d{2}))\s*([EW])\b", re.I)

# Military airfields named in Blue Book titles, by name without "AFB"/"Field". Hand-entered, to about 0.05 degrees.
BASES = {k: v for v, ks in {
    (30.41, -88.92): "keesler", (32.22, -101.52): "webb", (40.29, -88.14): "chanute", (32.85, -106.10): "holloman", (64.84, -147.61): "ladd",
    (38.95, -95.66): "forbes", (42.19, -72.53): "westover", (32.77, -97.44): "carswell", (32.38, -86.36): "maxwell", (47.58, -52.69): "pepperrell",
    (29.38, -98.58): "lackland|kelly", (61.25, -149.80): "elmendorf|elmendori", (35.41, -97.39): "tinker", (53.32, -60.42): "goose|goose bay",
    (29.61, -95.16): "ellington", (33.54, -112.38): "luke", (37.08, -76.36): "langley", (30.07, -85.58): "tyndall|tyndal", (38.54, -89.85): "scott",
    (42.61, -82.83): "selfridge", (38.73, -93.55): "whiteman", (42.72, -76.88): "sampson", (76.53, -68.70): "thule", (27.85, -82.52): "macdill",
    (34.59, -117.38): "george", (47.62, -117.65): "fairchild", (37.91, -85.97): "godman", (38.84, -94.56): "richards gebaur", (13.58, 144.93): "andersen|anderson",
    (32.64, -83.59): "robins", (39.67, -119.88): "stead", (41.50, -74.10): "stewart", (39.72, -104.89): "lowry", (33.92, -84.52): "dobbins",
    (47.21, -119.32): "larson", (35.05, -106.55): "sandia|kirtland", (21.33, -157.94): "hickam", (38.83, -104.80): "ent", (31.33, -92.55): "england",
    (32.36, -64.68): "kindley", (37.38, -120.57): "castle", (43.14, -89.34): "truax", (39.70, -104.75): "buckley", (39.83, -84.05): "wright patterson|wright|patterson",
    (39.81, -82.93): "lockbourne", (34.91, -117.88): "edwards|muroc", (34.74, -120.57): "vandenberg", (36.24, -115.03): "nellis", (33.88, -117.26): "march",
    (38.26, -121.93): "travis", (38.06, -122.51): "hamilton", (47.14, -122.48): "mcchord", (41.12, -95.91): "offutt", (38.81, -76.87): "andrews",
    (38.84, -77.02): "bolling", (30.48, -86.52): "eglin", (28.24, -80.61): "patrick", (40.02, -74.59): "mcguire", (39.13, -75.47): "dover",
    (43.23, -75.41): "griffiss", (46.95, -67.89): "loring", (43.08, -70.82): "pease", (41.66, -70.52): "otis", (42.47, -71.29): "hanscom",
    (44.65, -73.47): "plattsburgh", (48.42, -101.36): "minot", (47.96, -97.40): "grand forks", (44.15, -103.10): "ellsworth", (47.50, -111.19): "malmstrom",
    (43.04, -115.87): "mountain home", (41.12, -111.97): "hill", (32.17, -110.88): "davis monthan", (33.31, -111.66): "williams", (33.30, -104.53): "walker",
    (34.38, -103.32): "cannon", (33.60, -102.04): "reese", (33.99, -98.49): "sheppard", (32.42, -99.85): "dyess", (29.53, -98.28): "randolph",
    (30.19, -97.67): "bergstrom", (32.50, -93.66): "barksdale", (34.67, -99.27): "altus", (36.34, -97.92): "vance", (37.62, -97.27): "mcconnell",
    (38.79, -97.65): "schilling|smoky hill", (46.35, -87.39): "k i sawyer|sawyer", (46.25, -84.47): "kincheloe|kinross", (44.45, -83.39): "wurtsmith",
    (40.65, -86.15): "bunker hill|grissom", (35.17, -79.01): "pope", (35.34, -77.96): "seymour johnson", (30.97, -83.19): "moody", (32.01, -81.15): "hunter",
    (31.60, -84.09): "turner", (25.49, -80.38): "homestead", (28.43, -81.31): "mccoy|pinecastle", (32.34, -86.99): "craig", (35.96, -89.94): "blytheville",
    (39.14, -121.44): "beale", (38.55, -121.30): "mather", (38.67, -121.40): "mcclellan", (34.10, -117.23): "norton", (47.91, -122.28): "paine",
    (47.62, -117.53): "geiger", (42.16, -121.73): "kingsley", (64.67, -147.10): "eielson", (52.71, 174.11): "shemya|shamya", (15.19, 120.56): "clark",
    (26.36, 127.77): "kadena", (40.70, 141.37): "misawa", (35.75, 139.35): "yokota", (35.84, 139.41): "johnson", (33.59, 130.45): "itazuke",
    (37.09, 127.03): "osan", (37.56, 126.79): "kimpo", (32.89, 13.28): "wheelus", (37.00, 35.43): "incirlik", (49.44, 7.60): "ramstein",
    (50.03, 8.57): "rhein main", (49.95, 6.57): "bitburg", (52.41, 0.56): "lakenheath", (52.13, 1.43): "bentwaters", (63.99, -22.61): "keflavik",
    (48.54, -58.55): "ernest harmon|harmon", (67.01, -50.71): "sondrestrom", (18.49, -67.13): "ramey", (8.97, -79.56): "albrook", (8.91, -79.60): "howard",
}.items() for k in ks.split("|")}
# Small islands and common spellings that name a spot precisely enough to map.
PLACES = {"long island": (40.8, -73.1), "hawaiian islands": (21.45, -158.0), "oahu": (21.45, -158.0), "guam": (13.45, 144.78), "bermuda": (32.30, -64.78),
          "okinawa": (26.5, 127.9), "wake island": (19.28, 166.65), "midway island": (28.21, -177.38), "st lawrence island": (63.4, -170.3),
          "waikiki beach": (21.28, -157.83), "tuscon": (32.22, -110.97), "goose bay": (53.32, -60.42), "puerto rico": (18.2, -66.5),
          "trinidad": (10.5, -61.3), "kwajalein": (8.72, 167.73), "johnston island": (16.73, -169.53), "iwo jima": (24.78, 141.32)}


class Gazetteer:
    """Town -> coordinates, from (in order of preference) the GeoNames towns in
    the cities.json npm package, US post-office names, geonamescache, and the
    medians of the NUFORC geocoded reports."""

    def __init__(self, nuforc_rows):
        import difflib, geonamescache, zipcodes
        self.difflib = difflib
        self.us, self.world = {}, defaultdict(list)
        towns = json.loads((RAW / "npm/node_modules/cities.json/cities.json").read_text())
        gc = geonamescache.GeonamesCache()
        big = sorted(gc.get_cities().values(), key=lambda c: -c["population"])
        for c in big:                                   # largest first, so an ambiguous name resolves to the biggest town
            self.world[norm(c["name"])].append((c["countrycode"], c["latitude"], c["longitude"]))
            if c["countrycode"] == "US" and c.get("admin1code"):
                self.us.setdefault((norm(c["name"]), c["admin1code"].lower()), (c["latitude"], c["longitude"]))
        for c in towns:
            lat, lon = float(c["lat"]), float(c["lng"])
            self.world[norm(c["name"])].append((c["country"], lat, lon))
            if c["country"] == "US":
                self.us.setdefault((norm(c["name"]), c["admin1"].lower()), (lat, lon))
        zips = defaultdict(list)
        for z in zipcodes.list_all():
            if z.get("lat") and z.get("long"):
                zips[(norm(z["city"]), z["state"].lower())].append((float(z["lat"]), float(z["long"])))
        med = lambda pts: (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
        for k, v in zips.items():
            self.us.setdefault(k, med(v))
        nu = defaultdict(list)
        for r in nuforc_rows:
            if r["country"] == "us" and r["state"] and r["lat"] is not None:
                nu[(norm(re.sub(r"\(.*?\)", "", r["city"])), r["state"])].append((r["lat"], r["lon"]))
        for k, v in nu.items():
            self.us.setdefault(k, med(v))
        self.by_state = defaultdict(list)
        for name, st in self.us:
            self.by_state[st].append(name)
        self.countries = {norm(c["name"]): c["iso"] for c in gc.get_countries().values()}
        self.countries.update({"england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB", "great britain": "GB", "uk": "GB",
                               "korea": "KR", "north korea": "KP", "south korea": "KR", "west germany": "DE", "germany": "DE",
                               "french morocco": "MA", "formosa": "TW", "soviet union": "RU", "ussr": "RU", "okinawa": "JP",
                               "newfoundland": "CA", "labrador": "CA", "canal zone": "PA", "philippine islands": "PH", "persia": "IR",
                               "azores": "PT", "holland": "NL", "french indochina": "VN", "indochina": "VN", "iran": "IR"})

    def us_city(self, city, state, fuzzy=False):
        n = norm(city)
        variants = [n, norm(BASE_WORDS.sub("", city)), n.replace("ft ", "fort ").replace("st ", "saint ").replace("mt ", "mount ")]
        for v in variants:
            if v and (v, state) in self.us:
                return self.us[(v, state)]
        if fuzzy and len(n) >= 5:
            m = self.difflib.get_close_matches(n, self.by_state.get(state, []), n=1, cutoff=0.86)
            if m:
                return self.us[(m[0], state)]
        return None

    def city(self, name, iso=None):
        for v in (norm(name), norm(BASE_WORDS.sub("", name))):
            for c in self.world.get(v, []):
                if iso is None or c[0] == iso:
                    return c[1], c[2]
        return None

    def locate(self, loc):
        """Best-effort position for a free-text place such as a Blue Book title.
        Returns (lat, lon, country) with lat/lon None when only the country or state is known."""
        m = COORD.search(loc)
        if m:
            la, lam, lo, lom = (m[1], m[2], m[6], m[7]) if m[1] else (m[3], m[4], m[8], m[9])
            if not m[1] or not m[6]:                    # mixed forms are too ambiguous to trust
                la, lam, lo, lom = (m[1] or m[3], m[2] or m[4], m[6] or m[8], m[7] or m[9])
            lat, lon = int(la) + min(int(lam), 59) / 60, int(lo) + min(int(lom), 59) / 60
            if lat <= 90 and lon <= 180:
                return (lat if m[5].upper() == "N" else -lat, lon if m[10].upper() == "E" else -lon, "")
        for t in loc.split(","):                        # a named air base or small island anywhere in the title
            t = LEAD.sub("", re.sub(r"\(.*?\)|\[.*?\]", "", t)).strip(" .")
            n = norm(BASE_WORDS.sub("", t))
            if norm(t) in PLACES:
                return (*PLACES[norm(t)], "")
            if BASE_WORDS.search(t) and n in BASES:
                return (*BASES[n], "")
        toks = [LEAD.sub("", re.sub(r"\(.*?\)|\[.*?\]", "", t)).strip(" .") for t in loc.split(",")]
        toks = [t for t in toks if t]
        if not toks:
            return None, None, ""
        if len(toks) == 1:                              # "THE DALLES OREGON", "Lima Peru"
            words = toks[0].split()
            for n in (2, 1):
                tail = norm(" ".join(words[-n:]))
                if len(words) > n and (tail in STATE_OF or tail in self.countries):
                    toks = [" ".join(words[:-n]), " ".join(words[-n:])]
                    break
        last = norm(toks[-1])
        if last in STATE_OF and len(toks) >= 2:
            st = STATE_OF[last]
            for fuzzy in (False, True):
                for t in toks[:-1]:
                    pos = self.us_city(t, st, fuzzy)
                    if pos:
                        return pos[0], pos[1], "US"
            return None, None, "US"
        if last in self.countries and len(toks) >= 2:
            iso = self.countries[last]
            for t in toks[:-1]:
                pos = self.city(t, iso)
                if pos:
                    return pos[0], pos[1], iso
            return None, None, iso
        if last in STATE_OF:
            return None, None, "US"
        return None, None, self.countries.get(last, "")


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
                         "shape": NUFORC_SHAPE.get(r[4].strip().lower(), "unknown"), "sum": clean(r[7]), "ref": "",
                         "w": witnesses(clean(r[7], 2000))})
    return rows


def load_bluebook(gaz):
    """One row per case file in the National Archives series. The title gives
    place and month ("Dayton, Ohio, November 1969"); early files add an
    incident number, and a few hundred have an illegible or blank date, for
    which the catalogue's own coverage date is used when it has one."""
    rows, stats = [], Counter()
    for line in open(RAW / "nara/597821.jsonl", encoding="utf8"):
        r = json.loads(line)
        title = r["title"] or ""
        inc = re.search(r"\s*-\s*Incident Number:\s*(.*)$", title, re.I)
        if inc:
            title = title[: inc.start()]
        m = re.search(r",?\s*(?:(January|February|March|April|May|June|July|August|September|October|November|December)\s+)?((?:19)\d{2})\s*$", title)
        if m:
            year, month, loc = int(m[2]), MONTHS.get((m[1] or "").lower(), 0), title[: m.start()]
        else:
            d = re.match(r"(\d{4})-(\d\d)", r.get("date_start") or "")
            if not d:
                stats["undated, left out"] += 1
                continue
            year, month = int(d[1]), int(d[2]) if (r.get("date_end") or "")[:7] == r["date_start"][:7] else 0
            loc = re.sub(r",?\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)?\s*(?:\[\w+\]\s*)+$", "", title)
            stats["dated from the catalogue"] += 1
        loc = loc.strip(" ,")
        lat, lon, country = gaz.locate(loc)
        stats["mapped" if lat is not None else "not mapped"] += 1
        note = f"Incident number {inc[1].strip()}." if inc and re.match(r"\d", inc[1].strip()) else ""
        rows.append({"ds": "bluebook", "y": year, "m": month, "d": 0, "lat": lat, "lon": lon, "place": loc.title() if loc.isupper() else loc,
                     "country": country, "shape": "unknown", "sum": note, "ref": str(r["naId"])})
    print("  bluebook:", dict(stats))
    return rows


def load_geipan():
    import openpyxl
    ws = openpyxl.load_workbook(RAW / "geipan/cas.xlsx", read_only=True).active
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    rows = []
    tem = Counter()                                  # testimonies on file per case: a lower bound on witnesses
    tpath = RAW / "geipan/tem.xlsx"
    if tpath.exists():
        tw = openpyxl.load_workbook(tpath, read_only=True).active
        for i, v in enumerate(tw.iter_rows(values_only=True)):
            if i and v[0]:
                tem[re.sub(r"\s+T\d+\s*$", "", str(v[0])).strip()] += 1
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
                     "ref": str(r["ID Etude de Cas"]), "w": min(99, tem.get(str(r.get("Titre du Cas") or "").strip(), 0))})
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
                            pos = gaz.city(LEAD.sub("", re.split(r"[/,(&]| nr | near | and ", town, flags=re.I)[0]).strip(), "GB")
                        stats["mapped" if pos else "not mapped"] += 1
                        occ = cells[4] if cells[4] and "where" not in cells[4].lower() else ""
                        rows.append({"ds": "moduk", "y": year, "m": mon, "d": day, "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
                                     "place": ", ".join(x for x in (town, area) if x and x.lower() != "not given"), "country": "GB",
                                     "shape": shape_from_text(cells[-1]), "w": witnesses(cells[-1]), "sum": clean((f"[{occ}] " if occ else "") + cells[-1]),
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


def load_hatch():
    rows = []
    for r in csv.DictReader(open(RAW / "uapdrop/sightings.csv", encoding="utf8", errors="replace")):
        if r["source_key"] != "hatch":
            continue                                 # UAPDrop's Blue Book and GEIPAN rows duplicate our own copies
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)", r["observed_at"] or "")
        if not m:
            continue
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except ValueError:
            lat = lon = None
        text = r["summary"] or ""
        cite = r["source_url"].strip()
        rows.append({"ds": "hatch", "y": int(m[1]), "m": int(m[2]), "d": int(m[3]), "lat": lat, "lon": lon,
                     "place": r["location_name"].title() if r["location_name"].isupper() else r["location_name"], "country": r["country_code"],
                     "shape": shape_from_text(text), "w": witnesses(text),
                     "sum": clean(text + (f" — Source cited by Hatch: {cite}" if cite else ""), 420), "ref": "https://www.uapdrop.com/data.html"})
    return rows


CADORS_COORD = re.compile(r"(\d{2})(\d{2}(?:\.\d+)?)\s*N[\s/,]*(\d{2,3})(\d{2}(?:\.\d+)?)\s*W", re.I)


def load_cadors(gaz):
    import airportsdata
    icao = airportsdata.load("ICAO")
    ids = {r["cadorsnumber"] for r in csv.DictReader(open(RAW / "cadors/occurrence_events.csv", encoding="utf8", errors="replace"))
           if "CIRVIS/UFO" in (r["event_name_enm"] or "")}
    rows, stats = [], Counter()
    for r in csv.DictReader(open(RAW / "cadors/occurrence_information.csv", encoding="utf8", errors="replace")):
        if r["cadorsnumber"] not in ids:
            continue
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)", r["occurrencedate"] or "")
        if not m:
            continue
        loc, aero = (r["occurrencelocation"] or "").strip(), (r["aerodromelocation"] or "").strip()
        lat = lon = None
        c = CADORS_COORD.search(loc)
        c2 = re.search(r"\b(\d{2})(\d{2})\s*/\s*(\d{3})(\d{2})\b", loc)          # "5423/11958"
        c3 = re.search(r"\b(\d{2})\s*N\s*/?\s*(\d{2,3})\s*W\b", loc, re.I)       # "58N 111W"
        if c:
            lat, lon = int(c[1]) + float(c[2]) / 60, -(int(c[3]) + float(c[4]) / 60)
        elif c2:
            lat, lon = int(c2[1]) + int(c2[2]) / 60, -(int(c2[3]) + int(c2[4]) / 60)
        elif c3:
            lat, lon = float(c3[1]), -float(c3[2])
        else:
            a = re.search(r"\(([A-Z0-9]{4})\)", loc) or re.search(r"\(([A-Z0-9]{4})\)", aero)
            if a and a[1] in icao:
                lat, lon = icao[a[1]]["lat"], icao[a[1]]["lon"]
            elif loc:
                pos = gaz.city(re.split(r"[,(]", loc)[0].strip(), "CA")
                if pos:
                    lat, lon = pos
        stats["mapped" if lat is not None else "not mapped"] += 1
        where = "; ".join(x for x in (f"location given as “{loc}”" if loc else "", f"aerodrome {aero}" if aero else "") if x)
        rows.append({"ds": "cadors", "y": int(m[1]), "m": int(m[2]), "d": int(m[3]), "lat": lat, "lon": lon,
                     "place": loc if loc and not CADORS_COORD.search(loc) else (aero or r["subdivision_enm"] or ""), "country": "CA",
                     "shape": "unknown", "w": 0,
                     "sum": clean(f"CADORS {r['cadorsnumber']}: filed under 'Weather balloon, meteor, rocket, CIRVIS/UFO'"
                                  f"{', ' + r['occurrencetime'] if r['occurrencetime'] else ''}. {where}. The open data has no narrative; "
                                  f"search the CADORS number on Transport Canada's CADORS site for the full report.", 420),
                     "ref": "https://open.canada.ca/data/en/dataset/a348c1d1-2392-4595-b5e2-c6a244a7e87f"})
    print("  cadors:", dict(stats))
    return rows


MONTH_WORD = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)", re.I)
PURSUE_SHAPE = {"disc": "disc", "disk": "disc", "triangle": "triangle", "sphere": "sphere", "orb": "sphere", "cylinder": "cylinder",
                "tic-tac": "oval", "tic_tac": "oval", "oval": "oval", "light": "light", "lights": "light", "fireball": "fireball",
                "cube": "other", "diamond": "other", "rectangle": "other", "cigar": "cylinder", "boomerang": "boomerang", "chevron": "boomerang"}


def load_pursue(gaz):
    rows, stats = [], Counter()
    for r in csv.DictReader(open(RAW / "pursue/documents.csv", encoding="utf8", errors="replace")):
        if r["record_verdict"] == "not_incident":
            continue
        d = (r["incident_date"] or "").strip()
        y = m = day = 0
        a = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", d)
        if a:
            y = int(a[3]) + (0 if len(a[3]) == 4 else (1900 if int(a[3]) > 30 else 2000))
            m, day = int(a[1]), int(a[2])
        else:
            yy = re.search(r"\b(19[4-9]\d|20[0-2]\d)\b", d)
            if yy:
                y = int(yy[1])
                mw = MONTH_WORD.search(d)
                m = MONTHS[mw[1].lower()] if mw else 0
        if not y:
            stats["no date"] += 1
            continue
        where = re.sub(r",?\s*(U\.S\.A?\.?|USA)\s*$", "", r["incident_location"] or "").strip()
        lat, lon, country = gaz.locate(where) if where not in ("", "N/A") else (None, None, "")
        stats["mapped" if lat is not None else "not mapped"] += 1
        shape = PURSUE_SHAPE.get((r["uap_shape"] or "").lower().split("|")[0].strip(), "unknown")
        rows.append({"ds": "pursue", "y": y, "m": m, "d": day, "lat": lat, "lon": lon,
                     "place": r["incident_location"] if r["incident_location"] != "N/A" else "", "country": country or "US",
                     "shape": shape, "w": 0,
                     "sum": clean(f"{r['title']}. {r['summary']} (Machine-written summary by uapinsight.com; check the original.)", 520),
                     "ref": r["original_url"] or r["uapinsight_url"]})
    print("  pursue:", dict(stats))
    return rows


def main():
    print("loading datasets")
    nuforc = load_nuforc()
    gaz = Gazetteer(nuforc)
    rows = nuforc + load_bluebook(gaz) + load_geipan() + load_moduk(gaz) + load_faa()
    for name, fn in (("hatch", load_hatch), ("cadors", lambda: load_cadors(gaz)), ("pursue", lambda: load_pursue(gaz))):
        try:
            rows += fn()
        except FileNotFoundError:
            print(f"  {name}: not downloaded (run pipeline/fetch_extra.py)")
    rows = [r for r in rows if 1900 <= r["y"] <= 2026]
    rows.sort(key=lambda r: (r["y"], r["m"], r["d"], r["ds"]))
    counts = Counter(r["ds"] for r in rows)
    mapped = Counter(r["ds"] for r in rows if r["lat"] is not None)
    for d in DATASETS:
        d["n"], d["mapped"] = counts[d["id"]], mapped[d["id"]]
        d["w_known"] = sum(1 for r in rows if r["ds"] == d["id"] and r.get("w", 0) > 0)
        d["w3"] = sum(1 for r in rows if r["ds"] == d["id"] and r.get("w", 0) >= 3)
        print(f"  {d['id']:9s} {d['n']:6d} reports, {d['mapped']:6d} mapped, witnesses stated {d['w_known']:6d}, 3 or more {d['w3']:6d}")
    cols = {"ds": [DS[r["ds"]] for r in rows], "y": [r["y"] for r in rows], "m": [r["m"] for r in rows],
            "lat": [None if r["lat"] is None else round(r["lat"], 2) for r in rows],
            "lon": [None if r["lon"] is None else round(r["lon"], 2) for r in rows],
            "sh": [SH[r["shape"]] for r in rows], "w": [r.get("w", 0) for r in rows],
            # National Archives id of each Blue Book file, so the NAS can attach what it read from the scans
            "nara": [int(r["ref"]) if r["ds"] == "bluebook" and r["ref"].isdigit() else 0 for r in rows]}
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
