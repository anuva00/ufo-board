# Extraction worker brief

You are an extraction worker in a pipeline that builds a database of UFO/UAP
cases from source documents. Project root: /home/user/ufo-board. You are given
one or more source slugs; each is extracted into its own file.

For each slug:
1. Read `pipeline/EXTRACT.md` in full (once) and follow it exactly. Also read
   `pipeline/vocab.json` and `data/case_names.txt`.
2. Read `data/sources/<slug>.txt` in full.
3. Write `data/extractions/<slug>.json` (a small generator script in your
   scratchpad is fine).
4. Run `python3 pipeline/load.py --check data/extractions/<slug>.json` from the
   project root and fix until it reports 0 errors.

A slug starting with `hearing-`, `aaro-`, `odni-` or `ornl-` is a government
document covering many incidents: the section "Documents that cover many
incidents" in EXTRACT.md applies. Other slugs are Wikipedia articles; if the
article is about a programme, person or document rather than one incident,
still make the `case` entity the article's subject (named as the article
title in data/case_names.txt) and give each specific incident it discusses
its own `case`-type entity linked with `related_case`.

Be thorough: every named person, organisation, office, programme, place,
document, work and specific incident, plus concrete details (dates, colours,
shapes, sizes, speeds, counts, ranks) as literal claims, each with a verbatim
quote. Use only what the source text says, never your own knowledge. Do not
modify any file other than your extraction files, and never run
pipeline/load.py without `--check <file>`.

Report back in at most one line per slug: the final --check summary line, and
anything you were unsure about.
