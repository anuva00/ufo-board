"""Score links and export the board data to ui/data.json.

Rarity weight of a shared thing that appears in df of N cases:
    w = ln(N / df) / ln(N / 2)      (1.0 when only two cases share it, 0 when all do)
A pair of cases is scored by the sum of w over everything they share, so a
name in exactly two cases outweighs any number of "United States Air Force".
"""
import json, math, sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data/ufo.db"
LOCAL_ONLY = ("craft", "vehicle")


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    sources = db.execute("SELECT id, slug, title, url, lat, lon FROM source ORDER BY id").fetchall()
    N = len(sources)

    # The canonical entity that *is* each case (key 'case' in its own source).
    own = {r["source_id"]: r["canonical_id"] for r in db.execute("SELECT source_id, canonical_id FROM entity WHERE key='case'")}
    case_of_canon = {c: s for s, c in own.items()}

    cases = {}
    for s in sources:
        lit = defaultdict(list)
        for r in db.execute("""SELECT c.predicate, c.value FROM claim c JOIN entity e ON e.id=c.subject_id
                               WHERE c.source_id=? AND e.key='case' AND c.value IS NOT NULL ORDER BY c.id""", (s["id"],)):
            lit[r["predicate"]].append(r["value"])
        desc = db.execute("SELECT description FROM entity WHERE source_id=? AND key='case'", (s["id"],)).fetchone()[0]
        cases[s["id"]] = {"id": s["id"], "slug": s["slug"], "name": s["title"], "url": s["url"], "lat": s["lat"], "lon": s["lon"],
                          "date": (lit.get("date_start") or [None])[0], "country": (lit.get("country") or [None])[0],
                          "description": desc}

    canon = {r["id"]: dict(r) for r in db.execute("SELECT id, type, name FROM canonical")}
    # membership: canonical entity -> case -> evidence (claims touching it in that case)
    member = defaultdict(lambda: defaultdict(list))
    rows = db.execute("""
        SELECT c.source_id, c.predicate, c.value, c.quote, c.quote_start,
               s.canonical_id AS scid, s.name AS sname, s.key AS skey,
               o.canonical_id AS ocid, o.name AS oname, o.key AS okey
        FROM claim c JOIN entity s ON s.id=c.subject_id LEFT JOIN entity o ON o.id=c.object_id ORDER BY c.id""").fetchall()
    for r in rows:
        ev = {"s": r["sname"], "p": r["predicate"], "o": r["oname"] if r["oname"] is not None else r["value"],
              "lit": r["oname"] is None, "tocase": r["okey"] == "case", "q": r["quote"], "at": r["quote_start"]}
        for cid, key in ((r["scid"], r["skey"]), (r["ocid"], r["okey"])):
            if cid is not None and key != "case":
                member[cid][r["source_id"]].append(ev)
    # a case that is itself in the database counts as a member of its own node
    for cid, sid in case_of_canon.items():
        member[cid].setdefault(sid, [])

    entities, links = [], []
    for cid in [i for i in member if canon[i]["name"].lower().startswith("list of ")]:
        del member[cid]                      # navigation lists link everything to everything
    for cid, per_case in member.items():
        c = canon[cid]
        df = len(per_case)
        shareable = c["type"] not in LOCAL_ONLY
        w = round(math.log(N / df) / math.log(N / 2), 3) if shareable and df >= 2 and N > 2 else 0.0
        roles = defaultdict(set)
        for sid, evs in per_case.items():
            for ev in evs:
                if ev["tocase"]:
                    roles[sid].add(ev["p"])
        entities.append({"id": cid, "type": c["type"], "name": c["name"], "df": df, "w": max(w, 0.0),
                         "case": case_of_canon.get(cid)})
        for sid, evs in per_case.items():
            links.append({"e": cid, "c": sid, "roles": sorted(roles[sid]), "ev": evs})

    weight = {e["id"]: e["w"] for e in entities}
    shared = defaultdict(list)
    for cid, per_case in member.items():
        if weight[cid] <= 0 and cid not in case_of_canon:
            continue
        involved = set(per_case)
        for a, b in combinations(sorted(involved), 2):
            shared[(a, b)].append(cid)
    pairs = []
    for (a, b), ids in shared.items():
        direct = [i for i in ids if case_of_canon.get(i) in (a, b)]
        score = sum(weight[i] for i in ids) + 1.0 * len(direct)
        pairs.append({"a": a, "b": b, "score": round(score, 3), "direct": bool(direct),
                      "shared": sorted(ids, key=lambda i: -weight[i])})
    pairs.sort(key=lambda p: -p["score"])

    out = {"n_cases": N, "n_claims": len(rows), "cases": list(cases.values()), "entities": entities, "links": links, "pairs": pairs}
    (ROOT / "ui").mkdir(exist_ok=True)
    path = ROOT / "ui/data.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"{N} cases, {len(entities)} entities, {len(links)} case-entity links, {len(pairs)} linked case pairs "
          f"-> {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    name = lambda i: canon[i]["name"]
    print("\nStrongest case-to-case links:")
    for p in pairs[:12]:
        print(f"  {p['score']:6.2f}  {cases[p['a']]['name']}  <->  {cases[p['b']]['name']}")
        print("          via " + "; ".join(name(i) for i in p["shared"][:6]))
    rare = sorted((e for e in entities if e["df"] in (2, 3) and e["type"] in ("person", "organization", "place", "document", "work", "program")),
                  key=lambda e: (e["df"], e["type"]))
    print(f"\n{len(rare)} named things appear in exactly 2-3 cases (the interesting ones).")


if __name__ == "__main__":
    main()
