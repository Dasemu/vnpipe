"""Ren'Py side of the translations: ``game/tl/<language>/`` from ``data/translations/<code>.json``.

Every say statement of the generated script carries an explicit ``id`` (its first line id), so
each translation writes ``translate <language> <id>:`` for every one of them:

- the first box of a game unit that the mapping gave text shows that text, split into boxes
  like the original (stanzas, at most 4 lines in ADV or 8 in NVL, long prose paragraphs at
  sentence ends);
- every other box (the rest of the unit, or a unit grouped with an earlier one) is ``pass``.

The voice clips are shared by every language: a clip that starts on a box of the group starts
on the first translated box of the same speaker, ``voice sustain`` keeps it going over the
following boxes of that speaker and over stage directions.

Menu, card and credit strings go to ``translate <language> strings:``; the credits, whose
entries differ per language, are a per-language list in ``menu_data.rpy``.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from vnpipe.config import Work
from vnpipe.renpy_gen import (
    ADV_MAX_LINES,
    ART_STYLE_LABELS,
    NVL_MAX_LINES,
    UI_LABELS,
    BoxRecord,
    _markup,
    about_parts,
    char_var,
    credits_sections,
    escape_text,
    scene_menu_titles,
    title_cards,
)
from vnpipe.translate import config_digest, load_translation, translations
from vnpipe.translate_align import game_units

MAX_LINE_WORDS = 45  # a longer line (a prose paragraph) is split at sentence ends
SENTENCE_END_RE = re.compile(r"[.!?…][’”)]*\s+|[.!?]—")  # where a long line may be cut

# Native names of the languages a work or translation is written in.
LANGUAGE_NAMES = {
    "de": "Deutsch",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português",
}


@dataclass
class Translation:
    """One configured translation and its built mapping."""

    code: str
    language: str  # Ren'Py language name (``english``)
    label: str  # shown in the language picker, in that language
    cfg: dict[str, Any]
    data: dict[str, Any]


def load_all(work: Work, annotated: dict[str, Any]) -> list[Translation]:
    """The work's translations; an unbuilt or stale mapping is an error (the generated game
    would silently show the wrong lines)."""
    result = []
    annotated_file = work.path("data/annotated.json")
    digest = (
        hashlib.sha256(annotated_file.read_bytes()).hexdigest()
        if annotated_file.is_file()
        else None
    )
    for code, cfg in translations(work).items():
        data = load_translation(work, code)
        if data is None:
            raise ValueError(f"translation {code!r}: run `vnpipe translate build` first")
        if digest is not None and data["annotated_sha256"] != digest:
            raise ValueError(
                f"translation {code!r} was built from another annotated.json: "
                "run `vnpipe translate build` again"
            )
        if data["config_sha256"] != config_digest(cfg):
            raise ValueError(
                f"translation {code!r} was built with another [translations.{code}] mapping "
                "configuration: run `vnpipe translate build` again"
            )
        ids = {ln["id"] for sc in annotated["scenes"] for ln in sc["lines"]}
        for sid, scene in data["scenes"].items():
            unknown = [k for k in scene["units"] if k not in ids]
            if unknown:
                raise ValueError(f"translation {code!r}, {sid}: unknown line ids {unknown[:3]}")
        result.append(
            Translation(
                code,
                cfg["language"],
                cfg.get("label") or LANGUAGE_NAMES.get(code, code),
                cfg,
                data,
            )
        )
    return result


def languages(work: Work, tls: list[Translation]) -> list[tuple[str | None, str]]:
    """(Ren'Py language, label) pairs; the work's own language first (the default)."""
    ui = work.data.get("ui", {})
    lang = work.data["work"].get("language", "es")
    own = (ui.get("language"), ui.get("language_label") or LANGUAGE_NAMES.get(lang, lang))
    return [own, *((t.language, t.label) for t in tls)]


# --- say blocks ------------------------------------------------------------------------------


def _split_long(line: str) -> list[str]:
    """A line over ``MAX_LINE_WORDS`` words as runs of whole sentences (the text unchanged)."""
    if len(line.split()) <= MAX_LINE_WORDS:
        return [line]
    cuts = [0, *(m.end() for m in SENTENCE_END_RE.finditer(line)), len(line)]
    pieces = [line[a:b] for a, b in zip(cuts, cuts[1:], strict=False) if b > a]
    chunks: list[str] = []
    for piece in pieces:  # slices of the line, so a chunk is always a verbatim substring
        if chunks and len((chunks[-1] + piece).split()) <= MAX_LINE_WORDS:
            chunks[-1] += piece
        else:
            chunks.append(piece)
    return [c.strip() for c in chunks if c.strip()]


def item_boxes(item: dict[str, Any], limit: int) -> list[list[str]]:
    """The boxes (lists of lines) of one translated item."""
    boxes: list[list[str]] = []
    if item["kind"] == "dir":
        lines = [ln for st in item["stanzas"] for ln in st]
        for line in lines:
            parts = _split_long(line)
            if len(parts) > 1 or not boxes or len(boxes[-1]) >= limit:
                boxes.extend([p] for p in parts)
            else:
                boxes[-1].append(line)
        return boxes
    for stanza in item["stanzas"]:
        box: list[str] = []
        for line in stanza:
            parts = _split_long(line)
            if len(parts) > 1:
                if box:
                    boxes.append(box)
                    box = []
                boxes.extend([p] for p in parts)
                continue
            if len(box) >= limit:
                boxes.append(box)
                box = []
            box.append(line)
        if box:
            boxes.append(box)
    return boxes


def assign_clips(clips: list[tuple[str, str]], says: list[str]) -> dict[int, str]:
    """Place a group's voice clips (in order, with the Spanish box's speaker) on its translated
    say boxes (their speakers, in order): each clip goes to the next box of the same speaker
    when one is left (keeping room for the clips after it), else to the next box. Every clip
    is placed; a group with more clips than say boxes is an error."""
    if len(clips) > len(says):
        raise ValueError(f"{len(clips)} voice clips but only {len(says)} translated say boxes")
    placed: dict[int, str] = {}
    start = 0
    for k, (speaker, path) in enumerate(clips):
        last = len(says) - (len(clips) - k)  # leave a box for each later clip
        at = next((i for i in range(start, last + 1) if says[i] == speaker), start)
        placed[at] = path
        start = at + 1
    return placed


class VoiceState:
    """Which speaker's clip the translated boxes keep playing."""

    def __init__(self) -> None:
        self.active: str | None = None

    def statement(self, speaker: str, is_dir: bool, clip: str | None) -> str | None:
        if clip is not None:
            self.active = speaker
            return f'voice "{clip}"'
        if self.active is not None and (is_dir or speaker == self.active):
            return "voice sustain"
        self.active = None
        return None


