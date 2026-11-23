"""UFO Evidence Board server for the NAS.

Serves the dashboard and a small API for subscribed sources. A background
thread checks each subscribed YouTube channel for new videos, saves the
captions YouTube already has for them (no transcription model, no paid
service), and finds every name from the board that each transcript mentions.

Everything it stores lives in DATA_DIR (a folder on the NAS):
  board.db              subscriptions, episodes, mentions (SQLite)
  transcripts/<id>.json caption text with timings

Environment:
  DATA_DIR      /data        where to keep state
  SITE_DIR      /app/site    the dashboard files
  PORT          8080
  CHECK_HOURS   6            how often to look for new videos
  PAUSE_SECONDS 8            wait between caption downloads, to stay under YouTube's limits
"""
import gzip, hashlib, json, os, re, sqlite3, subprocess, threading, time, traceback, unicodedata, urllib.parse, urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "/data"))
SITE = Path(os.environ.get("SITE_DIR", "/app/site"))
PORT = int(os.environ.get("PORT", "8080"))
CHECK_HOURS = float(os.environ.get("CHECK_HOURS", "6"))
PAUSE = float(os.environ.get("PAUSE_SECONDS", "8"))
BLOCK_PAUSE_HOURS = 6
MAX_TRIES = 3
TRANSCRIPTS = DATA / "transcripts"
LOCK = threading.Lock()
WAKE = threading.Event()
STATE = {"activity": "Starting", "paused_until": 0, "paused_reason": ""}

# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, name TEXT, kind TEXT,
  added REAL, last_checked REAL, last_error TEXT, force INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS episode (vid TEXT PRIMARY KEY, source_id INTEGER, title TEXT, duration INTEGER, pos INTEGER,
  status TEXT DEFAULT 'waiting', lang TEXT, error TEXT, tries INTEGER DEFAULT 0, attempted REAL, fetched_at REAL, added REAL);
