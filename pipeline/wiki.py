"""Minimal, polite Wikipedia API client (stdlib only)."""
import json, time, urllib.error, urllib.parse, urllib.request

API = "https://en.wikipedia.org/w/api.php"
UA = "ufo-board-research/0.1 (evidence-board research tool)"


def api(params, retries=5):
    params = {"format": "json", "formatversion": 2, "maxlag": 5, **params}
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1)
                time.sleep(min(wait, 60))
                continue
            raise
