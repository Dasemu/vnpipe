# Voice track

A work can play a recording of the text in its original language under the translated text
(like subtitled anime). `vnpipe voice` builds it from a public-domain reading: ASR timestamps
locate each source-language speech in the audio, the speeches are mapped onto the game's
speeches by speaker sequence, and one clip per speech is cut. Anything doubtful stays silent;
a wrong voice is never emitted.

```
audio sections (mp3) ──fetch──▶ data/raw/...                       (gitignored)
source-language text ──fetch──▶ data/raw/...                       (gitignored)
audio ──asr (GPU host)──▶ voice/asr/NN.json     words with start/end/prob  (commit it)
text + ASR + annotated.json ──build──▶ voice/map.json, voice/report/<scene>.txt
mp3 + map ──cut (ffmpeg)──▶ voice/clips/<first line id>.ogg
                         ──renpy──▶ game/voice/*.ogg + `voice` statements
```

## Stages

| Command | What it does |
|---|---|
| `voice fetch [--scenes a,b]` | Downloads the source text and the audio sections (skips existing files). |
| `voice asr [--scenes] [--host H] [--force]` | `scp`s the sections and `scripts/asr.py` to the GPU host, runs faster-whisper (word timestamps) on every `[voice.asr].devices` GPU in parallel (files balanced by size), copies `voice/asr/NN.json` back. Skips sections already present. The only step that needs a GPU; commit the JSON so the rest runs anywhere. |
| `voice build [--scenes]` | Parses the source text into speeches, aligns them with the ASR words, maps them onto the game's speeches and writes `voice/map.json` and one report per scene. |
| `voice cut [--scenes]` | Cuts and normalises one clip per `ok` speech. Cached by (audio, window, loudness). |

Then `vnpipe renpy` ships the clips. `--scenes` defaults to `[voice].scenes`, else every
`[[voice.sections]]` entry. Config keys: see [work-config.md](work-config.md#voice).

## How it works

1. **Source speeches** (`voice_align.parse_speeches`): a speaker label is an upper-case line
   (`FAUST.`, `MEPHISTOPHELES (zu Faust).`, parentheses may span lines); `( ... )` stage
   directions are dropped; the label maps to a cast id through `[voice.speakers]`
   (unknown labels become speeches nobody can be mapped to).
2. **Word alignment** (`align`): `difflib.SequenceMatcher` over tokens normalised to lower-case
   letters (ß/ss and umlauts folded). The narrator reading speaker names or stage directions,
   and the preamble/outro, have no counterpart in the text, so they are never matched. Per speech,
   matched ASR words are clustered (a stray match is dropped); the span grows by up to two
   unmatched edge words that are less than 0.3 s from it (misheard first/last words).
3. **Speaker-sequence mapping** (`map_speeches`): global alignment over the two speaker-id
   sequences; only equal ids pair up, closer lengths score higher. Crowd labels are ordinal
   ("the others", "second"), so ids listed in `[voice].ensembles` also pair with each other
   (at a penalty); an individual never stands in for a crowd. Pairs whose length ratio
   (game words / source tokens) is outside 0.4-2.5 are dropped (merged or split speeches), unless
   the difference is at most 4 words (interjections).
4. **Filters**: coverage (matched share of the speech's tokens) at least 0.6, and the duration
   must be plausible for the syllable count (1.6-9 syllables/s). Statuses in the map: `ok`,
   `unmapped`, `low-coverage`, `implausible-duration`, `cut-failed`. Only `ok` has a clip.
5. **Cut**: `[voice.filters].<speaker>` is an ffmpeg `-af` chain (noise cleanup, EQ) applied
   to that speaker's clips before loudnorm; it is part of the cut cache key. Window = span start-0.1 s .. end+0.25 s, shrunk to 45% of the silence to the
   neighbouring ASR word so another voice is never pulled in. Two-pass `loudnorm`
   (I=-18, linear), mono Opus 40 kbps. The clip's duration is checked against the window.
6. **Ren'Py**: `voice "voice/<id>.ogg"` before the first box of the speech, `voice sustain`
   before its other boxes (a stage direction inside the speech keeps the clip going). Clips
   live in `game/voice/`, never under `audio/`, so they are not music moods. The Preferences
   screen has a Voice Volume slider (translated in `renpy_templates/tl/`). Credits get a "Voces"/"Voices"
   section from `[voice].credits` when clips ship.

`voice/report/<scene>.txt` lists, per scene, the game speeches and their status, the ASR
coverage and the source speeches left without a game speech.

## Quality check

Re-transcribe the clips with `scripts/asr.py` and compare each transcript with its source
speech (recall of the speech's words, precision against extra words = other voices). Clips
that fail only because of ASR mishearings on very short utterances are expected; contamination
shows as low precision with foreign words at the edges.

## Running it elsewhere

- ASR needs `faster-whisper` and a CUDA GPU on the host; it is deliberately not a vnpipe
  dependency. Medium (int8_float16) fits 4 GB; large-v3 did not.
- ffmpeg/ffprobe (libopus, loudnorm) must be on `PATH` for `cut`.
- Source text per section is found by its heading line, in order, so repeated headings work.
