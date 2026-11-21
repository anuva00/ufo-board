# Extraction guide

You turn one source document about a UFO case into structured, quoted claims.
The output feeds an evidence board that links cases through shared people,
places, organisations and details, so two things matter above all:

1. **Faithfulness.** Record only what the source text says. Do not add facts
   from memory, even if you are sure of them. Every claim carries a `quote`
   copied character-for-character from the source; the loader rejects any
   claim whose quote is not found verbatim.
2. **Coverage of named things.** A link between two cases exists only if both
   extractions captured the same person, place, organisation or detail. Capture
   every named person, organisation, place, programme, document, book/film and
   other UFO case the text mentions, not only the main ones. Minor names are
   where unexpected links come from.

## Input and output

- Read `data/sources/<slug>.txt` in full (use the `.txt`, not the `.json`).
- Write `data/extractions/<slug>.json`.
- Run `python3 pipeline/load.py --check data/extractions/<slug>.json` and fix
  every ERROR until it reports 0 errors. Warnings about predicates are fine.

```json
{
  "slug": "<slug>",
  "entities": [
    {"key": "case", "type": "case", "name": "<case name>", "description": "one sentence"},
    {"key": "halt", "type": "person", "name": "Charles I. Halt",
     "aliases": ["Lt. Col. Charles Halt"], "description": "USAF deputy base commander"}
  ],
  "claims": [
    {"s": "halt", "p": "witness", "o": "case", "quote": "<verbatim text>"},
    {"s": "halt", "p": "rank", "v": "Lieutenant Colonel", "quote": "<verbatim text>"}
  ]
}
```

A claim is `s` (subject entity key), `p` (predicate, snake_case) and either
`o` (another entity key) or `v` (a literal value), plus `quote`.

## Entities

`key` is any short unique id inside this file. The case itself must have key
`case`. `name` is the fullest form the source gives, without rank or title
(put "Lt. Col. Halt", "Dr. Hynek", abbreviations and alternate spellings in
`aliases`). Types:

| type | use for |
|---|---|
| `case` | this case (key `case`), and any *other* UFO incident the text mentions (own key, link with `related_case`) |
| `person` | any named individual |
| `organization` | agencies, military branches and units, companies, newspapers, UFO groups |
| `place` | towns, regions, countries, military bases, airports, landmarks |
| `craft` | the reported object(s), e.g. "triangular craft seen 26 December" |
| `vehicle` | witnesses' or pursuers' aircraft, cars, ships |
| `document` | memos, reports, letters, recordings, photographs, films as evidence |
| `program` | official studies and projects (Project Blue Book, Condon Committee, AATIP) |
| `work` | books, TV programmes, films, articles about the case |
| `feature` | a recurring characteristic, from the fixed list below |
| `explanation` | a conventional explanation someone proposed, from the fixed list below |

Use the common full name for well-known things ("United States Air Force", not
"the Air Force"; "Project Blue Book"), so the same thing matches across cases.

`feature` and `explanation` names must come from `pipeline/vocab.json`
(`features`, `explanations`). If nothing fits, use `other:<short text>`.
Add every feature the text supports; each needs its own quoted `has_feature`
claim. Add an explanation only if the source says someone proposed it.

## Claims

Every entity needs at least one claim. Aim for these, in order:

1. **Link each entity to the case**: subject is the entity, object is `case`.
   - people: `witness`, `investigator`, `official_involved`, `skeptic`,
     `proponent`, `journalist`, `hypnotist`, `commentator`, `other_involved`
   - `occurred_at`, `near`, `related_place` (places); `involved_org`, `investigated_by`
     (organisations, programmes); `reported_object` (craft);
     `involved_vehicle`; `key_document`; `depicted_in` (works);
     `has_feature`; `explained_as`; `related_case`
2. **Link entities to each other** where the text does: `member_of`,
   `employed_by`, `stationed_at`, `located_in`, `authored`, `commanded`,
   `relative_of`, `colleague_of`, `interviewed`, `proposed` (person →
   explanation), `disputed`, `described` (person → craft), `piloted`, `owned`,
   `part_of`.
3. **Literal details** with `v`. On the case: `date_start` and `date_end`
   (`YYYY-MM-DD`, or `YYYY-MM` / `YYYY` if that is all the text gives),
   `time_of_day`, `country`, `witness_count`, `duration`. On any entity, any
   concrete detail the text states, with a descriptive snake_case predicate:
   `rank`, `occupation`, `age`, `color`, `shape`, `size`, `speed`, `altitude`,
   `sound`, `statement`, `later_claim`, and so on. Small physical details
   (colours, counts, sizes, times) are wanted.

## Rules for the awkward cases

- **Direction.** Case links always run entity → `case`; read the predicate as
  the entity's role. `{"s": "bluebook", "p": "investigated_by", "o": "case"}`
  means the case was investigated by Blue Book. `{"s": "f_radar", "p":
  "has_feature", "o": "case"}` means the case has that feature.
- **Unnamed people** ("a crew chief", "tower operators") do not get entities.
  Record what they did as a literal on the case or on a related entity.
- **Partial names.** A relative given by first name only takes the family
  surname if the text makes it obvious ("his wife Peggy" → "Peggy Mantell").
  A bare surname with no other identification stays as written. Never correct
  a spelling or expand a name from memory.
- **Well-known institutions and places** may be expanded to their standard
  name when the text's meaning is unambiguous ("the Army" in a US case →
  "United States Army"), with the text's wording as an alias. US towns are
  "Town, State"; other towns "Town, Country"; states and countries plain.
- **Commentators.** Someone who only wrote or spoke about the case later uses
  `commentator`. A place that matters but is neither the location nor nearby
  uses `related_place`.
- **Skip navigation and bibliography.** In "See also", "References", "Further
  reading" and "External links", keep only items that name another specific
  UFO case (`related_case`) or a book/film about this case (`work`). Skip list
  articles ("List of reported UFO sightings"), publishers, and websites.
- **Skip pure comparisons and fiction.** "As big as a B-29" does not make the
  B-29 an entity; record the comparison as a literal.
- **Rumours and retracted items.** Add a `feature` only when the source says
  witnesses or investigators reported it. Rumours go in a `rumor` literal.
  An explanation that was proposed and later withdrawn still counts.
- **`witness_count`** only when the text states a number. `time_of_day` is the
  clock time of the first report if given, otherwise the text's own word.
- **Literal values** may be lightly normalised ("Gen." → "General"); the quote
  may not. One quote may support several claims, and a predicate may repeat.
- Prefer the recommended predicates; invent a new one only when none fits.

## Quotes

- Copy from the source exactly: same words, same punctuation. 8 to 400
  characters (the checker ignores differences in whitespace, curly versus
  straight quotes, and dash style). Do not join separated passages with "..." and do not fix typos.
- Choose the passage that actually supports the claim, so that a reader who
  sees only the quote would agree with it.
- A person who is only named in passing still gets a claim, with the sentence
  naming them as the quote.
- The source reports what people said; record it as such. "X claimed he saw a
  landed craft" supports `X witness case`, and a `statement` literal, not a
  claim that a craft landed.
