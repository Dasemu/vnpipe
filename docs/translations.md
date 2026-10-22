# Translations

A work can be played in other languages besides its own, from a **published public-domain
translation** of the same text. vnpipe never translates or rewrites the play: every line shown
in another language is copied verbatim from that translation (a Project Gutenberg ebook).
Only the game's own interface strings (menu labels, scene subtitles, credits) are written in
`work.toml` per language.

```
translation ebook ──translate fetch──▶ data/raw/<file>.txt            (gitignored)
                  ──translate build──▶ data/translations/<code>.json  (committed)
                  ──renpy──▶ game/tl/<language>/script.rpy, strings.rpy
                             game/languages.rpy  (the choice: Preferences, first launch)
```

## Mapping (`translate build`)

The ebook (Gutenberg header, footer and licence removed) is cut at each scene's heading and
parsed into **units**: speeches (split at stage directions) and stage directions. The game
side has the same units: each direction line and each run of lines with the same speaker and
speech. The two sequences are aligned (dynamic programming): a speech pairs only with a speech
of the same speaker (two ids of `ensembles` may pair, at a penalty), a direction with a
direction; similar sizes score higher.

Translators split and merge speeches differently, so the units are **grouped**: source units
left between two pairs are shown with the first unpaired game unit of the stretch (else with
the previous pair). Every source unit is shown exactly once, in the translation's order, each
with its own speaker. A game unit with no text of its own shows nothing in that language.

Parsing rules worth knowing (`translate_align.parse_units`):

- an upper-case line followed by a blank line is a speaker label, mapped by `speakers`; an
  indented one only for a known speaker (a chorus inside a song);
- `(...)` blocks, `[Exit` lines (also at the end of a verse line) and italic paragraphs are
  directions; a note under a label becomes `LABEL (note)`; a parenthesis inside a stanza
  ending in punctuation (`(These elements),—`) is verse;
- before the first speech, place and cast lines form one **setting** unit, used only where an
  override puts it; footnote numbers at line ends and illustration markers are dropped.

`translate status` prints coverage per scene (worst first): `paired` and `carry` units show
their own text, `grouped` ones show nothing; `suspects` are groups whose size ratio is far from
the scene's. `translate status --scene ID` prints the alignment side by side. Fix a wrong
pairing with an override that quotes the translation verbatim (see below); the build fails if
the quote does not start exactly one unit. A scene is `complete` when its first unit has text.
The JSON stores hashes of `data/annotated.json` and of the mapping keys of
`[translations.<code>]` (sections, speakers, overrides, ...); `vnpipe renpy` refuses a
mapping built from other ones (names and strings need no rebuild).

## Ren'Py output

Every say statement carries an explicit `id` (its first line id), and each translation writes
`translate <language> <id>:` for all of them: the first box of a game unit with text shows the
translated units (split into boxes like the original: stanzas, at most 4 lines in ADV and 8 in
NVL, long prose paragraphs cut at sentence ends), every other box is `pass`. The voice clips
are shared: each clip of the group, in order, starts on the next translated say box of the same
speaker (or, if none is left, on the next say box), and `voice sustain` carries it over that
speaker's next boxes and directions. Every clip is placed; a group with more clips than
translated say boxes is an error.

Names, title cards, the scene list, About and the art-style labels go to `translate <language>
strings:`; equal original strings that translate differently get `{#scene}` tags. The credits
are a list per language (`vn_credits_tl`). `vnpipe renpy` fails if the JSON is missing or was
built from another `data/annotated.json`.

The language choice (`game/languages.rpy`): the work's own language is the default; the player
picks another in Preferences ("Language") or in the first-launch picker. The choice is stored
in `persistent.vn_language` and copied to `config.language`, so a stale preference never
switches the language and loading a save keeps it. `RENPY_LANGUAGE=<language>` forces one
(e.g. for the playthrough test).

## Configuration

```toml
[translations.en]
language = "english"                 # Ren'Py language name
label = "English"                    # shown in the language choice
text_url = "https://www.gutenberg.org/cache/epub/NNN/pgNNN.txt"
text_file = "data/raw/pgNNN.txt"     # cache (gitignored)
gutenberg_id = NNN                   # credited as the source
title = "Faust"                      # [work] fields in this language
subtitle = "First Part"
translator = "..."
end_text = "To be continued…"
voice_credits = ["..."]              # [voice].credits in this language
ignore_lines = ["..."]               # exact lines of the ebook that are not play text
ensembles = ["chorus"]               # default: [voice].ensembles

[translations.en.speakers]           # speaker label as printed -> cast id
"MEPHISTOPHELES" = "mephisto"

[translations.en.scene_speakers.<scene id>]   # per-scene overrides of `speakers`; a list
"THE FIRST" = ["artisans", "servant_girls"]  # gives one cast id per occurrence, in order

[translations.en.direction_labels]   # labels printed like a speaker but read as a direction;
"FAUST AND WAGNER" = ""              # the text after is spoken by this cast id ("" = none)

[translations.en.names]              # cast id -> name (every named cast member)
mephisto = "Mephistopheles"

[translations.en.strings]            # other strings of the work (art-style labels)
"Pictórico" = "Painterly"

[[translations.en.sections]]         # one per scene, in play order
scene = "noche"
heading = "NIGHT"                    # heading line in the ebook, searched in order
title = "NIGHT"                      # optional: shown heading (default: `heading`)
subtitle = "Faust’s study · Night"   # card subtitle
# default_speaker = "poet_voice"     # a section without speaker labels is one speech

[[translations.en.overrides]]        # forced pair: game unit (first line id) <-> source unit
scene = "dia_nebuloso"
game = "dia_nebuloso_0001"
source = "A FIELD"                   # verbatim start of the source unit (markup removed)
```
