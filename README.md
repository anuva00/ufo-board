# UFO evidence board

A knowledge graph of well-documented UFO cases with an evidence-board
interface: cases are pinned cards, the people, places, organisations and
details they share are sticky notes, and red string joins them. Every string
leads back to a verbatim quote in a stored source.

A string means two sources mention the same thing. It is a lead to check, not
a finding.

## Pipeline

| Step | Command | What it does |
|---|---|---|
| Collect | `python3 pipeline/fetch.py` | Fetches the source text for each case in `data/cases.json` into `data/sources/` (currently English Wikipedia, pinned to a revision) |
| Extract | one agent per case, following `pipeline/EXTRACT.md` | Writes `data/extractions/<slug>.json`: entities and quoted claims |
| Load | `python3 pipeline/load.py` | Validates every extraction and builds `data/ufo.db` (SQLite). A claim whose quote is not found verbatim in the source is rejected |
| Resolve | `python3 pipeline/resolve.py` | Merges entities that are the same real-world thing across cases. Weak matches go to `data/merge_candidates.txt`; confirmed ones go in `data/merges.json` |
| Score and export | `python3 pipeline/export.py` | Ranks links by rarity and writes `ui/data.json` |
| Browse | serve `ui/` (for example `python3 -m http.server -d ui`) | The board |

`data/ufo.db` is rebuilt from the JSON files, so it is not committed.

## Data model

Facts are claims, not columns: `subject -> predicate -> object or value`, each
with the quote that supports it and its character offset in the source. Any new
kind of detail (the colour of a witness's car) fits without changing the schema.
See `pipeline/schema.sql`.

## Scoring

A thing shared by `df` of `N` cases has weight `ln(N/df) / ln(N/2)`: 1.0 when
only two cases share it, 0 when every case does. A pair of cases scores the sum
of the weights of everything they share, plus 1 if one names the other.

## Known limits

- One source per case so far, and it is a tertiary one. Links reflect what
  Wikipedia editors chose to mention.
- Extraction is done by language-model agents. Quotes are machine-verified;
  the reading of each quote (who was a witness, who was a sceptic) is not.
- Name matching is conservative. The same person spelled two ways stays
  split until confirmed in `data/merges.json`.

Source text is from Wikipedia contributors, CC BY-SA 4.0.
