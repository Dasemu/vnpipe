# Annotation stage

Turns `data/parsed.json` into `data/annotated.json` (every line gets an `ann` block: speaker,
kind, emotion, location, time of day, who is on stage and where, music). Most of it is
deterministic; a cheap LLM (Haiku) decides only what needs judgement and answers in a few
short records per speech.

```
parsed.json ──chunks──▶ rendered text ──runner──▶ annotations/cache/<chunk>.txt
                                      ──merge───▶ data/annotated.json
```

| Decided by | Fields |
|---|---|
| Code, from `parsed.json` + `work.toml` | `speaker` (verse with speaker: that speaker; unattributed verse: `[work].lyric_voice`; directions and cast lists: `narrator`), `kind` (`dialogue`/`narration`/`stage_direction`), `location`, `time_of_day` (scene config), stage positions |
| LLM | emotion per speech (and changes inside long speeches), music mood changes, entrances/exits of sprite characters |

## Chunks and input

A chunk is whole speeches, at most 300 lines, never crossing a scene (a scene over the limit
splits on speech, then stanza, boundaries). Ids are `<scene>_cNN`. The input is rendered
compactly (numbers are the 4-digit line index in the scene id, `S` the speech index):

```
SCENE noche "DE NOCHE" | study | night | sprite cast: faust (Fausto), wagner (Wagner) | on stage at start: faust* | part 1/2
(D0001 En un aposento gótico ...)
[S0 FAUSTO]
0002 Filosofía, ¡ay, Dios!, Jurisprudencia,
```

"Sprite cast" is the sprite characters who speak in the scene plus the pinned ones. "On stage
at start" is the pinned cast (`*`, from `[staging.<scene>].cast`) plus the sprite characters
who spoke in earlier chunks of the scene; it is computed from the text, not from the model's
earlier answers, so chunks are independent and can run in parallel. The system prompt
(`src/vnpipe/prompts/annotate.md`) holds the format and the emotion/music vocabularies with
guidance.

## Output records

Plain text, one record per line, parsed strictly:

| Record | Meaning |
|---|---|
| `S <speech> <emotion>` | Required for every speech with verse in the chunk |
| `L <line> <emotion>` | Emotion changes from that spoken line on, inside its speech |
| `M <line> <music>` | Music from that line on. The first record must be an `M` between the chunk's first line and its first spoken line; it also covers the lines before it |
| `E <line> +id -id ...` | Sprite characters entering/leaving at that line (ids from the sprite cast) |

Emotions: neutral, joyful, melancholic, solemn, ironic, angry, fearful, ecstatic. Music:
contemplative, comic, sacred, ominous, mystical, none.

Validation: unknown emotion/mood/id, missing or unknown speech, duplicate records, a line
outside the chunk, `L` on a non-spoken line, an exit of a pinned character or of someone who
is not on stage → error. `+id` for a character who speaks later in the chunk is dropped
(speakers enter automatically). On an error the runner retries once with the error and the
rejected answer appended to the input; if it still fails the chunk is `failed` (others go on)
and the next `run` tries it again.

## Expansion (deterministic, in `merge`)

- Emotion per line: the speech's `S`, replaced from each `L` line on; directions are `neutral`.
- Music: the current mood on every line, carried across chunks and scenes.
- `on_stage`: state at scene start (pinned cast, with their pinned positions) + `E` events +
  auto-enter (a sprite character who speaks is on stage from that line). Pinned characters
  never leave. Positions are permanent within a scene: pinned first, then the first free of
  left/center/right in entrance order (a slot nobody used yet is preferred over one freed by
  an exit, because the generator keeps the first position it sees).
- `[[overrides]]`, `[[cues]]` (incl. `move`) and `[staging]` are applied later by the generator
  exactly as before; the shape of `annotated.json` did not change.

## Commands

| Command | Effect |
|---|---|
| `vnpipe annotate run [--model haiku] [--jobs 4] [--only GLOB] [--runner CMD]` | Annotate every chunk without a valid cache (or cached with another model) |
| `vnpipe annotate status [-v]` | Done / pending / failed chunks, total tokens and cost from the usage log |
| `vnpipe annotate prompt <chunk>` | Print instructions + input of a chunk, for a manual or sub-agent run |
| `vnpipe annotate merge [--strict]` | Expand the caches into `data/annotated.json` |

**Runner.** Any command that reads the chunk on stdin and prints either the `claude -p
--output-format json` envelope (`result`, `usage`, `total_cost_usd`) or plain records.
`{model}` and `{prompt_file}` are substituted. It runs in a temporary directory. Default:

```
env MAX_THINKING_TOKENS=0 claude -p --model {model} --tools "" --strict-mcp-config --setting-sources ""
  --system-prompt-file {prompt_file} --output-format json --no-session-persistence
```

`MAX_THINKING_TOKENS=0` is essential: with thinking on, Haiku spent ~10,000 hidden output
tokens (about 2 minutes, $0.06) on a chunk whose answer is ~200 tokens.

**Cache.** `annotations/cache/<chunk>.txt` holds the accepted answer behind a header line
with `input=sha256(prompt version + chunk input)` and the model; changing the prompt, the text,
the scene config or the pinned cast makes the chunk `stale`. Rejected final answers go to
`<chunk>.failed`. `annotations/usage.jsonl` gets one line per call: chunk, model, attempt,
input / output / cache-creation / cache-read tokens, cost, seconds (and `rejected` with the
reason when the answer was refused).

**Partial output.** `merge` always writes every scene. A chunk without a valid answer is
expanded with no records (neutral, music carried over, speakers auto-enter), listed in
`fallback_chunks`, and each unannotated scene is printed as a warning. `--strict` refuses to
merge instead. This keeps the generator and the end-to-end test whole while a work is being
annotated scene by scene.

## Measured cost (Haiku, Faust pilot)

Overhead of a headless call with the flags above is ~700 tokens. Three chunks (705 spoken
lines): 4.4k-6.6k input tokens each (instructions ~1.4k + text) and 180-255 output tokens,
$0.010-0.014 per chunk, ~5 s each; about 25 tokens per spoken line against ~1,000 for the old
per-line JSON (about 40x less). Extrapolated, the whole play costs roughly $0.35.

## Resumability

`run` is idempotent: cached chunks are skipped, so re-running only pays for stale, pending
and failed ones. Re-run `merge` afterwards.
