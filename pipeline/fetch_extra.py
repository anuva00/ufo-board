"""Download the extra bulk sources into data/raw/ (all openly licensed):

  UAPDrop open dataset (CC BY 4.0)  -> data/raw/uapdrop/sightings.csv   (we use its Larry Hatch *U* records)
  Transport Canada CADORS (OGL-Canada) -> data/raw/cadors/*.csv          (pilot and controller UFO reports)
  uapinsight PURSUE dataset (CC BY 4.0) -> data/raw/pursue/documents.csv  (the 2026 US release documents)
"""
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data/raw"
FILES = {
    "uapdrop/sightings.csv": "https://www.uapdrop.com/rest/v1/public/sightings.csv",
    "cadors/occurrence_information.csv": "https://opendatatc.tc.canada.ca/CADORS_Occurrence_Information.csv",
    "cadors/occurrence_events.csv": "https://opendatatc.tc.canada.ca/CADORS_Occurrence_Event_Information.csv",
    "pursue/documents.csv": "https://raw.githubusercontent.com/tomasstankovic/uapinsight-pursue-dataset/main/data/documents.csv",
}

for rel, url in FILES.items():
    path = RAW / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ufo-board/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r:
        path.write_bytes(r.read())
    print(f"{path.stat().st_size // 1024:8d} KB  {rel}")
