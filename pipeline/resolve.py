"""Entity resolution: decide which raw entities from different sources are the
same real-world thing, and fill entity.canonical_id.

Automatic merges are deliberately conservative (same normalised name or alias
within a compatible type). Weaker matches are written to
data/merge_candidates.txt for review; confirmed ones go in data/merges.json:

  {"merge": [["Ed Ruppelt", "Edward J. Ruppelt"]],
   "distinct": [["Steve Michalak", "Stan Michalak"]],
   "split": [{"name": "Robert Taylor", "source": "michigan-swamp-gas-ufo-reports"}]}

"distinct" silences a candidate; "split" undoes an automatic merge of two
different things that share a name.

Re-runnable: raw entities are never modified, only their canonical_id.
"""
import json, re, sqlite3, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data/ufo.db"

TITLES = set("""mr mrs ms miss dr prof professor sir lord rev father capt captain cpt lt lieutenant col colonel
lt col ltcol maj major gen general brig brigadier sgt sergeant ssgt tsgt msgt cpl corporal pvt private airman a1c
cmdr commander cdr adm admiral ens ensign officer chief sheriff deputy detective constable senator sen rep
representative congressman congresswoman president governor gov mayor judge first second 1st 2nd staff master
technical flight wing squadron group air marshal vice rear pilot astronaut usaf retired ret phd md""".split())

# Things extraction agents name inconsistently. Key: normalised form, value: canonical normalised form.
SYNONYMS = {
    "usaf": "united states air force", "us air force": "united states air force",
    "u s air force": "united states air force",
    "usaaf": "united states army air forces", "us army air forces": "united states army air forces",
    "army air forces": "united states army air forces", "united states army air force": "united states army air forces",
    "us navy": "united states navy", "u s navy": "united states navy", "usn": "united states navy",
    "us army": "united states army", "u s army": "united states army",
    "cia": "central intelligence agency", "fbi": "federal bureau of investigation",
    "nasa": "national aeronautics and space administration",
    "faa": "federal aviation administration", "raf": "royal air force",
    "mod": "ministry of defence", "uk ministry of defence": "ministry of defence", "british ministry of defence": "ministry of defence",
    "dod": "united states department of defense", "department of defense": "united states department of defense",
    "us department of defense": "united states department of defense", "pentagon": "united states department of defense",
    "the pentagon": "united states department of defense",
    "mufon": "mutual ufo network", "nicap": "national investigations committee on aerial phenomena",
    "apro": "aerial phenomena research organization", "cufos": "center for ufo studies",
    "j allen hynek center for ufo studies": "center for ufo studies",
    "aatip": "advanced aerospace threat identification program", "aaro": "all domain anomaly resolution office",
    "uaptf": "unidentified aerial phenomena task force", "uap task force": "unidentified aerial phenomena task force",
    "blue book": "project blue book", "condon report": "condon committee", "university of colorado ufo project": "condon committee",
    "us": "united states", "usa": "united states", "u s": "united states", "united states of america": "united states",
    "uk": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
    "ussr": "soviet union",
}
CASE_NOISE = set("the ufo ufos incident incidents case encounter sighting sightings affair event events of in at alleged reported".split())
GROUP = {"organization": "org", "program": "org", "document": "doc", "work": "doc"}   # places never merge with organisations ("New York" the magazine)
SUFFIXES = {"jr", "sr", "ii", "iii"}
GENERIC = {"united states", "united kingdom", "soviet union", "new mexico", "new york", "air force", "new zealand"}
NO_MERGE = {"craft", "vehicle"}  # specific to one case by nature


def base_norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"['’.]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^the ", "", s)


def norm(s):
    s = base_norm(s)
    return SYNONYMS.get(s, s)


def person_keys(name):
    toks = [t for t in norm(name).split() if t not in TITLES]
    suffix = [t for t in toks if t in SUFFIXES]          # Jr. and Sr. are different people
    toks = [t for t in toks if t not in SUFFIXES]
    core = [t for t in toks if len(t) > 1]
    keys = set()
    if len(toks) >= 2:
        keys.add(" ".join(toks + suffix))
    if len(core) >= 2:
        keys.add(" ".join([core[0], core[-1]] + suffix))
    return keys, (core[-1] if core else None), (core[0] if len(core) >= 2 else None)


def keys_for(etype, name):
    if etype == "person":
        return person_keys(name)[0]
    n = norm(name)
    if not n:
        return set()
    if etype == "case":
        core = " ".join(t for t in n.split() if t not in CASE_NOISE)   # the year stays: "1952 flap" is not "1973 flap"
        return {core or n}
    return {n}


