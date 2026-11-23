"""Prepare the dashboard for serving from the NAS (run while the image is built):
wrap the page in a full HTML document, point it at the locally saved
JavaScript libraries instead of the CDN, and pre-compress the data files.

  python3 server/prepare_site.py SITE_DIR
"""
import gzip, sys
from pathlib import Path

site = Path(sys.argv[1])
page = site / "index.html"
html = page.read_text(encoding="utf8")
swaps = {
    "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js": "vendor/d3.min.js",
    "https://cdn.jsdelivr.net/npm/3d-force-graph@1.79.0/dist/3d-force-graph.min.js": "vendor/3d-force-graph.min.js",
}
for a, b in swaps.items():
    if a not in html:
        sys.exit(f"index.html no longer loads {a}; update server/prepare_site.py")
    html = html.replace(a, b)
if not html.lstrip().lower().startswith("<!doctype"):
    html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n'
            + html + "\n</body>\n</html>\n")
page.write_text(html, encoding="utf8")
n = 0
for f in site.rglob("*.json"):
    f.with_name(f.name + ".gz").write_bytes(gzip.compress(f.read_bytes(), 6))
    n += 1
print(f"prepared {page}; compressed {n} data files")
