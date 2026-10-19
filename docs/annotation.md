# Annotation stage

Turns `data/parsed.json` into `data/annotated.json`: every parsed line gets an `ann` block
(emotion, who is on stage and where, music). Judgement calls are made by LLM sub-agents;
`vnpipe annotate` only prepares their input, checks their output and merges it.

```
parsed.json ──chunk──▶ annotations/chunks/<chunk_id>.json   (annotator input)
  sub-agent ──────────▶ annotations/cache/<chunk_id>.json   (annotator output)
              ──merge──▶ data/annotated.json
```

## Chunks

- Id `<scene_id>_cNN`. A chunk never crosses a scene and holds at most 60 lines. Splits
  prefer speech boundaries, then stanza boundaries, and only then cut mid-stanza.
- `input_sha256` is the SHA-256 of the canonical JSON of the chunk without that field. It
  includes `prompt_version` (SHA-256 of `src/vnpipe/prompts/annotate.md`), so editing the
  prompt, the text, `work.toml` scenes/cast or the schema enums invalidates the cache.
- Contents: `scene`, `allowed` (speakers, characters, locations, emotions, positions,
  music), `context_before` (up to 6 preceding lines, read-only) and `lines`, each with a
  `defaults` block that is deterministic and must be copied verbatim:
  - verse with a speaker: `dialogue` / that speaker;
  - verse without speaker: `narration` / `[work].lyric_voice` from `work.toml`;
  - direction and cast list: `stage_direction` / `narrator`;
  - `location` and `time_of_day` from the scene config.
- `allowed.characters` are the cast ids except `narrator` and the lyric voice. Cast members
  with `sprite = false` are valid on stage; the Ren'Py generator filters them.

## Cache

One file per chunk, `annotation.schema.json`: `chunk_id`, `input_sha256` (copied from the
chunk) and `annotations` (one per line, in order). Cache files are never deleted by the
tool; a cache whose hash no longer matches is simply `stale` and gets redone.

## Commands

| Command | Effect |
|---|---|
| `vnpipe annotate chunk` | Write chunk files, remove chunk files that no longer exist. |
| `vnpipe annotate status [id ...]` | Per chunk `ok`, `missing`, `stale` or `invalid` (with reason) plus a summary. Always exits 0. |
| `vnpipe annotate prompt <id>` | Print the full annotator instructions for a chunk (absolute paths filled in). |
| `vnpipe annotate merge [--fallback]` | Validate every cache and write `data/annotated.json`. |

A cache is `ok` when it validates against the schema, carries the chunk's hash, covers exactly
the chunk's line ids in order, keeps `speaker`/`kind`/`location`/`time_of_day` equal to the
defaults, and uses only allowed characters (each at most once per line). `stale` means the
hash differs; everything else wrong is `invalid`.

`merge` without `--fallback` prints every problem and exits 1 without writing anything. With
`--fallback`, each non-ok chunk gets deterministic annotations (defaults, emotion `neutral`,
music `null`, on stage: the speakers seen so far in the scene who have sprites, placed
left/right/center in order of appearance), a warning is printed, and the chunk ids are listed
in `fallback_chunks`. Output is byte-identical across runs.

## Running the annotators

1. `vnpipe annotate chunk`
2. For each chunk that is not `ok` in `vnpipe annotate status`, start one cheap-model
   sub-agent whose whole instruction is the output of `vnpipe annotate prompt <chunk_id>`.
   Chunks are independent, so run them in parallel.
3. `vnpipe annotate status` until everything is `ok`; relaunch only the failing chunks.
4. `vnpipe annotate merge`.

## Resumability

Re-running is safe: only `missing`, `stale` and `invalid` chunks need work. After changing the
prompt every chunk becomes stale; after changing the text only the affected chunks do. Always
run `vnpipe annotate chunk` after changing `parsed.json`, `work.toml` or the prompt, before
starting annotators (`status` warns when chunk files are out of date).
