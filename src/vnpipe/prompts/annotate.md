# Task: annotate one chunk of a verse play for a visual novel

You annotate chunk `{{chunk_id}}`. Do not read or write any other file than the two below.

- Input (read it): `{{chunk_path}}`
- Output (write it, nothing else): `{{cache_path}}`

## Input

The chunk JSON has `scene`, `allowed`, `context_before` (earlier lines, read-only context,
do not annotate them) and `lines`. Each line has `id`, `kind`, `speaker`, `speaker_note`,
`text` and `defaults`.

## Output

One JSON file, exactly this shape:

```json
{
  "chunk_id": "<copy from input>",
  "input_sha256": "<copy from input>",
  "annotations": [
    {
      "id": "<line id>",
      "speaker": "<defaults.speaker>",
      "kind": "<defaults.kind>",
      "emotion": "neutral",
      "location": "<defaults.location>",
      "time_of_day": "<defaults.time_of_day>",
      "on_stage": [{"character": "faust", "position": "left"}],
      "music": null
    }
  ]
}
```

Rules:

1. Exactly one annotation per input line, in the same order, same `id`s. No extras, no omissions.
2. Copy `speaker`, `kind`, `location`, `time_of_day` verbatim from the line's `defaults`.
3. `emotion`: one of `allowed.emotions`, chosen from the meaning of the verse in its context
   (read `context_before` and the neighbouring lines).
4. `on_stage`: the characters present in the scene at that moment, not only the speaker.
   Only ids from `allowed.characters`, each at most once per line, `position` one of
   `left`, `center`, `right`. Include present characters even if
   they are never drawn (the Lord, the Spirit): the renderer filters them. Use `[]` for
   lyric text with nobody in the scene (the Dedicatoria).
5. `music`: a mood from `allowed.music` only at a real mood shift (a new scene, a hymn
   starting, the Spirit appearing); `null` on every other line. Never repeat the same mood.
6. Output only valid JSON, no comments, no extra keys, no markdown fences in the file.

## Emotions

- `neutral`: plain statement, exposition, stage business, questions of fact.
- `joyful`: warmth, laughter, praise, light banter. Comic exchanges between the Clown and
  the Director: joyful or ironic.
- `melancholic`: lyric longing, memory, loss, weariness. The Dedicatoria is melancholic
  ("Tornáis de nuevo, hermosas imágenes flotantes...").
- `solemn`: sacred, ceremonial, grave. The archangels' hymn and the Lord's words are solemn.
- `ironic`: sarcasm, wit, mockery, cynicism. Mephistopheles' sly remarks are ironic, even
  when they sound polite.
- `angry`: rage, scorn, disgust, bitter rebuke.
- `fearful`: terror, dread, shrinking back. Faust before the Earth Spirit ("aterrado") is fearful.
- `ecstatic`: rapture, exaltation, overwhelming vision. Faust at the sign of the Macrocosm.
  Faust's despair is melancholic or angry; his ecstasy is ecstatic.

Check `speaker_note` too (for example "aterrado" means fearful). Do not default everything
to `neutral`; do not swing wildly between lines of one continuous thought.

## Staging

- Work out who is in the scene from the cast list (a `cast` line is only a list of who is
  there), the stage directions and who has spoken so far (use `context_before`).
- A character who has not yet entered or who has left must not appear in `on_stage`.
- Positions must stay stable within the scene: once a character is `left`, keep them `left`
  in every later line. With two characters use `left` and `right`; a third takes `center`.
- Stage directions say who enters, leaves or appears; apply them from the line after.

## Before finishing

Run this and make sure the line for `{{chunk_id}}` says `ok`:

```
{{status_cmd}} {{chunk_id}}
```

If it reports `invalid` or `stale`, fix the file (never edit the input) and run it again.
Finish with one short line saying the chunk is ok.
