"""Build ui/nuclear.json: nuclear power stations, weapons sites and test sites.

Power stations come from the open GeoNuclearData set (WNA / IAEA PRIS based,
last updated March 2024), fetched to data/raw/nuclear/:
  https://github.com/cristianst85/GeoNuclearData
Reactors are grouped into stations. Weapons and test sites come from the
hand-entered list in data/nuclear_weapons_sites.json.
"""
import csv, json, re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = {"Operational": "active", "Under Construction": "construction", "Planned": "planned", "Shutdown": "inactive",
          "Suspended Operation": "suspended", "Suspended Construction": "suspended", "Decommissioning Completed": "decommissioned",
          "Cancelled Construction": "cancelled", "Never Commissioned": "cancelled"}
ORDER = ["active", "construction", "suspended", "planned", "inactive", "decommissioned", "cancelled"]
WORDS = {"active": "operating", "construction": "under construction", "planned": "planned", "inactive": "shut down",
         "suspended": "suspended", "decommissioned": "decommissioned", "cancelled": "cancelled"}


def main():
    stations = defaultdict(list)
    for r in csv.DictReader(open(ROOT / "data/raw/nuclear/nuclear_power_plants.csv", encoding="utf8")):
        if r["Status"] not in STATUS or not r["Latitude"]:
            continue
        base = re.sub(r"[\s-]+(\d+[A-Z]?|[IVX]+|[A-D])$", "", r["Name"]).strip()
        stations[(base, r["CountryCode"])].append(r)
    out = []
    for (name, cc), rs in stations.items():
        st = Counter(STATUS[r["Status"]] for r in rs)
        status = next(s for s in ORDER if st[s])
        cap = sum(int(r["Capacity"]) for r in rs if r["Capacity"].isdigit() and STATUS[r["Status"]] == "active")
        first = min((r["OperationalFrom"][:4] for r in rs if r["OperationalFrom"]), default="")
        last = max((r["OperationalTo"][:4] for r in rs if r["OperationalTo"]), default="")
        parts = [f"{len(rs)} reactor{'s' if len(rs) > 1 else ''}: " + ", ".join(f"{n} {WORDS[s]}" for s in ORDER if (n := st[s]))]
        if cap:
            parts.append(f"{cap:,} MW operating")
        if first:
            parts.append(f"first power {first}")
        if last and status in ("inactive", "decommissioned"):
            parts.append(f"last shut down {last}")
        out.append({"n": name, "k": "power", "c": cc, "lat": round(sum(float(r["Latitude"]) for r in rs) / len(rs), 3),
                    "lon": round(sum(float(r["Longitude"]) for r in rs) / len(rs), 3), "s": status, "d": "; ".join(parts) + "."})
    hand = json.loads((ROOT / "data/nuclear_weapons_sites.json").read_text())["sites"]
    for n, k, c, lat, lon, s, d in hand:
        out.append({"n": n, "k": k, "c": c, "lat": lat, "lon": lon, "s": s, "d": d + ".", "hand": True})
    (ROOT / "ui/nuclear.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(len(out), "sites;", dict(Counter(o["k"] for o in out)), dict(Counter(o["s"] for o in out)))


if __name__ == "__main__":
    main()