def render_scene_blocks(
    tl: Translation,
    scene: dict[str, Any],
    records: list[BoxRecord],
    lyric_voice: str,
    cast: dict[str, Any],
) -> list[str]:
    units = tl.data["scenes"][scene["id"]]["units"]
    heads = {u.first_line for u in game_units(scene, lyric_voice)}
    out: list[str] = []
    voice = VoiceState()
    # groups: a box with text, then the boxes that show nothing until the next one
    groups: list[list[BoxRecord]] = []
    for rec in records:
        if rec.line_id in heads and rec.line_id in units or not groups:
            groups.append([rec])
        else:
            groups[-1].append(rec)
    for group in groups:
        head = group[0]
        if not (head.line_id in heads and head.line_id in units):  # nothing before any text
            for rec in group:
                out += [f"translate {tl.language} {rec.line_id}:", "    pass", ""]
            continue
        if not head.sustain:
            voice.active = None
        clips = [(r.speaker, r.voice) for r in group if r.voice]
        limit = NVL_MAX_LINES if head.nvl else ADV_MAX_LINES
        boxes: list[tuple[str, bool, list[str]]] = []
        for item in units[head.line_id]:
            speaker = item["speaker"]
            if speaker != "narrator" and speaker not in cast:
                raise ValueError(f"translation {tl.code!r}: unknown speaker {speaker!r}")
            is_dir = item["kind"] == "dir"
            boxes += [(speaker, is_dir, box) for box in item_boxes(item, limit)]
        say_at = [i for i, (_, is_dir, _) in enumerate(boxes) if not is_dir]
        try:
            placed = assign_clips(clips, [boxes[i][0] for i in say_at])
        except ValueError as exc:
            raise ValueError(f"translation {tl.code!r}, {head.line_id}: {exc}") from None
        clip_at = {say_at[k]: path for k, path in placed.items()}
        body: list[str] = []
        for i, (speaker, is_dir, box) in enumerate(boxes):
            if head.nvl:
                body.append("nvl clear")
            statement = voice.statement(speaker, is_dir, clip_at.get(i))
            if statement:
                body.append(statement)
            text = "\\n".join(escape_text(line) for line in box)
            body.append(f'{char_var(speaker, nvl=head.nvl)} "{text}"')
        out.append(f"translate {tl.language} {head.line_id}:")
        out += [f"    {ln}" for ln in body]
        out.append("")
        for rec in group[1:]:
            out += [f"translate {tl.language} {rec.line_id}:", "    pass", ""]
    return out