def usable_alias(etype, alias, name=""):
    """A one-word alias ("Bureau", "Army", "Franklin") is too generic to merge
    on, unless it is an acronym (NICAP) or a known synonym (USAF)."""
    if etype == "person":
        return True                      # person_keys already needs two name tokens
    n = base_norm(alias)
    if n in SYNONYMS or (alias.isupper() and len(alias) >= 3):
        return True
    if len(n.split()) < 2:
        return False
    # A shortened form of the entity's own name ("National Archives" for
    # "National Archives of New Zealand") is generic, unless it is the title
    # before a subtitle or parenthetical.
    own = base_norm(name)
    if set(n.split()) < set(own.split()):
        return bool(re.match(re.escape(alias.strip()) + r"\s*[:(]", name.strip(), re.I))
    return True


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    ents = db.execute("SELECT e.id, e.source_id, e.type, e.name, s.slug FROM entity e JOIN source s ON s.id = e.source_id").fetchall()
    aliases = defaultdict(list)
    for r in db.execute("SELECT entity_id, alias FROM alias"):
        aliases[r["entity_id"]].append(r["alias"])
    merges_file = ROOT / "data/merges.json"
    manual = json.loads(merges_file.read_text()) if merges_file.exists() else {}

    parent = {e["id"]: e["id"] for e in ents}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    distinct = {frozenset(norm(n) for n in pair) for pair in manual.get("distinct", [])}
    by_key, by_alias, cands = defaultdict(list), defaultdict(list), []
    name_of = {e["id"]: e["name"] for e in ents}
    # "split": same name, different real-world thing; keep this source's entity on its own.
    split = {(norm(x["name"]), x["source"]) for x in manual.get("split", [])}
    for e in ents:
        if e["type"] in NO_MERGE or (norm(e["name"]), e["slug"]) in split:
            continue
        g = GROUP.get(e["type"], e["type"])
        for k in keys_for(e["type"], e["name"]):
            by_key[(g, k)].append(e["id"])
        for a in aliases[e["id"]]:
            if usable_alias(e["type"], a, e["name"]):
                for k in keys_for(e["type"], a):
                    by_alias[(g, k)].append(e["id"])
    # An alias merges an entity into something that carries that alias as its
    # primary name. Two entities that merely share an alias ("State Police")
    # are only flagged for review.
    for key, ids in by_alias.items():
        if key in by_key:
            by_key[key].extend(ids)
        else:
            names = sorted({name_of[i] for i in ids})
            cands.extend(f"shared-alias\t{a}\t{b}" for i, a in enumerate(names) for b in names[i + 1:])
    for pair in manual.get("merge", []):
        ks = {norm(n) for n in pair}
        ids = [e["id"] for e in ents if norm(e["name"]) in ks]      # primary names only
        by_key[("manual", "|".join(sorted(ks)))] = ids
    for ids in by_key.values():
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])

    groups = defaultdict(list)
    for e in ents:
        groups[find(e["id"])].append(e)

    db.execute("UPDATE entity SET canonical_id = NULL")
    db.execute("DELETE FROM canonical")
    canon_rows = []
    for members in groups.values():
        names = Counter(m["name"] for m in members)
        top = max(names.values())
        name = max((n for n, c in names.items() if c == top), key=len)
        etype = Counter(m["type"] for m in members).most_common(1)[0][0]
        n_cases = len({m["source_id"] for m in members})
        cid = db.execute("INSERT INTO canonical (type, name, n_cases) VALUES (?,?,?)", (etype, name, n_cases)).lastrowid
        db.executemany("UPDATE entity SET canonical_id=? WHERE id=?", [(cid, m["id"]) for m in members])
        canon_rows.append((cid, etype, name, n_cases, members))
    db.commit()

    # Weak candidates for human/agent review: people sharing a surname whose
    # first names are compatible, and named things where one name contains the other.
    people = defaultdict(list)
    for cid, etype, name, n, members in canon_rows:
        if etype == "person":
            _, last, first = person_keys(name)
            if last:
                people[last].append((first, name, n))
    for last, lst in people.items():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                (f1, n1, c1), (f2, n2, c2) = lst[i], lst[j]
                if frozenset((norm(n1), norm(n2))) in distinct:
                    continue
                if f1 is None or f2 is None or f1.startswith(f2) or f2.startswith(f1) or f1[0] == f2[0]:
                    cands.append(f"person\t{n1}\t{n2}")
    named = [(norm(name), name) for cid, etype, name, n, _ in canon_rows if etype in ("organization", "program") and "," not in name]
    toks = [(set(a.split()), a, name) for a, name in named]
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            a, b = toks[i], toks[j]
            small, big = (a, b) if len(a[0]) <= len(b[0]) else (b, a)
            if len(small[0]) >= 2 and small[1] not in GENERIC and small[0] < big[0] and frozenset((a[1], b[1])) not in distinct:
                cands.append(f"named\t{a[2]}\t{b[2]}")
    (ROOT / "data/merge_candidates.txt").write_text("\n".join(sorted(set(cands))) + "\n")

    multi = sorted((r for r in canon_rows if r[3] > 1 and r[1] not in ("feature", "explanation")), key=lambda r: -r[3])
    print(f"{len(ents)} raw entities -> {len(canon_rows)} canonical; {len(multi)} named things appear in 2+ cases; "
          f"{len(cands)} merge candidates to review")
    for cid, etype, name, n, _ in multi[:25]:
        print(f"  {n:3d} cases  {etype:12s} {name}")


if __name__ == "__main__":
    main()
