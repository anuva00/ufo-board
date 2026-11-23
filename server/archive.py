"""National Archives scans on the NAS: download every page image of a catalogue
series (for example the Project Blue Book case files), read it with
Tesseract OCR, pull the main fields off each Air Force record card, index the
text for full-text search, and find the names the board knows.

Stored in DATA_DIR:
  archive/<file id>/<page>.jp2   page images (kept unless KEEP_IMAGES=0)
  board.db                       files, pages, OCR text (full-text index), fields, name matches

Environment:
  OCR_WORKERS  3   pages read at once
  KEEP_IMAGES  1   keep the downloaded page images after OCR (about 0.8 MB a page)
"""
import difflib, io, json, os, re, shutil, subprocess, tempfile, threading, time, traceback, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CATALOG = "https://catalog.archives.gov/proxy/records/parentNaId/{naid}?page={page}&limit=100{extra}"
RECORD = "https://catalog.archives.gov/proxy/records/search?naId={naid}"
UA = {"User-Agent": "ufo-board/1.0 (personal research archive)"}
OCR_WORKERS = int(os.environ.get("OCR_WORKERS", "3"))
KEEP_IMAGES = os.environ.get("KEEP_IMAGES", "1") != "0"
PRESETS = {597821: "Project Blue Book case files (sanitized), US National Archives"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_source (naid INTEGER PRIMARY KEY, title TEXT, added REAL, paused INTEGER DEFAULT 0,
  listed INTEGER DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS archive_file (naid INTEGER PRIMARY KEY, series INTEGER, title TEXT, pages INTEGER, pos INTEGER,
  status TEXT DEFAULT 'waiting', fields TEXT, error TEXT, done_at REAL);
CREATE TABLE IF NOT EXISTS archive_page (naid INTEGER, n INTEGER, url TEXT, status TEXT DEFAULT 'waiting', conf REAL,
  PRIMARY KEY (naid, n));
CREATE VIRTUAL TABLE IF NOT EXISTS page_text USING fts5(text, naid UNINDEXED, n UNINDEXED, tokenize = 'porter unicode61');
CREATE TABLE IF NOT EXISTS archive_mention (naid INTEGER, eid INTEGER, n INTEGER, page INTEGER, snip TEXT, PRIMARY KEY (naid, eid));
CREATE INDEX IF NOT EXISTS archive_file_series ON archive_file(series, status, pos);
CREATE INDEX IF NOT EXISTS archive_mention_eid ON archive_mention(eid);
"""

APP = None                          # the main server module, set by start()
WAKE = threading.Event()
STATE = {"activity": "Idle"}


def get_json(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))


def series_title(naid):
    d = get_json(RECORD.format(naid=naid))
    hits = d["body"]["hits"]["hits"]
    if not hits:
        raise ValueError("The National Archives catalogue has no record with that number.")
    rec = hits[0]["_source"]["record"]
    if rec.get("levelOfDescription") not in ("series", "fileUnit", "collection", "recordGroup"):
        raise ValueError("That catalogue record is a single item; subscribe to the series or file unit that contains it.")
    return rec.get("title") or str(naid)


# ---------------------------------------------------------------- OCR and fields

def ocr_image(data):
    """(text, mean confidence) for one page. Two passes: plain, and a high-contrast
    threshold that recovers the faint typing on Air Force record cards; the
    better-scoring pass is kept."""
    from PIL import Image, ImageOps
    im = ImageOps.autocontrast(Image.open(io.BytesIO(data)).convert("L"), cutoff=1)
    best = None
    with tempfile.TemporaryDirectory() as tmp:
        passes = [("plain", im, "3"), ("faint", im.point(lambda v: 255 if v > 210 else 0), "6")]
        for i, (name, img, psm) in enumerate(passes):
            path = os.path.join(tmp, f"{name}.png")
            img.save(path)
            out = subprocess.run(["tesseract", path, "stdout", "--psm", psm, "-l", "eng", "tsv"],
                                 capture_output=True, text=True, timeout=300, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
            words, confs, lines, last = [], [], [], None
            for row in out.stdout.splitlines()[1:]:
                c = row.split("\t")
                if len(c) < 12 or not c[11].strip():
                    continue
                key = (c[2], c[3], c[4])               # block, paragraph, line
                if key != last:
                    lines.append([])
                    last = key
                lines[-1].append(c[11])
                try:
                    confs.append(float(c[10]))
                except ValueError:
                    pass
                words.append(c[11])
            text = "\n".join(" ".join(l) for l in lines)
            good = sum(1 for w, c in zip(words, confs) if c >= 70 and re.fullmatch(r"[A-Za-z]{3,}[.,;:]?", w))
            score = (good, sum(confs) / len(confs) if confs else 0)
            if best is None or score > best[0]:
                best = (score, text)
            if i == 0 and not CARD.search(text) and score[1] >= 65:
                break                                  # a clean typed page: no need for the second pass
    return best[1], round(best[0][1], 1)


CARD = re.compile(r"PROJECT\s*1\s*0\s*0\s*7\s*3|RECORD\s+CARD|C[O0]NCLUS[I1]ON", re.I)
CONCLUSIONS = {
    "unidentified": "Unidentified", "insufficient": "Insufficient data", "balloon": "Balloon", "aircraft": "Aircraft",
    "satellite": "Satellite", "decay": "Satellite", "astro": "Astronomical", "astronomical": "Astronomical", "meteor": "Astronomical",
    "star": "Astronomical", "planet": "Astronomical", "venus": "Astronomical", "jupiter": "Astronomical", "mars": "Astronomical",
    "moon": "Astronomical", "sun": "Astronomical", "fireball": "Astronomical", "comet": "Astronomical", "aurora": "Astronomical",
    "missile": "Missile or rocket", "rocket": "Missile or rocket", "hoax": "Hoax", "psychological": "Psychological",
    "birds": "Other", "searchlight": "Other", "reflection": "Other", "clouds": "Other", "kite": "Other", "flare": "Other",
    "chaff": "Other", "inversion": "Other", "mirage": "Other", "contrail": "Other", "radar": "Radar", "other": "Other",
    "unreliable": "Insufficient data", "data": "Insufficient data",
}
SOURCES = {"military": "Military", "civilian": "Civilian", "multiple": "Multiple"}
OBS = ["ground visual", "air visual", "ground radar", "air radar", "air intercept radar", "ground-visual", "air-visual"]
NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "several": 3, "many": 3}


def fuzzy(word, vocab, cutoff=0.8):
    m = difflib.get_close_matches(word.lower(), vocab, n=1, cutoff=cutoff)
    return m[0] if m else None


def card_fields(text):
    """Main fields of an Air Force record card ("Project 10073 Record"), read from
    noisy OCR text. Each is a best guess and may be wrong."""
    f = {}
    words = re.findall(r"[A-Za-z]+", text)
    low = [w.lower() for w in words]
    at = next((i for i, w in enumerate(low) if fuzzy(w, ["conclusion"], 0.75)), None)
    window = words[at + 1: at + 25] if at is not None else words
    qual, other = None, False
    for w in window:
        lw = w.lower()
        if fuzzy(lw, ["possible", "probable", "possibly", "probably"], 0.8):
            qual = qual or ("probable" if lw.startswith("prob") else "possible")
        if len(lw) < 4 or "conclusion" in f:
            continue
        hit = fuzzy(lw, list(CONCLUSIONS), 0.75 if len(lw) >= 7 else 0.8)
        if hit == "other":
            other = True
        elif hit and hit != "data":
            f["conclusion"] = CONCLUSIONS[hit]
            f["conclusion_word"] = w
    if "conclusion" not in f and other:
        f["conclusion"] = "Other"
    if "conclusion" not in f and any(fuzzy(w.lower(), ["insufficient"], 0.8) for w in window):
        f["conclusion"] = "Insufficient data"
    if "conclusion" in f and qual:
        f["qualifier"] = qual
    for w in words:
        hit = fuzzy(w, list(SOURCES), 0.8) if len(w) >= 6 else None
        if hit:
            f["source"] = SOURCES[hit]
            break
    flat = " ".join(low)
    for o in OBS:
        m = difflib.SequenceMatcher(None, flat, o).find_longest_match(0, len(flat), 0, len(o))
        if m.size >= len(o) - 2:
            f["observation"] = o.replace("-", " ").capitalize()
            break
    m = re.search(r"objects?\W+(?:\w+\W+){0,6}?(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|several|many)\b", flat)
    if m:
        f["objects"] = int(m[1]) if m[1].isdigit() else NUM[m[1]]
    m = re.search(r"\b(\d{1,3}|one|two|three|four|five|ten|fifteen|twenty|thirty)\s*(?:\d/\d\s*)?(sec|second|min|minute|hr|hour)s?\b", flat)
    if m:
        f["duration"] = m[0]
    w = APP.witnesses_in(text) if APP else 0
    if w:
        f["witnesses"] = w
    return f


# ---------------------------------------------------------------- listing and processing

def list_series(naid):
    """Every file unit in a series with its page image URLs. The catalogue stops
    paging at 10,000 records, so a large series is read in both title orders."""
    seen, total = set(), None
    for extra in ("", "&sort=title:asc", "&sort=title:desc"):
        page = 1
        while page <= 100:
            STATE["activity"] = f"Listing catalogue records ({len(seen):,} so far)"
            d = get_json(CATALOG.format(naid=naid, page=page, extra=extra))
            hits = d["body"]["hits"]
            total = total or hits["total"]["value"]
            rows = []
            for hh in hits["hits"]:
                r = hh["_source"]["record"]
                objs = [o["objectUrl"] for o in r.get("digitalObjects") or [] if o.get("objectUrl")]
                if r["naId"] in seen:
                    continue
                seen.add(r["naId"])
                rows.append((r["naId"], r.get("title") or str(r["naId"]), objs))
            with APP.LOCK, APP.db() as con:
                base = con.execute("SELECT count(*) FROM archive_file WHERE series = ?", (naid,)).fetchone()[0]
                for i, (fid, title, objs) in enumerate(rows):
                    if con.execute("SELECT 1 FROM archive_file WHERE naid = ?", (fid,)).fetchone():
                        continue
                    con.execute("INSERT INTO archive_file (naid, series, title, pages, pos) VALUES (?,?,?,?,?)",
                                (fid, naid, title, len(objs), base + i))
                    con.executemany("INSERT OR IGNORE INTO archive_page (naid, n, url) VALUES (?,?,?)",
                                    [(fid, n + 1, u) for n, u in enumerate(objs)])
            if len(hits["hits"]) < 100:
                break
            page += 1
            time.sleep(1)
        if total is not None and (total < 10000 or len(seen) >= total):
            break
    with APP.LOCK, APP.db() as con:
        con.execute("UPDATE archive_source SET listed = 1, last_error = NULL WHERE naid = ?", (naid,))


def process_file(row, pool):
    fid = row["naid"]
    folder = APP.DATA / "archive" / str(fid)
    folder.mkdir(parents=True, exist_ok=True)
    pages = APP.db().execute("SELECT n, url FROM archive_page WHERE naid = ? AND status != 'done' ORDER BY n", (fid,)).fetchall()

    def one(p):
        path = folder / f"{p['n']}.jp2"
        if not path.exists():
            with urllib.request.urlopen(urllib.request.Request(p["url"], headers=UA), timeout=180) as r:
                data = r.read()
            path.write_bytes(data)
        else:
            data = path.read_bytes()
        text, conf = ocr_image(data)
        if not KEEP_IMAGES:
            path.unlink(missing_ok=True)
        return p["n"], text, conf

    errors, err = 0, None
    for fut in [pool.submit(one, p) for p in pages]:
        try:
            n, text, conf = fut.result()
        except Exception as e:
            errors += 1
            err = f"{type(e).__name__}: {e}"[:300]
            continue
        with APP.LOCK, APP.db() as con:
            con.execute("DELETE FROM page_text WHERE naid = ? AND n = ?", (fid, n))
            con.execute("INSERT INTO page_text (text, naid, n) VALUES (?,?,?)", (text, fid, n))
            con.execute("UPDATE archive_page SET status = 'done', conf = ? WHERE naid = ? AND n = ?", (conf, fid, n))
    finish_file(fid, error=None if not errors else f"{errors} page(s) failed; will retry. Last error: {err}")
    if not KEEP_IMAGES:
        shutil.rmtree(folder, ignore_errors=True)


def finish_file(fid, error=None):
    con = APP.db()
    texts = con.execute("SELECT n, text FROM page_text WHERE naid = ? ORDER BY n", (fid,)).fetchall()
    fields = {}
    for r in texts:
        if CARD.search(r["text"]):
            fields = card_fields(r["text"])
            fields["card_page"] = r["n"]
            break
    match_file(fid, texts)
    left = con.execute("SELECT count(*) FROM archive_page WHERE naid = ? AND status != 'done'", (fid,)).fetchone()[0]
    with APP.LOCK, APP.db() as c:
        c.execute("UPDATE archive_file SET status = ?, fields = ?, error = ?, done_at = ? WHERE naid = ?",
                  ("done" if not left else "failed", json.dumps(fields), error, time.time(), fid))


def match_file(fid, texts=None):
    if texts is None:
        texts = APP.db().execute("SELECT n, text FROM page_text WHERE naid = ? ORDER BY n", (fid,)).fetchall()
    total = {}
    for r in texts:
        segs = [(r["n"], line) for line in r["text"].splitlines() if readable(line)]
        for eid, (n, first, snips) in APP.MATCHER.find(segs).items():
            t = total.setdefault(eid, [0, first, snips[0][1] if snips else ""])
            t[0] += n
    with APP.LOCK, APP.db() as con:
        con.execute("DELETE FROM archive_mention WHERE naid = ?", (fid,))
        con.executemany("INSERT INTO archive_mention VALUES (?,?,?,?,?)", [(fid, e, v[0], int(v[1]), v[2]) for e, v in total.items()])


def readable(line):
    """False for lines of scanner noise ("ee a ae a. Me eS"), where stray letters can spell an acronym."""
    t = re.findall(r"[A-Za-z0-9]+", line)
    if not t:
        return False
    good = sum(1 for w in t if len(w) >= 3 and (w.isdigit() or re.search(r"[aeiouyAEIOUY]", w)))
    return good >= 0.5 * len(t)


def rematch_all():
    fids = [r[0] for r in APP.db().execute("SELECT naid FROM archive_file WHERE status = 'done'")]
    for i, fid in enumerate(fids):
        if i % 200 == 0:
            STATE["activity"] = f"Re-matching names in archive files ({i:,} of {len(fids):,})"
        match_file(fid)


def worker():
    pool = ThreadPoolExecutor(OCR_WORKERS)
    while True:
        WAKE.clear()
        try:
            for s in APP.db().execute("SELECT * FROM archive_source WHERE paused = 0 AND listed = 0").fetchall():
                try:
                    list_series(s["naid"])
                except Exception as e:
                    with APP.LOCK, APP.db() as con:
                        con.execute("UPDATE archive_source SET last_error = ? WHERE naid = ?", (f"Listing failed: {e}"[:300], s["naid"]))
            while not WAKE.is_set():
                row = APP.db().execute("""SELECT f.* FROM archive_file f JOIN archive_source s ON s.naid = f.series
                                          WHERE s.paused = 0 AND (f.status = 'waiting' OR (f.status = 'failed' AND f.done_at < ?))
                                          ORDER BY f.status = 'failed', f.series, f.pos LIMIT 1""", (time.time() - 6 * 3600,)).fetchone()
                if not row:
                    break
                STATE["activity"] = f"Reading {row['title']} ({row['pages']} pages)"
                process_file(row, pool)
        except Exception:
            traceback.print_exc()
            time.sleep(60)
        STATE["activity"] = "Idle"
        WAKE.wait(timeout=900)


# ---------------------------------------------------------------- API

def summary():
    con = APP.db()
    out = []
    for s in con.execute("SELECT * FROM archive_source ORDER BY added"):
        c = con.execute("""SELECT count(*), sum(status = 'done'), sum(status = 'failed'), coalesce(sum(pages), 0) FROM archive_file WHERE series = ?""",
                        (s["naid"],)).fetchone()
        pages_done = con.execute("""SELECT count(*) FROM archive_page p JOIN archive_file f ON f.naid = p.naid
                                    WHERE f.series = ? AND p.status = 'done'""", (s["naid"],)).fetchone()[0]
        concl = {}
        for r in con.execute("SELECT fields FROM archive_file WHERE series = ? AND status = 'done'", (s["naid"],)):
            k = json.loads(r[0] or "{}").get("conclusion") or "Not read"
            concl[k] = concl.get(k, 0) + 1
        out.append({**dict(s), "files": c[0] or 0, "files_done": c[1] or 0, "files_failed": c[2] or 0, "pages": c[3], "pages_done": pages_done,
                    "conclusions": concl})
    folder = APP.DATA / "archive"
    used = sum(f.stat().st_size for f in folder.rglob("*.jp2")) if folder.exists() else 0
    return {"sources": out, "activity": STATE["activity"], "keep_images": KEEP_IMAGES, "disk_bytes": used,
            "workers": OCR_WORKERS, "presets": [{"naid": k, "title": v} for k, v in PRESETS.items()]}


def search(q, limit=60):
    q = q.strip()
    if not q:
        return []
    # each word must appear; quoted phrases are kept as typed
    terms = re.findall(r'"[^"]+"|\S+', q)
    fts = " ".join(t if t.startswith('"') else '"' + t.replace('"', "") + '"' for t in terms)
    rows = APP.db().execute("""SELECT p.naid, p.n, f.title, snippet(page_text, 0, '[[', ']]', ' … ', 18) AS snip
                               FROM page_text p JOIN archive_file f ON f.naid = p.naid
                               WHERE page_text MATCH ? ORDER BY rank LIMIT ?""", (fts, limit)).fetchall()
    return [dict(r) for r in rows]


def file_detail(fid):
    con = APP.db()
    f = con.execute("SELECT * FROM archive_file WHERE naid = ?", (fid,)).fetchone()
    if not f:
        return None
    pages = [{"n": r["n"], "status": r["status"], "conf": r["conf"], "url": r["url"],
              "image": (APP.DATA / "archive" / str(fid) / f"{r['n']}.jp2").exists()}
             for r in con.execute("SELECT * FROM archive_page WHERE naid = ? ORDER BY n", (fid,))]
    texts = {r["n"]: r["text"] for r in con.execute("SELECT n, text FROM page_text WHERE naid = ?", (fid,))}
    for p in pages:
        p["text"] = texts.get(p["n"], "")
    ms = [dict(r) for r in con.execute("SELECT eid, n, page, snip FROM archive_mention WHERE naid = ? ORDER BY n DESC", (fid,))]
    return {**dict(f), "fields": json.loads(f["fields"] or "{}"), "pages_list": pages, "mentions": ms,
            "catalog_url": f"https://catalog.archives.gov/id/{fid}"}


def all_fields():
    return {r["naid"]: json.loads(r["fields"] or "{}") for r in APP.db().execute("SELECT naid, fields FROM archive_file WHERE status = 'done'")}


def mentions():
    out = {}
    for r in APP.db().execute("SELECT m.eid, m.naid, m.n, m.page, m.snip, f.title FROM archive_mention m JOIN archive_file f ON f.naid = m.naid ORDER BY m.n DESC"):
        out.setdefault(r["eid"], []).append([r["naid"], r["n"], r["page"], r["snip"], r["title"]])
    return out


_jpeg_cache = {}


def page_jpeg(fid, n):
    key = (fid, n)
    if key in _jpeg_cache:
        return _jpeg_cache[key]
    path = APP.DATA / "archive" / str(fid) / f"{n}.jp2"
    if not path.exists():
        return None
    from PIL import Image
    im = Image.open(path).convert("L")
    im.thumbnail((1400, 1800))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=78)
    if len(_jpeg_cache) > 40:
        _jpeg_cache.pop(next(iter(_jpeg_cache)))
    _jpeg_cache[key] = buf.getvalue()
    return _jpeg_cache[key]


def add_source(naid):
    title = PRESETS.get(naid) or series_title(naid)
    with APP.LOCK, APP.db() as con:
        if con.execute("SELECT 1 FROM archive_source WHERE naid = ?", (naid,)).fetchone():
            raise FileExistsError("That archive series is already added.")
        con.execute("INSERT INTO archive_source (naid, title, added) VALUES (?,?,?)", (naid, title, time.time()))
    WAKE.set()
    return title


def set_paused(naid, paused):
    with APP.LOCK, APP.db() as con:
        con.execute("UPDATE archive_source SET paused = ? WHERE naid = ?", (1 if paused else 0, naid))
        if not paused:
            con.execute("UPDATE archive_file SET done_at = 0 WHERE series = ? AND status = 'failed'", (naid,))
    WAKE.set()


def remove_source(naid):
    with APP.LOCK, APP.db() as con:
        fids = [r[0] for r in con.execute("SELECT naid FROM archive_file WHERE series = ?", (naid,))]
        con.execute("DELETE FROM archive_source WHERE naid = ?", (naid,))
        for i in range(0, len(fids), 500):
            chunk = fids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for t in ("archive_page", "archive_mention", "page_text", "archive_file"):
                con.execute(f"DELETE FROM {t} WHERE naid IN ({marks})", chunk)
    for fid in fids:
        shutil.rmtree(APP.DATA / "archive" / str(fid), ignore_errors=True)


def init(app):
    global APP
    APP = app
    with app.LOCK, app.db() as con:
        con.executescript(SCHEMA)


def run():
    threading.Thread(target=worker, daemon=True).start()
