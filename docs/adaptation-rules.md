# Adaptation rules

How a play becomes a visual novel. The text is never rewritten, abridged or reordered:
every verse line of the scoped text is shown, in order. What changes is *how* it is
shown. Generic rules are implemented in `src/vnpipe/renpy_gen.py`; work-specific choices
live in the work's `work.toml` (`[staging.*]`, `[[cues]]`, `[cast.*]`; see [work-config.md](work-config.md)).

## Principle: show less, suggest more

A VN tempts you to draw everything. Romantic verse drama works better when the image
sets the mood and the words carry the scene. So:

1. **Draw people only when they converse.** Sprites appear in dialogue scenes.
2. **Never draw the ineffable.** Divine or supernatural presences are a lighting change,
   a flash or a shake on the background, never a sprite. Their lines still carry a name
   and colour.
3. **Lyric passages are full-screen text (NVL).** Poems and hymns are read over a
   background, without a dialogue box or sprites.
4. **Choral or crowd presences belong to the background**, not drawn as several sprites.
5. **Stage directions are narration**, shown in italics by the narrator. Directions that
   change the scene also trigger a cue (background, tint, transition). Cast lists under
   a scene heading are not shown; they only inform who is on stage.
6. **One background per location**, plus variants only where the text itself changes
   the light (a vision, an apparition, a change of mood).

## Text rhythm

- Each scene opens with a title card (scene heading, centred, over black).
- Verse is grouped into text boxes of at most **4 lines**, never crossing a stanza or a
  speaker change. In NVL, a whole stanza (max 8 lines) is shown before the page clears.
- Split verse lines (a line shared between two speakers, printed deeply indented) are
  shown as their own box so the exchange keeps its speed.

## Sprites and expressions

- The annotation gives each line one emotion from a fixed vocabulary: `neutral`,
  `joyful`, `melancholic`, `solemn`, `ironic`, `angry`, `fearful`, `ecstatic`.
- `work.toml` maps that vocabulary to the 3–4 expressions each character actually has
  (`[cast.<id>].expressions`). A text box uses the emotion of its first line.
- Positions (`left`, `center`, `right`) come from the annotation, but the first
  position a character gets in a scene is kept for the whole scene, so nobody jumps
  around between boxes.
- Characters with `sprite = false` are never drawn, even if annotated as on stage.

## Human judgment layer

The annotation is an LLM's first guess; `work.toml` can override it, generically:

- `[staging.<scene>].cast = { <char> = "<pos>" }` pins characters (sprite = true) on stage
  at fixed positions for the whole scene, overriding the annotation's `on_stage`.
- A cue's `move = { <char> = "<pos>" }` slides a visible sprite to another position.
- `[[overrides]] { from, to, emotion }` forces the emotion of an inclusive line range
  (same scene) before expression mapping.

Stage-direction lines never change who is on stage (they keep the previous box's
`on_stage`). Unknown line ids, characters, positions or emotions are errors at generation.

## Transitions

- `dissolve` between expressions and on sprite entry/exit, `fade` between scenes,
  `slow` (2 s dissolve) for light changes, `flash` for supernatural apparitions.

## Music

Every line carries an optional music mood (`contemplative`, `comic`, `sacred`,
`ominous`, `mystical`, `none`). The prototype ships without audio; the generator emits
`play music` only when `game/audio/<mood>.ogg` exists, so adding files is enough.
