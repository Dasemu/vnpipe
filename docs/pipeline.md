# Pipeline overview

`vnpipe` turns a public-domain text from Project Gutenberg into a Ren'Py visual novel.
A *work* (one text) is a directory with a `work.toml` (see [work-config.md](work-config.md));
all paths below are relative to the work root. Adding a work needs a new `work.toml` plus art,
no code changes.

```
Gutenberg .txt ──fetch──▶ data/raw/            (cached, gitignored)
              ──parse──▶ data/play.txt         (header/footer stripped, scope cut)
                         data/parsed.json      (scenes, speakers, verse lines, directions)
              ──chunk──▶ annotations/chunks/*.json   (LLM work units)
   LLM sub-agents ─────▶ annotations/cache/*.json    (one file per chunk, keyed by hash)
              ──merge──▶ data/annotated.json   (schema-validated, complete)
art (any generator) ──▶ assets/assets.json + images (manifest, sha256, status)
              ──renpy──▶ vn/                   (generated Ren'Py project)
```

Every stage is a `vnpipe` subcommand, deterministic given its inputs, and idempotent
(re-running produces byte-identical output). The only non-deterministic steps, LLM annotation
and image generation, write to caches keyed by content hash, so they are resumable and never
redone unless their input changes.

## Stages

| Stage | Commands | Module |
|---|---|---|
| Text | `fetch`, `parse` | `text.py` |
| Annotation | `annotate chunk/status/prompt/merge` ([details](annotation.md)) | `annotate.py` |
| Assets | `assets prompt/import/process/status/placeholders/sheet` | `assets.py` |
| Ren'Py | `renpy [--from-parsed] [--with-test] [--out DIR]` | `renpy_gen.py` |

`--work DIR` (before the subcommand) selects the work root; default is the current directory.

## Source layout

| Path | Purpose |
|---|---|
| `src/vnpipe/` | Work-agnostic pipeline code |
| `src/vnpipe/schemas/` | JSON Schemas: parsed text, annotation, annotated text, asset manifest |
| `src/vnpipe/prompts/annotate.md` | Annotator instructions (its hash versions the cache) |
| `src/vnpipe/renpy_templates/` | Default Ren'Py GUI, screens and fonts |
| `tests/` | Unit tests on `tests/fixtures/` (a mini work); the Ren'Py SDK tests need `RENPY_SDK` |

## Asset manifest

`assets/assets.json` lists backgrounds and sprites with a status (`processed`, `approved`,
`placeholder`, ...). Anything missing or unusable is replaced by a generated placeholder, so
the game always runs.
