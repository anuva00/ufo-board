"""Score links and export the deep layer to ui/data.json.

A "case file" is one case or one multi-case document (hearing, report); it
can have several sources. Rarity weight of a thing shared by df of N files:
    w = ln(N / df) / ln(N / 2)      (1.0 when only two share it, 0 when all do)
A pair of files is scored by the sum of w over everything they share, plus 1
if one names the other.

Also attaches to each case the bulk reports (ui/reports.json) filed within
NEAR_KM of it in the same or an adjacent month, as a cross-check against the
wide layer.
"""
import json, math, re, sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data/ufo.db"
LOCAL_ONLY = ("craft", "vehicle")
NEAR_KM = 150


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def geocoder():
    """Place name -> (lat, lon), using the same gazetteer as the bulk layer."""
    try:
        from reports import Gazetteer, STATES, load_nuforc, norm
        gaz = Gazetteer(load_nuforc())
    except Exception as e:                     # bulk data not fetched: cases keep only Wikipedia coordinates
        print("  (no gazetteer:", e, ")")
        return lambda name: None

    def lookup(name):
        toks = [t.strip() for t in re.sub(r"\(.*?\)", "", name).split(",") if t.strip()]
        if len(toks) >= 2 and norm(toks[-1]) in STATES:
            return gaz.us_city(toks[0], STATES[norm(toks[-1])])
        if len(toks) >= 2 and norm(toks[-1]) in gaz.countries:
            return gaz.city(toks[0], gaz.countries[norm(toks[-1])])
        return None
    return lookup


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    sources = db.execute("SELECT id, slug, title, url, lat, lon, kind, publisher, date, case_slug, case_name, case_kind FROM source ORDER BY id").fetchall()
    slugs = list(dict.fromkeys(s["case_slug"] for s in sources))
    cid_of = {slug: i + 1 for i, slug in enumerate(slugs)}                 # case file id
    case_of_source = {s["id"]: cid_of[s["case_slug"]] for s in sources}
    N = len(slugs)
    lookup = geocoder()
    manual = json.loads((ROOT / "data/case_coords.json").read_text()) if (ROOT / "data/case_coords.json").exists() else {}

    cases = {}
    for slug in slugs:
        srcs = [s for s in sources if s["case_slug"] == slug]
        wiki = [s for s in srcs if s["kind"] == "encyclopedia"]
        name = wiki[0]["title"] if wiki else (srcs[0]["case_name"] or re.sub(r" \(part \d+ of \d+\)$", "", srcs[0]["title"]))
        ids = [s["id"] for s in srcs]
        marks = ",".join("?" * len(ids))
        lit = defaultdict(list)
        for r in db.execute(f"""SELECT c.predicate, c.value FROM claim c JOIN entity e ON e.id=c.subject_id
                                WHERE c.source_id IN ({marks}) AND e.key='case' AND c.value IS NOT NULL ORDER BY c.id""", ids):
            lit[r["predicate"]].append(r["value"])
        desc = db.execute(f"SELECT description FROM entity WHERE source_id IN ({marks}) AND key='case' AND description IS NOT NULL ORDER BY id", ids).fetchone()
        kind = "document" if all(s["case_kind"] == "document" for s in srcs) else "case"
        if kind == "case" and not wiki:          # a resolved single incident is named by its extraction, not the paper's title
            n = db.execute(f"SELECT name FROM entity WHERE source_id IN ({marks}) AND key='case' ORDER BY id", ids).fetchone()
            name = n[0] if n else name
        lat, lon = next(((s["lat"], s["lon"]) for s in srcs if s["lat"] is not None), (None, None))
        if slug in manual and slug != "_about":
            lat, lon = manual[slug]["lat"], manual[slug]["lon"]
        if lat is None and kind == "case":
            for r in db.execute(f"""SELECT e.name FROM claim c JOIN entity e ON e.id=c.subject_id JOIN entity o ON o.id=c.object_id
                                    WHERE c.source_id IN ({marks}) AND o.key='case' AND c.predicate='occurred_at' ORDER BY c.id""", ids):
                pos = lookup(r["name"])
                if pos:
                    lat, lon = round(pos[0], 3), round(pos[1], 3)
                    break
        date = (lit.get("date_start") or [None])[0] or next((s["date"] for s in srcs if s["date"]), None)
        seen, src_list = set(), []
        for s in srcs:                                   # parts of one document collapse to one entry
            key = (s["url"], s["kind"])
            if key in seen:
                continue
            seen.add(key)
            src_list.append({"title": re.sub(r" \(part \d+ of \d+\)$", "", s["title"]), "url": s["url"], "kind": s["kind"],
                             "publisher": s["publisher"], "date": s["date"]})
        cases[cid_of[slug]] = {"id": cid_of[slug], "slug": slug, "name": name, "kind": kind, "sources": src_list,
                               "lat": lat, "lon": lon, "date": date, "country": (lit.get("country") or [None])[0],
                               "description": desc[0] if desc else None}
    src_no = {}                                          # source row id -> index into its case's source list
    for s in sources:
        lst = cases[case_of_source[s["id"]]]["sources"]
        src_no[s["id"]] = next(i for i, x in enumerate(lst) if x["url"] == s["url"] and x["kind"] == s["kind"])

    own = {case_of_source[r["source_id"]]: r["canonical_id"] for r in db.execute("SELECT source_id, canonical_id FROM entity WHERE key='case'")}
    case_of_canon = {c: s for s, c in own.items()}

    canon = {r["id"]: dict(r) for r in db.execute("SELECT id, type, name FROM canonical")}
    # Other names each thing goes by (raw names and aliases), for matching it in video transcripts.
    also = defaultdict(set)
    for r in db.execute("SELECT canonical_id, name FROM entity UNION SELECT e.canonical_id, a.alias FROM alias a JOIN entity e ON e.id = a.entity_id"):
        if r[0] is not None and r[1] and r[1].strip() and r[1].strip() != canon[r[0]]["name"]:
            also[r[0]].add(r[1].strip())
    member = defaultdict(lambda: defaultdict(list))      # canonical entity -> case file -> evidence
    rows = db.execute("""
        SELECT c.source_id, c.predicate, c.value, c.quote,
               s.canonical_id AS scid, s.name AS sname, s.key AS skey,
               o.canonical_id AS ocid, o.name AS oname, o.key AS okey
        FROM claim c JOIN entity s ON s.id=c.subject_id LEFT JOIN entity o ON o.id=c.object_id ORDER BY c.id""").fetchall()
    for r in rows:
        ev = {"s": r["sname"], "p": r["predicate"], "o": r["oname"] if r["oname"] is not None else r["value"],
              "lit": r["oname"] is None, "tocase": r["okey"] == "case", "q": r["quote"], "src": src_no[r["source_id"]]}
        for cid, key in ((r["scid"], r["skey"]), (r["ocid"], r["okey"])):
            if cid is not None and key != "case":
                member[cid][case_of_source[r["source_id"]]].append(ev)
    for cid, case in case_of_canon.items():
        member[cid].setdefault(case, [])
    for cid in [i for i in member if canon[i]["name"].lower().startswith("list of ")]:
        del member[cid]                                  # navigation lists link everything to everything

    entities, links = [], []
    for cid, per_case in member.items():
        c = canon[cid]
        df = len(per_case)
        shareable = c["type"] not in LOCAL_ONLY
        w = round(math.log(N / df) / math.log(N / 2), 3) if shareable and df >= 2 and N > 2 else 0.0
        ent = {"id": cid, "type": c["type"], "name": c["name"], "df": df, "w": max(w, 0.0), "case": case_of_canon.get(cid)}
        if also.get(cid):
            ent["al"] = sorted(also[cid], key=len)[:8]
        entities.append(ent)
        for case, evs in per_case.items():
            roles = sorted({ev["p"] for ev in evs if ev["tocase"]})
            links.append({"e": cid, "c": case, "roles": roles, "ev": evs})

    weight = {e["id"]: e["w"] for e in entities}
    shared = defaultdict(list)
    for cid, per_case in member.items():
        if weight[cid] <= 0 and cid not in case_of_canon:
            continue
        for a, b in combinations(sorted(per_case), 2):
            shared[(a, b)].append(cid)
    pairs = []
    for (a, b), ids in shared.items():
        direct = [i for i in ids if case_of_canon.get(i) in (a, b)]
        score = sum(weight[i] for i in ids) + 1.0 * len(direct)
        pairs.append({"a": a, "b": b, "score": round(score, 2), "direct": bool(direct), "shared": sorted(ids, key=lambda i: -weight[i])})
    pairs.sort(key=lambda p: -p["score"])

    # ---- cross-check against the wide layer
    rep_path = ROOT / "ui/reports.json"
    if rep_path.exists():
        rep = json.loads(rep_path.read_text())
        for c in cases.values():
            m = re.match(r"(\d{4})(?:-(\d\d))?(?:-(\d\d))?", c["date"] or "")
            if not m or not m[2] or c["lat"] is None or c["kind"] != "case":
                continue
            t0 = int(m[1]) * 12 + int(m[2])
            near = []
            for i in range(rep["n"]):
                if rep["lat"][i] is None or not rep["m"][i] or abs(rep["y"][i] * 12 + rep["m"][i] - t0) > 1:
                    continue
                d = km(c["lat"], c["lon"], rep["lat"][i], rep["lon"][i])
                if d <= NEAR_KM:
                    near.append((round(d), i))
            near.sort()
            c["near"] = [i for _, i in near[:40]]
            c["near_n"] = len(near)

    out = {"n_cases": N, "n_claims": len(rows), "cases": list(cases.values()), "entities": entities, "links": links, "pairs": pairs}
    path = ROOT / "ui/data.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    n_doc = sum(1 for c in cases.values() if c["kind"] == "document")
    print(f"{N} case files ({N - n_doc} cases, {n_doc} documents), {sum(len(c['sources']) for c in cases.values())} sources, "
          f"{len(entities)} entities, {len(links)} links, {len(pairs)} linked pairs -> ui/data.json ({path.stat().st_size // 1024} KB)")
    print(f"mapped cases: {sum(1 for c in cases.values() if c['lat'] is not None)}; with nearby bulk reports: {sum(1 for c in cases.values() if c.get('near_n'))}")
    print("\nStrongest links:")
    for p in pairs[:10]:
        print(f"  {p['score']:6.2f}  {cases[p['a']]['name'][:50]}  <->  {cases[p['b']]['name'][:50]}")
        print("          via " + "; ".join(canon[i]["name"] for i in p["shared"][:6]))


if __name__ == "__main__":
    main()
