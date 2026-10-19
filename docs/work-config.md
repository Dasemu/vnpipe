# Work configuration (`work.toml`)

A work is a directory containing `work.toml`, plus the folders the pipeline fills:
`data/`, `annotations/`, `assets/` and the generated `vn/`. A complete example is the
`faust-vn` repo. Keys:

| Table | Keys |
|---|---|
| `[work]` | `id`, `title`, `subtitle`, `author`, `translator`, `language` (`es`/`en`: About, menu and credits texts), `lyric_voice` (cast id for verse nobody speaks; default `poet_voice`), `end_text`, `menu_bg` (legacy alias of `[ui].main_menu_bg`). |
| `[ui]` | `main_menu_bg` (background asset id of the main menu and, darker, the game menu; default: `[work].menu_bg`, else the first staged `bg`); `language` (Ren'Py language name set as `config.default_language`, e.g. `spanish`; its strings come from `<work>/tl/<language>/*.rpy`, copied into `game/tl/`). |
| `[source]` | `gutenberg_id`, `url`, `raw_file` (cache path, relative to the work root, gitignored). |
| `[scope]` | `start_marker`, `end_marker` (whole stripped lines), `end_occurrence` (1-based, searched after the start; the end marker itself is excluded). |
| `[speakers]` | Speaker heading as printed (upper case, without its italic note) → cast id. Several headings may share one id (anonymous crowd speakers). A key with punctuation or markup (`"CORO, TUTTI"`) is matched against the whole printed line first. An unknown speaker is a parse error. |
| `[[scenes]]` | `heading` (as printed), `id`, `location`, `time_of_day`, optional `occurrence`, `format` (below) and `subtitle` (a short, already localized place/time line shown under the heading on the title card; absent = heading only). |
| `[parser]` | `direction_headings`: whole lines in capitals that are stage directions or section titles (`"ENTRA FAUSTO"`), not speakers. Each becomes a `direction` line once the scene's first speech has started (before it, the line is treated as cast/setting). |
| `[cast.<id>]` | `name` (shown in the dialogue box), `color`, `sprite` (default `true`; `false` = never drawn), `expressions` (annotation emotion → sprite expression; needs `neutral`). |
| `[staging.<scene id>]` | `bg` (background shown after the title card), `mode` (`adv` or `nvl`), `cast = { <char> = "left\|center\|right" }` pins sprite characters for the whole scene, `music` (see Audio; ignores the annotation moods for this scene). |
| `[[cues]]` | `at` (line id; fires right before it) plus any of `bg`, `transition` (`dissolve`, `fade`, `flash`, `slow`), `tint` (`none`, `dark`, `red`), `mode`, `hide_all`, `shake`, `move = { <char> = "<pos>" }`, `music` (see Audio; applies from that line until the next cue or the scene end). |
| `[[overrides]]` | `from`, `to` (inclusive line ids in one scene), `emotion`: forces the emotion before expression mapping. |

### Text structure

`parse` cuts the scope, then drops noise: illustration markers (also multi-line), footnote
definitions (`  [12] text` plus indented continuation lines) and footnote markers (`word[12]`).
Then, per line:

- A scene heading starts a scene. A heading printed more than once needs one `[[scenes]]`
  entry per appearance, each with `occurrence` (1-based, in order of appearance; default 1).
  Two entries with the same heading and occurrence, or the same `id`, are config errors; an
  appearance with no entry is a parse error.
- Before the first speech, unindented blocks are `cast` lines (cast list, setting in capitals;
  an italic block is a `direction`). A speaker heading directly followed by an unindented line
  belongs to such a block.
- A speaker heading is `NAME`, optionally followed by an italic note (`, _a_ FAUSTO`,
  ` (_solo_)`, notes wrapped over two lines); the note is kept in `speaker_note`. It must be
  followed by a blank line and verse, otherwise it is not a heading.
- `(...)` blocks, unindented `_italic_` blocks and `direction_headings` are `direction` lines.
- `format = "prose"` on a scene: unindented paragraphs (consecutive lines joined) become one
  `verse` line each (`indent` 0, `stanza` = paragraph index in the speech). Unindented text
  in a verse scene is a parse error.

Anything else is a `ParseError` with the raw line number.

Emotions: `neutral`, `joyful`, `melancholic`, `solemn`, `ironic`, `angry`, `fearful`, `ecstatic`.
Unknown line ids, characters, positions or emotions are errors at generation. See
[adaptation-rules.md](adaptation-rules.md) for how these choices are meant to be used.

Asset ids (`bg_*`, sprite names) and the art styles (`styles`, `default_style`, each with a
label and prompts) are defined in the work's `assets/assets.json` (manifest v2, see
[running.md](running.md)); a missing image is replaced by a placeholder. Style names and
labels are work data: nothing about them is hard-coded in the pipeline.

### Audio

`vnpipe renpy` copies `<work>/audio/**/*.ogg` into `game/audio/` (subdirectories kept; other
files such as `credits.toml` are not copied). Music is chosen per line from the annotation
mood (`ann.music`), unless overridden:

- `audio/<mood>/*.ogg`: the mood plays its tracks as a looping playlist (sorted by path).
  A lone `audio/<mood>.ogg` is a one-track mood. A mood without audio keeps what plays.
- `[staging.<scene>].music` and the cue key `music` take `"<mood>"`, `"track:<path under
  audio/>"` (a single looping file, e.g. `track:scenes/x.ogg`) or `"none"` (silence).
  A scene override replaces the annotation moods for that scene; a cue switches from its line on.
  `"auto"` (cues only) hands control back to the annotation moods.
- A track that is already playing is never restarted (`if_changed`); the first music statement
  of each scene is always emitted so a scene entered from the scene select has music.
- Unknown `track:` paths, and moods with no audio when the work has audio, are errors.

### Menus, credits and reading comfort

- **Main menu**: the `[ui].main_menu_bg` background, darkened, with `[work].title` large and
  `[work].subtitle` below it; entries Start, Scenes, Load, Preferences, About, Credits, Help, Quit.
  The game menu uses the same background, darker.
- **Scenes** (main menu only): every scene the player has reached (a persistent set updated
  when the scene starts), listed by heading; selecting one starts the game from that scene.
  Unreached scenes show as an em dash. A heading shared by several scenes is told apart by
  the scene's `subtitle` (else its number).
- **Credits**: text author/translator/Gutenberg source from `[work]` and `[source]`; the art
  line from the manifest's renditions (non-placeholder `source`, e.g. "ChatGPT (OpenAI)");
  the music from `<work>/audio/credits.toml` (`[[track]]` with `file`, `attribution`, optional
  `attribution_<language>` used instead when it matches `[work].language`; only tracks whose file
  exists). Attributions are printed verbatim. Labels follow `[work].language`.
- Defaults: `preferences.text_cps = 40`, `afm_time = 15`, `window auto`, a blinking
  click-to-continue arrow. Verse boxes stay at 4 lines; NVL keeps stanza pages.