CREATE TABLE IF NOT EXISTS mention (vid TEXT, eid INTEGER, n INTEGER, t REAL, snips TEXT, PRIMARY KEY (vid, eid));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS mention_eid ON mention(eid);
"""


def db():
    con = sqlite3.connect(DATA / "board.db", timeout=30)
    con.row_factory = sqlite3.Row
    return con


def setup():
    DATA.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS.mkdir(exist_ok=True)
    with LOCK, db() as con:
        con.executescript(SCHEMA)


# ---------------------------------------------------------------- YouTube

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}


def normalise_url(url):
    """Accept a channel, playlist or video link; return (url to list, kind) or raise ValueError."""
    url = url.strip()
    if not re.match(r"https?://", url):
        url = "https://" + url
    u = urllib.parse.urlparse(url)
    if u.hostname not in YT_HOSTS:
        raise ValueError("Only YouTube links are supported so far.")
    q = urllib.parse.parse_qs(u.query)
    if u.hostname == "youtu.be" or u.path == "/watch" or u.path.startswith("/shorts/"):
        vid = u.path.strip("/") if u.hostname == "youtu.be" else (q.get("v", [""])[0] or u.path.split("/")[-1])
        if not re.fullmatch(r"[\w-]{11}", vid):
            raise ValueError("That video link has no valid video id.")
        return f"https://www.youtube.com/watch?v={vid}", "video"
    if u.path == "/playlist" and q.get("list"):
        return f"https://www.youtube.com/playlist?list={q['list'][0]}", "playlist"
    m = re.match(r"^/(@[\w.\-]+|channel/[\w-]+|c/[\w.\-]+|user/[\w.\-]+)", u.path)
    if not m:
        raise ValueError("That does not look like a channel, playlist or video link.")
    return f"https://www.youtube.com/{m[1]}/videos", "channel"


def list_videos(url):
    """(name, [(video id, title, duration seconds)]) for a channel, playlist or single video, newest first."""
    m = re.search(r"[?&]v=([\w-]{11})", url)
    if m:                                          # a single video: YouTube's oEmbed gives the title without a bot check
        req = urllib.request.Request(f"https://www.youtube.com/oembed?format=json&url={urllib.parse.quote(url, safe='')}",
                                     headers={"User-Agent": "ufo-board/1.0"})
        try:
            title = json.load(urllib.request.urlopen(req, timeout=30)).get("title") or m[1]
        except Exception:
            title = m[1]
        return title, [(m[1], title, None)]
    out = subprocess.run(["yt-dlp", "--flat-playlist", "-J", "--no-warnings", "--", url],
                         capture_output=True, text=True, timeout=900)
    d = json.loads(out.stdout) if out.stdout.strip() else None
    if not d:
        raise RuntimeError((out.stderr.strip().splitlines() or ["yt-dlp could not read that link"])[-1][:300])
    vids = [(e["id"], e.get("title") or e["id"], e.get("duration")) for e in d["entries"] if e and e.get("id") and len(e["id"]) == 11]
    name = d.get("channel") or d.get("uploader") or d.get("title") or url
    return re.sub(r"\s*-\s*Videos$", "", name), vids


class Blocked(Exception):
    pass


class NoCaptions(Exception):
    pass


def fetch_captions(vid):
    """[(start seconds, text)], language code. Uses only the captions YouTube already has."""
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        tl = YouTubeTranscriptApi().list(vid)
        try:
            t = tl.find_transcript(["en", "en-US", "en-GB"])
        except Exception:
            t = next(iter(tl))                     # no English: keep the original language
        snips = t.fetch()
        return [(round(s.start, 1), s.text) for s in snips], t.language_code
    except Exception as e:
        name = type(e).__name__
        if name in ("IpBlocked", "RequestBlocked", "PoTokenRequired") or re.search(r"/sorry/|429|Too Many Requests", str(e)):
            raise Blocked(name) from e
        if name in ("TranscriptsDisabled", "NoTranscriptFound", "StopIteration"):
            raise NoCaptions("This video has no captions.") from e
        if name in ("VideoUnavailable", "VideoUnplayable", "AgeRestricted", "InvalidVideoId"):
            raise NoCaptions(f"YouTube says: {name}") from e
        raise


# ---------------------------------------------------------------- name matching

STRIP = set("ufo ufos uap incident incidents sighting sightings encounter case affair photograph photographs film the".split())
TITLES = set("mr mrs ms dr prof sir lt col gen maj capt cmdr adm sgt sen rep president senator congressman general colonel major captain commander".split())
SKIP_SINGLE = {"mariana", "aurora", "phoenix", "time", "life", "look"}
MATCH_TYPES = {"person", "organization", "program", "place", "case", "document", "work"}
PRIORITY = {"case": 0, "program": 1, "organization": 2, "person": 3, "place": 4, "document": 5, "work": 6}


def toks(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’]", "", s)
    return re.findall(r"[a-z0-9]+", s)


def keys_for(e):
    """Word sequences that count as a mention of entity e."""
    out = set()
    for raw in [e["name"], *e.get("al", [])]:
        text = re.sub(r"\(.*?\)", "", raw)
        t = toks(text)
        if t and t[0] == "the":
            t = t[1:]
        if e["type"] == "person":
            t = [w for w in t if w not in TITLES]
            core = [w for w in t if len(w) > 1]
            if len(core) >= 2:
                out.add(tuple(core))
                out.add((core[0], core[-1]))
            continue
        if len(t) >= 2:
            out.add(tuple(t))
        elif len(t) == 1 and raw.strip().isupper() and len(t[0]) >= 3:   # acronyms: AARO, NASA, MUFON
            out.add(tuple(t))
        if e["type"] == "case" or e.get("case") is not None:
            short = [w for w in t if w not in STRIP and not re.fullmatch(r"(19|20)\d\d", w)]
            if len(short) >= 2 or (len(short) == 1 and len(short[0]) >= 5 and short[0] not in SKIP_SINGLE):
                out.add(tuple(short))
    return out


class Matcher:
    def __init__(self, entities):
        best = {}
        for e in entities:
            if e["type"] not in MATCH_TYPES and e.get("case") is None:
                continue
            for k in keys_for(e):
                cur = best.get(k)
                rank = (PRIORITY.get(e["type"], 9), -e.get("df", 0))
                if cur is None or rank < cur[0]:
                    best[k] = (rank, e["id"])
        self.keys = {k: v[1] for k, v in best.items()}
        self.maxlen = max((len(k) for k in self.keys), default=1)

    def find(self, segments):
        """{entity id: [count, first time, [(time, snippet), ...]]} for a list of (start, text) captions."""
        words, at = [], []
        for i, (_, text) in enumerate(segments):
            for w in toks(text):
                words.append(w)
                at.append(i)
        hits = {}
        i = 0
        while i < len(words):
            for n in range(min(self.maxlen, len(words) - i), 0, -1):
                eid = self.keys.get(tuple(words[i:i + n]))
                if eid is not None:
                    seg = at[i]
                    h = hits.setdefault(eid, [0, segments[seg][0], []])
                    h[0] += 1
                    if len(h[2]) < 3 and (not h[2] or segments[seg][0] - h[2][-1][0] > 60):
                        snip = " ".join(segments[j][1] for j in range(max(0, seg - 1), min(len(segments), seg + 2)))
                        h[2].append((segments[seg][0], re.sub(r"\s+", " ", snip).strip()[:240]))
                    i += n - 1
                    break
            i += 1
        return hits


MATCHER = None


def load_matcher():
    global MATCHER
    raw = (SITE / "data.json").read_bytes()
    digest = hashlib.sha1(raw).hexdigest()
    MATCHER = Matcher(json.loads(raw)["entities"])
    with LOCK, db() as con:
        old = con.execute("SELECT v FROM meta WHERE k = 'data_hash'").fetchone()
    if not old or old[0] != digest:                # the board's names changed: re-match every saved transcript
        vids = [r[0] for r in db().execute("SELECT vid FROM episode WHERE status = 'done'")]
        for vid in vids:
            match_episode(vid)
        with LOCK, db() as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('data_hash', ?)", (digest,))
        if vids:
            print(f"re-matched {len(vids)} transcripts against the updated board", flush=True)


def match_episode(vid):
    path = TRANSCRIPTS / f"{vid}.json"
    if not path.exists():
        return
    segs = json.loads(path.read_text())["segments"]
    hits = MATCHER.find(segs)
    with LOCK, db() as con:
        con.execute("DELETE FROM mention WHERE vid = ?", (vid,))
        con.executemany("INSERT INTO mention VALUES (?,?,?,?,?)",
                        [(vid, eid, h[0], h[1], json.dumps(h[2])) for eid, h in hits.items()])


# ---------------------------------------------------------------- background worker

def check_sources(con_now):
    due = db().execute("SELECT * FROM source WHERE force = 1 OR last_checked IS NULL OR last_checked < ?",
                       (con_now - CHECK_HOURS * 3600,)).fetchall()
    for s in due:
        STATE["activity"] = f"Looking for new videos on {s['name'] or s['url']}"
        try:
            name, vids = list_videos(s["url"])
            err = None
        except Exception as e:
            name, vids, err = s["name"], [], str(e)[:300]
        now = time.time()
        with LOCK, db() as con:
            for pos, (vid, title, dur) in enumerate(vids):
                con.execute("""INSERT INTO episode (vid, source_id, title, duration, pos, added) VALUES (?,?,?,?,?,?)
                               ON CONFLICT(vid) DO UPDATE SET title = excluded.title, pos = excluded.pos
                               WHERE episode.source_id = excluded.source_id""",
                            (vid, s["id"], title, int(dur) if dur else None, pos, now))
            con.execute("UPDATE source SET name = ?, last_checked = ?, last_error = ?, force = 0 WHERE id = ?",
                        (name, now, err, s["id"]))


def fetch_waiting():
    while True:
        if time.time() < STATE["paused_until"]:
            return
        row = db().execute("""SELECT e.vid, e.title, e.tries FROM episode e JOIN source s ON s.id = e.source_id
                              WHERE e.status = 'waiting' OR (e.status = 'failed' AND e.tries < ? AND e.attempted < ?)
                              ORDER BY e.status = 'failed', s.id, e.pos LIMIT 1""",
                           (MAX_TRIES, time.time() - BLOCK_PAUSE_HOURS * 3600)).fetchone()
        if not row or WAKE.is_set():
            return
        vid = row["vid"]
        STATE["activity"] = f"Saving captions: {row['title']}"
        status, lang, err = "done", None, None
        try:
            segs, lang = fetch_captions(vid)
            (TRANSCRIPTS / f"{vid}.json").write_text(json.dumps({"id": vid, "lang": lang, "segments": segs}, ensure_ascii=False))
        except Blocked as e:
            STATE["paused_until"] = time.time() + BLOCK_PAUSE_HOURS * 3600
            STATE["paused_reason"] = f"YouTube is refusing caption requests from this connection ({e}). Trying again in {BLOCK_PAUSE_HOURS} hours."
            with LOCK, db() as con:
                con.execute("UPDATE episode SET attempted = ? WHERE vid = ?", (time.time(), vid))
            return
        except NoCaptions as e:
            status, err = "none", str(e)
        except Exception as e:
            status, err = "failed", f"{type(e).__name__}: {e}"[:300]
        with LOCK, db() as con:
            con.execute("UPDATE episode SET status = ?, lang = ?, error = ?, tries = tries + 1, attempted = ?, fetched_at = ? WHERE vid = ?",
                        (status, lang, err, time.time(), time.time() if status == "done" else None, vid))
        if status == "done":
            match_episode(vid)
        time.sleep(PAUSE)


def worker():
    while True:
        WAKE.clear()
        try:
            check_sources(time.time())
            fetch_waiting()
        except Exception:
            traceback.print_exc()
        paused = time.time() < STATE["paused_until"]
        STATE["activity"] = "Paused" if paused else "Up to date"
        WAKE.wait(timeout=600)


# ---------------------------------------------------------------- HTTP

def counts(con, sid):
    c = {"total": 0, "done": 0, "waiting": 0, "none": 0, "failed": 0}
    for r in con.execute("SELECT status, count(*) FROM episode WHERE source_id = ? GROUP BY status", (sid,)):
        c[r[0]] = r[1]
        c["total"] += r[1]
    return c


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(SITE), **k)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/"):
            return
        super().log_message(fmt, *args)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        gz = "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 2048
        if gz:
            body = gzip.compress(body, 5)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(min(n, 65536)) or b"{}") if n else {}

    # static files: serve a pre-compressed copy when the browser accepts it
    def send_head(self):
        path = self.translate_path(self.path)
        if "gzip" in (self.headers.get("Accept-Encoding") or "") and os.path.isfile(path + ".gz"):
            f = open(path + ".gz", "rb")
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return f
        return super().send_head()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if not u.path.startswith("/api/"):
            return super().do_GET()
        con = db()
        if u.path == "/api/health":
            return self.send_json({"ok": True, "activity": STATE["activity"],
                                   "paused": STATE["paused_reason"] if time.time() < STATE["paused_until"] else "",
                                   "check_hours": CHECK_HOURS})
        if u.path == "/api/sources":
            return self.send_json([{**dict(r), "counts": counts(con, r["id"])}
                                   for r in con.execute("SELECT * FROM source ORDER BY added")])
        if u.path == "/api/episodes":
            rows = con.execute("""SELECT e.vid, e.source_id, e.title, e.duration, e.pos, e.status, e.lang, e.error,
                                  (SELECT count(*) FROM mention m WHERE m.vid = e.vid) AS names,
                                  (SELECT coalesce(sum(n), 0) FROM mention m WHERE m.vid = e.vid) AS mentions
                                  FROM episode e ORDER BY e.source_id, e.pos""").fetchall()
            return self.send_json([dict(r) for r in rows])
        m = re.fullmatch(r"/api/episodes/([\w-]{11})", u.path)
        if m:
            e = con.execute("SELECT * FROM episode WHERE vid = ?", (m[1],)).fetchone()
            if not e:
                return self.send_json({"error": "No such episode."}, 404)
            ms = [{"e": r["eid"], "n": r["n"], "t": r["t"], "s": json.loads(r["snips"])}
                  for r in con.execute("SELECT * FROM mention WHERE vid = ? ORDER BY n DESC", (m[1],))]
            return self.send_json({**dict(e), "mentions": ms})
        if u.path == "/api/mentions":
            out = {}
            for r in con.execute("SELECT eid, vid, n, t, snips FROM mention ORDER BY n DESC"):
                s = json.loads(r["snips"])
                out.setdefault(r["eid"], []).append([r["vid"], r["n"], r["t"], s[0][1] if s else ""])
            return self.send_json(out)
        return self.send_json({"error": "Not found."}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/sources":
            try:
                url, kind = normalise_url(str(self.body().get("url", "")))
            except (ValueError, json.JSONDecodeError) as e:
                return self.send_json({"error": str(e)}, 400)
            with LOCK, db() as con:
                if con.execute("SELECT 1 FROM source WHERE url = ?", (url,)).fetchone():
                    return self.send_json({"error": "That source is already subscribed."}, 409)
                con.execute("INSERT INTO source (url, name, kind, added, force) VALUES (?,?,?,?,1)", (url, None, kind, time.time()))
            WAKE.set()
            return self.send_json({"ok": True, "url": url, "kind": kind}, 201)
        m = re.fullmatch(r"/api/sources/(\d+)/check", u.path)
        if m:
            with LOCK, db() as con:
                con.execute("UPDATE source SET force = 1 WHERE id = ?", (int(m[1]),))
                con.execute("UPDATE episode SET tries = 0 WHERE source_id = ? AND status = 'failed'", (int(m[1]),))
            STATE["paused_until"] = 0
            WAKE.set()
            return self.send_json({"ok": True})
        return self.send_json({"error": "Not found."}, 404)

    def do_DELETE(self):
        m = re.fullmatch(r"/api/sources/(\d+)", urllib.parse.urlparse(self.path).path)
        if not m:
            return self.send_json({"error": "Not found."}, 404)
        sid = int(m[1])
        with LOCK, db() as con:
            vids = [r[0] for r in con.execute("SELECT vid FROM episode WHERE source_id = ?", (sid,))]
            con.execute("DELETE FROM mention WHERE vid IN (SELECT vid FROM episode WHERE source_id = ?)", (sid,))
            con.execute("DELETE FROM episode WHERE source_id = ?", (sid,))
            con.execute("DELETE FROM source WHERE id = ?", (sid,))
        for vid in vids:
            (TRANSCRIPTS / f"{vid}.json").unlink(missing_ok=True)
        return self.send_json({"ok": True})


def main():
    setup()
    load_matcher()
    threading.Thread(target=worker, daemon=True).start()
    print(f"UFO Evidence Board on port {PORT}; data in {DATA}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