def render_tl_script(
    tl: Translation,
    annotated: dict[str, Any],
    records: dict[str, list[BoxRecord]],
    lyric_voice: str,
    cast: dict[str, Any],
) -> str:
    out = [
        "## Generated by `vnpipe renpy` from data/translations/"
        f"{tl.code}.json. Do not edit: regenerate instead.",
        "",
    ]
    for scene in annotated["scenes"]:
        if scene["id"] not in tl.data["scenes"]:
            raise ValueError(f"translation {tl.code!r} has no scene {scene['id']!r}")
        out += render_scene_blocks(tl, scene, records.get(scene["id"], []), lyric_voice, cast)
    return "\n".join(out)


# --- strings ---------------------------------------------------------------------------------


def string_literal(text: str) -> str:
    """A Ren'Py string literal whose value is ``text`` exactly (already markup-escaped)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def render_tl_strings(tl: Translation, pairs: list[tuple[str, str]]) -> str:
    """``translate <language> strings:`` for each (original, translation) pair; an original
    may appear once only (a clash is an error: tag the original with ``{#...}``)."""
    seen: dict[str, str] = {}
    out = [
        "## Generated by `vnpipe renpy`. Do not edit: regenerate instead.",
        "",
        f"translate {tl.language} strings:",
        "",
    ]
    for old, new in pairs:
        if not old or old == new:
            continue
        if old in seen:
            if seen[old] != new:
                raise ValueError(
                    f"translation {tl.code!r}: {old!r} is both {seen[old]!r} and {new!r}"
                )
            continue
        seen[old] = new
        out += [f"    old {string_literal(old)}", f"    new {string_literal(new)}", ""]
    return "\n".join(out)


def _runtime(text: str) -> str:
    """The value Ren'Py holds for a generated string (markup escaped, a trailing ``{#...}``
    tag kept)."""
    match = re.search(r"\{#[^{}]*\}$", text)
    if not match:
        return _markup(text)
    return _markup(text[: match.start()]) + match.group(0)


def translated_view(
    work: Work, annotated: dict[str, Any], tl: Translation
) -> tuple[Work, dict[str, Any]]:
    """The work and script as the translation names them: title, subtitle, translator, end
    text, source, voice credits, scene headings and subtitles."""
    cfg = tl.cfg
    info = {**work.data["work"], "language": tl.code}
    for key in ("title", "subtitle", "translator", "end_text"):
        if key in cfg:
            info[key] = cfg[key]
    sections = {s["scene"]: s for s in cfg.get("sections", [])}
    scenes = [
        {**sc, "subtitle": sections.get(sc["id"], {}).get("subtitle")}
        for sc in work.data.get("scenes", [])
    ]
    data = {**work.data, "work": info, "scenes": scenes}
    if cfg.get("gutenberg_id"):
        data["source"] = {**work.data.get("source", {}), "gutenberg_id": cfg["gutenberg_id"]}
    if "voice" in work.data:
        data["voice"] = {**work.data["voice"], "credits": cfg.get("voice_credits", [])}
    ann = {
        **annotated,
        "scenes": [
            {**sc, "heading": tl.data["scenes"][sc["id"]]["title"]} for sc in annotated["scenes"]
        ],
    }
    return Work(work.root, data), ann


def collision_tags(work: Work, annotated: dict[str, Any], tls: list[Translation]) -> dict[str, str]:
    """Scene id -> tag id for scenes whose heading, subtitle or menu title is the same text as
    another one but translated differently (``{#<id>}`` on cards, ``{#<id> menu}`` in the
    scene list)."""
    es_cards = title_cards(work, annotated)
    es_menu = dict(scene_menu_titles(work, annotated))
    targets: dict[str, dict[str, set[str]]] = {}  # original -> translation -> scene ids
    for tl in tls:
        tw, ta = translated_view(work, annotated, tl)
        en_cards = title_cards(tw, ta)
        en_menu = dict(scene_menu_titles(tw, ta))
        for sc in annotated["scenes"]:
            sid = sc["id"]
            name = f"card_{sid}"
            pairs = list(zip(es_cards[name], en_cards[name], strict=True))
            pairs.append((es_menu[f"scene_{sid}"], en_menu[f"scene_{sid}"]))
            for old, new in pairs:
                if old:
                    targets.setdefault(old, {}).setdefault(new or "", set()).add(sid)
    tags: dict[str, str] = {}
    for by_new in targets.values():
        if len(by_new) > 1:
            for sids in by_new.values():
                tags.update({sid: sid for sid in sids})
    return tags


def string_pairs(
    work: Work,
    annotated: dict[str, Any],
    manifest: dict[str, Any] | None,
    tl: Translation,
    tags: dict[str, str],
) -> list[tuple[str, str]]:
    """(original, translation) of every generated string the game shows outside dialogue."""
    tw, ta = translated_view(work, annotated, tl)
    lang = work.data["work"].get("language", "es")
    pairs: list[tuple[str, str]] = []
    names = tl.cfg.get("names", {})
    missing = [cid for cid, c in work.cast.items() if c.get("name") and cid not in names]
    if missing:
        raise ValueError(f"[translations.{tl.code}.names] lacks {missing}")
    for cid, name in names.items():
        if cid not in work.cast:
            raise ValueError(f"[translations.{tl.code}.names]: unknown cast id {cid!r}")
        pairs.append((_markup(work.cast[cid].get("name") or ""), _markup(name)))
    es_cards, en_cards = title_cards(work, annotated, tags), title_cards(tw, ta)
    for name, card in es_cards.items():
        for old, new in zip(card, en_cards[name], strict=True):
            if old and new:
                pairs.append((_runtime(old), _markup(new)))
    en_menu = dict(scene_menu_titles(tw, ta))
    pairs += [(old, en_menu[label]) for label, old in scene_menu_titles(work, annotated, tags)]
    info, tinfo = work.data["work"], tw.data["work"]
    pairs.append((_markup(info["title"]), _markup(tinfo["title"])))
    pairs.append((_markup(info.get("subtitle", "")), _markup(tinfo.get("subtitle", ""))))
    pairs.append(
        (
            "\n\n".join(_markup(p) for p in about_parts(work)),
            "\n\n".join(_markup(p) for p in about_parts(tw)),
        )
    )
    ui, tui = UI_LABELS.get(lang, UI_LABELS["en"]), UI_LABELS.get(tl.code, UI_LABELS["en"])
    pairs += [(ui[k], tui[k]) for k in ("scenes", "credits")]
    art = ART_STYLE_LABELS.get(lang, ART_STYLE_LABELS["en"])
    tart = ART_STYLE_LABELS.get(tl.code, ART_STYLE_LABELS["en"])
    pairs += list(zip(art, tart, strict=True))
    pairs += [(_markup(k), _markup(v)) for k, v in tl.cfg.get("strings", {}).items()]
    return pairs


def translated_credits(
    work: Work, annotated: dict[str, Any], manifest: dict[str, Any] | None, tl: Translation
) -> list[tuple[str, list[str]]]:
    tw, _ = translated_view(work, annotated, tl)
    return credits_sections(tw, manifest)
