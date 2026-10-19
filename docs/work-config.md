# Work configuration (`work.toml`)

A work is a directory containing `work.toml`, plus the folders the pipeline fills:
`data/`, `annotations/`, `assets/` and the generated `vn/`. A complete example is the
`faust-vn` repo. Keys:

| Table | Keys |
|---|---|
| `[work]` | `id`, `title`, `subtitle`, `author`, `translator`, `language` (`es`/`en`, used for the About text), `lyric_voice` (cast id for verse nobody speaks; default `poet_voice`), `end_text`, `menu_bg` (background asset id for the main menu; default: first staged `bg`). |
| `[source]` | `gutenberg_id`, `url`, `raw_file` (cache path, relative to the work root, gitignored). |
| `[scope]` | `start_marker`, `end_marker` (whole stripped lines), `end_occurrence` (1-based, searched after the start; the end marker itself is excluded). |
| `[speakers]` | Speaker heading as printed (upper case) → cast id. An unknown speaker is a parse error. |
| `[[scenes]]` | `heading` (as printed), `id`, `location`, `time_of_day`. |
| `[cast.<id>]` | `name` (shown in the dialogue box), `color`, `sprite` (default `true`; `false` = never drawn), `expressions` (annotation emotion → sprite expression; needs `neutral`). |
| `[staging.<scene id>]` | `bg` (background shown after the title card), `mode` (`adv` or `nvl`), `cast = { <char> = "left\|center\|right" }` pins sprite characters for the whole scene. |
| `[[cues]]` | `at` (line id; fires right before it) plus any of `bg`, `transition` (`dissolve`, `fade`, `flash`, `slow`), `tint` (`none`, `dark`, `red`), `mode`, `hide_all`, `shake`, `move = { <char> = "<pos>" }`. |
| `[[overrides]]` | `from`, `to` (inclusive line ids in one scene), `emotion`: forces the emotion before expression mapping. |

Emotions: `neutral`, `joyful`, `melancholic`, `solemn`, `ironic`, `angry`, `fearful`, `ecstatic`.
Unknown line ids, characters, positions or emotions are errors at generation. See
[adaptation-rules.md](adaptation-rules.md) for how these choices are meant to be used.

Asset ids (`bg_*`, sprite names) are defined in the work's `assets/assets.json`; a missing
image is replaced by a placeholder.
