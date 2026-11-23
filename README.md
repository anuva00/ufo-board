# UFO evidence board

A dashboard for exploring UFO/UAP records in two layers:

- **Wide layer:** about 97,000 individual sighting reports from five datasets
  (NUFORC, US Air Force Project Blue Book case files at the National Archives,
  French GEIPAN, UK Ministry of Defence, US FAA), normalised to date, place,
  coordinates, shape and summary. No language model is involved.
- **Deep layer:** case files and government documents (Wikipedia articles,
  congressional hearing transcripts, AARO and ODNI reports) read by extraction
  agents into entities and claims. Every claim carries a verbatim quote that
  the loader checks against the stored source text.

The dashboard (`ui/index.html`) shows a world map, a timeline with a year
brush, a 3D network of names shared between case files, and the quotes and
sources behind every link.

A link means two sources mention the same thing. It is a lead to check, not a
finding.

## Pipeline

| Step | Command | What it does |
|---|---|---|
| Collect cases | `python3 pipeline/fetch.py` | Wikipedia text for each case in `data/cases.json`, pinned to a revision |
| Collect archives | `python3 pipeline/fetch_nara.py` | Record descriptions and extracted text from the US National Archives UAP collection |
| Collect documents | `python3 pipeline/add_sources.py` | Hearing transcripts (govinfo) and agency reports as source files, split into parts |
| Extract | one agent per source, following `pipeline/EXTRACT.md` | `data/extractions/<slug>.json`: entities and quoted claims |
| Load | `python3 pipeline/load.py` | Validates every extraction and builds `data/ufo.db`; a claim whose quote is not found verbatim is rejected |
| Resolve | `python3 pipeline/resolve.py` | Merges entities that are the same thing across sources; reviewed decisions live in `data/merges.json` |
| Bulk layer | `python3 pipeline/reports.py` | Builds `ui/reports.json` and `ui/rep/*.json` from `data/raw/` |
| Export | `cd pipeline && python3 export.py` | Scores links and writes `ui/data.json` |
| Nuclear sites | `python3 pipeline/nuclear.py` | Builds `ui/nuclear.json`: power stations from GeoNuclearData, weapons and test sites from the hand-entered `data/nuclear_weapons_sites.json` |
| Map outlines | `node pipeline/build_geo.js` | Builds `ui/geo.json` from the world-atlas and us-atlas npm packages |

`data/raw/` (downloads) and `data/ufo.db` are rebuilt by the scripts and are
not committed. Other raw inputs: the NUFORC snapshot from
`planetsig/ufo-reports` on GitHub, the GEIPAN case export from geipan.fr, and
the yearly UK MoD report PDFs from gov.uk.

## Scoring

A thing shared by `df` of `N` case files has weight `ln(N/df) / ln(N/2)`: 1.0
when only two files share it, 0 when every file does. A pair of files scores
the sum of the weights of everything they share, plus 1 if one names the other.

## Known limits

- Coordinates are town-level at best. A report whose place could not be
  matched to a town, air base or stated coordinates is counted in the timeline
  but not mapped: about 2,100 of the 10,276 Blue Book files (titles that give
  only a country, a state or an illegible place) and a third of the UK MoD
  reports. Another 346 Blue Book files have no legible date and are left out.
- The NUFORC snapshot ends in 2014; there is no bulk civilian data after that.
- Extraction is done by language-model agents. Quotes are machine-verified; the
  reading of each quote (who was a witness, who was a sceptic) is not.
- Name matching is conservative and only partly reviewed.
- Some sites refuse automated access from cloud servers (war.gov, aaro.mil,
  dni.gov, YouTube), so the PURSUE release files and video transcripts are not
  included. AARO and ODNI documents come from the National Archives copies.

Wikipedia text is CC BY-SA 4.0. US government documents are public domain.
GEIPAN data is published by CNES; UK MoD reports are under the Open Government
Licence. NUFORC reports are the property of the National UFO Reporting Center.

## Run it on a NAS with Docker

```bash
git clone https://github.com/anuva00/ufo-board.git
cd ufo-board
docker compose up -d --build
```

Then open `http://<address-of-the-NAS>:8080`. To use another port, change
the left-hand number in `docker-compose.yml`. To update later, run
`git pull` and `docker compose up -d --build` again; rebuilding also picks up
the latest yt-dlp, which YouTube changes often make necessary.

The container serves the dashboard and a small Python service
(`server/app.py`). State is kept in `./nas-data` next to the compose file, so
it survives rebuilds.

### Sources: following YouTube channels

The Sources tab lists subscribed YouTube channels, playlists or single
videos. Paste a link and the NAS:

1. lists the videos (yt-dlp), and checks again every `CHECK_HOURS` (6);
2. saves the captions YouTube already has for each video, a few seconds
   apart (youtube-transcript-api). No transcription model and no paid
   service are used; videos without captions are listed but skipped;
3. finds every name the board knows in each transcript, by exact wording
   after lower-casing, and shows them on the video and in each person's,
   programme's and case's panel, with a link to the moment in the video.

If YouTube starts refusing requests from the connection, the service pauses
for six hours and says so in the tab. "Check now" retries straight away.

Limits: auto-captions mishear names, and a surname said on its own is not
counted, so mentions are undercounted. Transcripts stay in `nas-data` and are
not committed. Anyone on the local network can add or remove sources.

