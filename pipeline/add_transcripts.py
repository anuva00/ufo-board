"""Turn saved YouTube transcripts (data/youtube/<id>.json) into source files
for extraction: data/sources/yt-<id>[-pN].json and .txt, split into parts.

  python3 pipeline/add_transcripts.py            every transcript
  python3 pipeline/add_transcripts.py ID [ID..]  only these videos
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from add_sources import split

ROOT = Path(__file__).resolve().parent.parent
YT, SRC = ROOT / "data/youtube", ROOT / "data/sources"


def main():
    want = set(sys.argv[1:])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for f in sorted(YT.glob("*.json")):
        d = json.loads(f.read_text())
        if want and d["id"] not in want:
            continue
        # one caption line per row keeps quotes short and checkable; captions have no punctuation to rely on
        text = "\n".join(re.sub(r"\s+", " ", t).strip() for _, t in d["segments"] if t.strip())
        parts = split(text)
        slug = "yt-" + re.sub(r"[^A-Za-z0-9]+", "-", d["id"]).strip("-").lower()
        name = f"American Alchemy: {d['title']}"
        for i, part in enumerate(parts, 1):
            s = slug if len(parts) == 1 else f"{slug}-p{i}"
            doc = {"slug": s, "title": name if len(parts) == 1 else f"{name} (part {i} of {len(parts)})", "url": d["url"],
                   "revision_id": None, "fetched_at": now, "license": "Transcript of a YouTube video; copyright the channel",
                   "lat": None, "lon": None, "kind": "interview", "publisher": "American Alchemy (YouTube)", "date": None,
                   "case_slug": slug, "case_name": name, "case_kind": "document", "text": part}
            (SRC / f"{s}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
            (SRC / f"{s}.txt").write_text(part)
        print(f"{len(text):8d} chars, {len(parts)} part(s)  {slug}  {d['title'][:60]}")


if __name__ == "__main__":
    main()
