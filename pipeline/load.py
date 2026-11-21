"""Validate extraction files and load them into data/ufo.db.

  python3 pipeline/load.py                 rebuild the database from all files
  python3 pipeline/load.py --check FILE    validate one extraction file only

The key guarantee: every claim's quote must appear verbatim in the stored
source text. A claim that cannot be pinned to the source is rejected.
"""
import json, re, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, EXT, DB = ROOT / "data/sources", ROOT / "data/extractions", ROOT / "data/ufo.db"
VOCAB = json.loads((Path(__file__).parent / "vocab.json").read_text())
PUNCT = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "−": "-", " ": " ", " ": " "})
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")


def normalise(text):
    """Collapse whitespace and unify quote/dash styles. Returns (norm, map)
    where map[i] is the offset in the original text of norm[i]."""
    out, pos, prev_space = [], [], True
    for i, ch in enumerate(text.translate(PUNCT)):
        if ch.isspace():
            if not prev_space:
                out.append(" "); pos.append(i)
            prev_space = True
        else:
            out.append(ch); pos.append(i); prev_space = False
    return "".join(out), pos


def validate(ext, source_text):
    """Returns (errors, warnings, located) - located[i] = (start, end) of claim i's quote."""
    errors, warnings, located = [], [], {}
    norm, pos = normalise(source_text)
    ents = {}
    for e in ext.get("entities", []):
        k = e.get("key")
        if not k or k in ents:
            errors.append(f"entity key missing or duplicated: {k!r}"); continue
        ents[k] = e
        if e.get("type") not in VOCAB["entity_types"]:
            errors.append(f"entity {k}: unknown type {e.get('type')!r}")
        if not (e.get("name") or "").strip():
            errors.append(f"entity {k}: empty name")
        for t, allowed in (("feature", VOCAB["features"]), ("explanation", VOCAB["explanations"])):
            if e.get("type") == t and e.get("name") not in allowed and not str(e.get("name", "")).startswith("other:"):
                errors.append(f"entity {k}: {t} name {e.get('name')!r} is not in the vocabulary (use a listed name or 'other:<text>')")
    if ents.get("case", {}).get("type") != "case":
        errors.append("there must be an entity with key 'case' and type 'case'")
    used = set()
    for i, c in enumerate(ext.get("claims", [])):
        tag = f"claim {i} ({c.get('s')} {c.get('p')} {c.get('o', c.get('v'))})"
        if c.get("s") not in ents:
            errors.append(f"{tag}: unknown subject"); continue
        if ("o" in c) == ("v" in c):
            errors.append(f"{tag}: needs exactly one of 'o' (entity key) or 'v' (literal)"); continue
        if "o" in c and c["o"] not in ents:
            errors.append(f"{tag}: unknown object"); continue
        if not SNAKE.match(c.get("p") or ""):
            errors.append(f"{tag}: predicate must be snake_case")
        elif "o" in c and c["p"] not in VOCAB["case_link_predicates"] + VOCAB["entity_link_predicates"]:
            warnings.append(f"{tag}: predicate not in the recommended list")
        q, _ = normalise(c.get("quote") or "")
        q = q.strip()
        if not 8 <= len(q) <= 400:
            errors.append(f"{tag}: quote must be 8-400 characters (got {len(q)})"); continue
        at = norm.find(q)
        if at < 0:
            errors.append(f"{tag}: quote not found verbatim in source: {q[:70]!r}"); continue
        located[i] = (pos[at], pos[at + len(q) - 1] + 1)
        used.add(c["s"]); used.add(c.get("o"))
    for k in ents:
        if k not in used:
            errors.append(f"entity {k}: no valid claim mentions it (every entity needs a quoted claim)")
    return errors, warnings, located


def check_file(path):
    ext = json.loads(Path(path).read_text())
    src = SRC / f"{ext.get('slug')}.json"
    if not src.exists():
        return ext, None, [f"no source for slug {ext.get('slug')!r}"], [], {}
    doc = json.loads(src.read_text())
    return (ext, doc) + validate(ext, doc["text"])


def main():
    if "--check" in sys.argv:
        ext, _, errors, warnings, _ = check_file(sys.argv[sys.argv.index("--check") + 1])
        for w in warnings: print("warning:", w)
        for e in errors: print("ERROR:", e)
        print(f"{len(ext.get('entities', []))} entities, {len(ext.get('claims', []))} claims, {len(errors)} errors, {len(warnings)} warnings")
        sys.exit(1 if errors else 0)

    DB.unlink(missing_ok=True)
    db = sqlite3.connect(DB)
    db.executescript((Path(__file__).parent / "schema.sql").read_text())
    bad = 0
    for f in sorted(EXT.glob("*.json")):
        ext, doc, errors, warnings, located = check_file(f)
        if errors:
            bad += 1
            print(f"SKIPPED {f.name}: {len(errors)} errors (run --check)"); continue
        sid = db.execute("INSERT INTO source (slug,title,url,revision_id,fetched_at,license,lat,lon,text) VALUES (?,?,?,?,?,?,?,?,?)",
                         [doc[k] for k in ("slug", "title", "url", "revision_id", "fetched_at", "license", "lat", "lon", "text")]).lastrowid
        ids = {}
        for e in ext["entities"]:
            ids[e["key"]] = db.execute("INSERT INTO entity (source_id,key,type,name,description) VALUES (?,?,?,?,?)",
                                       (sid, e["key"], e["type"], e["name"].strip(), e.get("description"))).lastrowid
            db.executemany("INSERT INTO alias VALUES (?,?)", [(ids[e["key"]], a) for a in e.get("aliases", []) if a.strip()])
        for i, c in enumerate(ext["claims"]):
            s, t = located[i]
            db.execute("INSERT INTO claim (source_id,subject_id,predicate,object_id,value,quote,quote_start,quote_end) VALUES (?,?,?,?,?,?,?,?)",
                       (sid, ids[c["s"]], c["p"], ids.get(c.get("o")), None if "o" in c else str(c["v"]), doc["text"][s:t], s, t))
        print(f"loaded  {f.name}: {len(ext['entities'])} entities, {len(ext['claims'])} claims")
    db.commit()
    n = lambda t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"\n{n('source')} sources, {n('entity')} entities, {n('claim')} claims in {DB.relative_to(ROOT)}; {bad} files skipped")


if __name__ == "__main__":
    main()
