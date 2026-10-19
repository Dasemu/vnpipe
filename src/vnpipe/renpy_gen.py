"""Ren'Py generator: annotated play + asset manifest -> a complete Ren'Py 8 project.

The output is deterministic (no timestamps, sorted iteration, copied bytes), so
re-running the command yields identical files. Staging rules are documented in
``docs/adaptation-rules.md``; work-specific choices come from ``work.toml``.

The GUI files in ``renpy_templates/`` were produced once with the Ren'Py SDK's
``generate_gui`` command and are MIT-licensed Ren'Py files (see ``docs/running.md``).
"""

import argparse
import hashlib
import json
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from vnpipe import PACKAGE_DIR
from vnpipe.assets import load_manifest, placeholder, style_file, usable_rendition
from vnpipe.config import Work, load_work
from vnpipe.schema import validate

TEMPLATE_DIR = PACKAGE_DIR / "renpy_templates"
NO_MANIFEST_STYLE = {"default": {"label": "Default"}}  # work without an asset manifest

ADV_MAX_LINES = 4
NVL_MAX_LINES = 8
SPLIT_INDENT = 6  # parsed `indent` at or above this marks a split verse line
TITLE_PAUSE = 2.0
END_PAUSE = 3.0

VALID_POSITIONS = ("left", "center", "right")
EMOTIONS = (
    "neutral",
    "joyful",
    "melancholic",
    "solemn",
    "ironic",
    "angry",
    "fearful",
    "ecstatic",
)
POSITIONS = ("left", "right", "center")  # order in which --from-parsed assigns positions
TRANSITIONS = {
    "dissolve": "dissolve",
    "fade": "fade",
    "slow": "vn_slow",
    "flash": "vn_flash",
}
TINTS = ("none", "dark", "red")
DEFAULT_LYRIC_VOICE = "poet_voice"
DEFAULT_END_TEXT = "Continuará…"
MENU_BG_FALLBACK = "gui/main_menu.png"
MUSIC_FADE = "fadeout 1.0 fadein 1.0"
UNKNOWN_MUSIC = "?"  # what plays is not known statically (start of a scene: it may be entered
# from the main menu), so the first music statement of every scene is always emitted

ABOUT_LABELS = {
    "es": ("Texto", "Traducción", "Adaptación a novela visual generada con vnpipe."),
    "en": ("Text", "Translation", "Visual novel adaptation generated with vnpipe."),
}
# Menu/credits strings per work language: scenes, credits, text, translation, source, music,
# illustrations, "illustrations generated with X", Gutenberg line, "no scene reached yet".
UI_LABELS = {
    "es": {
        "scenes": "Escenas",
        "credits": "Créditos",
        "text": "Texto",
        "translation": "Traducción",
        "source": "Fuente",
        "music": "Música",
        "art": "Ilustraciones",
        "art_with": "Ilustraciones generadas con {}",
        "gutenberg": "Proyecto Gutenberg, n.º {id}",
    },
    "en": {
        "scenes": "Scenes",
        "credits": "Credits",
        "text": "Text",
        "translation": "Translation",
        "source": "Source",
        "music": "Music",
        "art": "Illustrations",
        "art_with": "Illustrations generated with {}",
        "gutenberg": "Project Gutenberg #{id}",
    },
}
ART_SOURCE_NAMES = {"chatgpt-web": "ChatGPT (OpenAI)"}  # manifest `source` -> display name
LOCKED_SCENE = "—"
# (preferences title and picker heading, picker hint) per work language
ART_STYLE_LABELS = {
    "es": ("Estilo visual", "Puedes cambiarlo más tarde en Preferencias."),
    "en": ("Visual style", "You can change this later in Preferences."),
}


# --- text helpers -------------------------------------------------------------


def escape_text(text: str) -> str:
    """Escape a text for use inside a double-quoted Ren'Py say/define string."""
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("[", "[[")
        .replace("{", "{{")
        .replace("\n", "\\n")
    )


def quote(text: str) -> str:
    return f'"{escape_text(text)}"'


def _plain_quote(text: str) -> str:
    """Quote a path or identifier-like string (no Ren'Py text markup)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def char_var(cast_id: str, nvl: bool = False) -> str:
    """Ren'Py variable of a cast member's Character (prefixed: `director` etc. are built-ins)."""
    return f"c_{cast_id}" + ("_nvl" if nvl else "")


def bg_image_name(asset_id: str) -> str:
    """``bg_heaven_lord`` -> ``bg heaven_lord`` (tag ``bg``, attribute ``heaven_lord``)."""
    return "bg " + asset_id.removeprefix("bg_")


# --- audio ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Audio:
    """The OGG files shipped in ``game/audio/``, as paths relative to ``game/``."""

    moods: dict[str, list[str]] = field(default_factory=dict)  # mood -> tracks (sorted)
    files: frozenset[str] = frozenset()


def scan_audio(out: Path) -> Audio:
    """``audio/<mood>/*.ogg`` is a mood playlist; ``audio/<mood>.ogg`` a one-track mood."""
    game = out / "game"
    root = game / "audio"
    if not root.is_dir():
        return Audio()
    paths = sorted(p.relative_to(game).as_posix() for p in root.rglob("*.ogg"))
    moods: dict[str, list[str]] = {}
    for rel in paths:
        parts = rel.split("/")[1:]
        mood = parts[0] if len(parts) > 1 else Path(parts[0]).stem
        moods.setdefault(mood, []).append(rel)
    return Audio(moods, frozenset(paths))


def copy_translations(work: Work, out: Path) -> list[Path]:
    """Copy ``<work>/tl/**/*.rpy`` into ``game/tl/`` and set ``[ui].language`` as default."""
    src_root = work.path("tl")
    dest_root = out / "game" / "tl"
    lang_file = out / "game" / "language.rpy"
    written = []
    if src_root.is_dir():
        shutil.rmtree(dest_root, ignore_errors=True)
        for src in sorted(src_root.rglob("*.rpy")):
            dest = dest_root / src.relative_to(src_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            written.append(dest)
    language = work.data.get("ui", {}).get("language")
    if language:
        write_text(lang_file, f"define config.default_language = {quote(language)}\n")
        written.append(lang_file)
    else:
        lang_file.unlink(missing_ok=True)
    return written


def copy_audio(work: Work, out: Path) -> list[Path]:
    """Copy ``<work>/audio/**/*.ogg`` into ``game/audio/`` keeping subdirectories."""
    src_root = work.path("audio")
    if not src_root.is_dir():
        return []  # no work audio: leave whatever the project already holds
    dest_root = out / "game" / "audio"
    shutil.rmtree(dest_root, ignore_errors=True)
    written = []
    for src in sorted(src_root.rglob("*.ogg")):
        dest = dest_root / src.relative_to(src_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        written.append(dest)
    return written


def check_music_spec(spec: Any, where: str, allow_auto: bool) -> None:
    if not isinstance(spec, str) or not spec or spec == "track:":
        raise ValueError(f"{where}: music must be a mood, 'track:<path>' or 'none'")
    if spec == "auto" and not allow_auto:
        raise ValueError(f"{where}: music 'auto' is only valid in a cue")


# --- annotations ----------------------------------------------------------------


def lyric_voice(work: Work) -> str:
    return work.data["work"].get("lyric_voice", DEFAULT_LYRIC_VOICE)


def annotations_from_parsed(work: Work, parsed: dict[str, Any]) -> dict[str, Any]:
    """Trivial in-memory annotations (used before the LLM annotations exist)."""
    voice = lyric_voice(work)
    scene_cfg = {s["id"]: s for s in work.scenes}
    digest = hashlib.sha256(
        json.dumps(parsed, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    scenes = []
    for scene in parsed["scenes"]:
        cfg = scene_cfg.get(scene["id"], {})
        spoken: list[str] = []
        positions: dict[str, str] = {}
        lines = []
        for line in scene["lines"]:
            speaker = line["speaker"]
            if line["kind"] == "direction":
                ann_speaker, kind = "narrator", "stage_direction"
            elif speaker:
                ann_speaker, kind = speaker, "dialogue"
            else:
                ann_speaker, kind = voice, "narration"
            if line["kind"] == "verse" and speaker and work.cast.get(speaker, {}).get("sprite"):
                if speaker not in positions:
                    positions[speaker] = POSITIONS[len(positions) % len(POSITIONS)]
                if speaker not in spoken:
                    spoken.append(speaker)
            lines.append(
                {
                    **line,
                    "ann": {
                        "speaker": ann_speaker,
                        "kind": kind,
                        "emotion": "neutral",
                        "location": cfg.get("location", "unknown"),
                        "time_of_day": cfg.get("time_of_day", "timeless"),
                        "on_stage": [{"character": c, "position": positions[c]} for c in spoken],
                    },
                }
            )
        scenes.append({"id": scene["id"], "heading": scene["heading"], "lines": lines})
    return {
        "work": parsed["work"],
        "parsed_sha256": digest,
        "fallback_chunks": [],
        "scenes": scenes,
    }


# --- assets -----------------------------------------------------------------------


@dataclass
class ImageEntry:
    name: str  # Ren'Py image name, e.g. "bg heaven" or "hero neutral"
    asset: dict[str, Any]
    default: str  # default style id: always present, the ConditionSwitch fallback
    # (style, path below game/images/, file to copy or None for a placeholder), default style
    # first, then the other styles with an approved rendition in manifest order
    variants: list[tuple[str, str, Path | None]]


def _sprite_asset(character: str, expression: str) -> dict[str, Any]:
    return {
        "id": f"sprite_{character}_{expression}",
        "kind": "sprite",
        "character": character,
        "expression": expression,
        "renditions": {},
    }


def _bg_asset(asset_id: str) -> dict[str, Any]:
    return {"id": asset_id, "kind": "background", "renditions": {}}


def _needed_bg_ids(work: Work) -> list[str]:
    ids = [cfg["bg"] for cfg in work.data.get("staging", {}).values() if cfg.get("bg")]
    ids += [cue["bg"] for cue in work.data.get("cues", []) if cue.get("bg")]
    return ids


def art_styles(manifest: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    """(styles by id, default style id); a single pseudo-style when there is no manifest."""
    if manifest is None:
        return NO_MANIFEST_STYLE, next(iter(NO_MANIFEST_STYLE))
    return manifest["styles"], manifest["default_style"]


def collect_images(work: Work, manifest: dict[str, Any] | None) -> list[ImageEntry]:
    """Every image the project declares: manifest assets plus anything the staging needs."""
    styles, default = art_styles(manifest)
    assets: dict[str, dict[str, Any]] = {}
    for asset in (manifest or {}).get("assets", []):
        assets[asset["id"]] = asset
    for asset_id in _needed_bg_ids(work):
        assets.setdefault(asset_id, _bg_asset(asset_id))
    for cid, info in work.cast.items():
        if not info.get("sprite"):
            continue
        for expression in sorted(set(info.get("expressions", {}).values())):
            sprite = _sprite_asset(cid, expression)
            assets.setdefault(sprite["id"], sprite)
    entries = []
    for asset_id in sorted(assets):
        asset = assets[asset_id]
        if asset["kind"] == "background":
            name = bg_image_name(asset_id)
        else:
            name = f"{asset['character']} {asset['expression']}"
        variants: list[tuple[str, str, Path | None]] = []
        for style in [default, *(s for s in styles if s != default)]:
            rendition = asset["renditions"].get(style)
            rel = (rendition or {}).get("file") or style_file(asset, style)
            candidate = work.path("assets") / rel
            if style == default:
                # the fallback may also be a placeholder; otherwise one is generated
                # (a placeholder is always regenerated, without the asset id label)
                ok = rendition and rendition["status"] in {"processed", "approved"}
                variants.append((style, rel, candidate if ok and candidate.is_file() else None))
            elif usable_rendition(asset, style) and candidate.is_file():
                variants.append((style, rel, candidate))
        entries.append(ImageEntry(name, asset, default, variants))
    return entries


# --- script generation ----------------------------------------------------------------


@dataclass
class Box:
    lines: list[dict[str, Any]] = field(default_factory=list)
    key: tuple | None = None
    speaker: str = ""
    is_direction: bool = False


def _playlist(files: list[str]) -> str:
    return "[" + ", ".join(_plain_quote(f) for f in files) + "]"


def _is_direction(line: dict[str, Any]) -> bool:
    return line["kind"] == "direction" or line["ann"]["kind"] == "stage_direction"


class SceneWriter:
    """Turns one scene's lines into Ren'Py statements, tracking staging state."""

    def __init__(
        self, work: Work, scene: dict[str, Any], audio: Audio, music_state: dict[str, str | None]
    ):
        self.work = work
        self.scene = scene
        self.audio = audio
        self.voice = lyric_voice(work)
        self.cues = {c["at"]: c for c in work.data.get("cues", [])}
        staging = work.data.get("staging", {}).get(scene["id"], {})
        self.mode: str = staging.get("mode", "adv")
        self.bg: str | None = staging.get("bg")
        self.tint = "none"
        self.music_state = music_state  # {'playing': key}, shared across scenes
        music_state["playing"] = UNKNOWN_MUSIC
        # scene-wide or cue-set music; None = follow the annotation moods
        self.music_spec: str | None = staging.get("music")
        if self.music_spec == "auto":
            self.music_spec = None
        self.shown: dict[str, str] = {}  # character -> expression currently shown
        self.pinned: dict[str, str] = dict(staging.get("cast", {}))  # always on stage in ADV
        self.positions: dict[str, str] = dict(self.pinned)  # character -> position in this scene
        self.last_on_stage: list[dict[str, str]] = []  # on_stage of the last non-direction box
        self.out: list[str] = []
        self.box: Box | None = None

    def emit(self, text: str = "") -> None:
        self.out.append(("    " + text) if text else "")

    # -- speakers ---------------------------------------------------------------

    def speaker_of(self, line: dict[str, Any]) -> str:
        if _is_direction(line):
            return "narrator"
        speaker = line["ann"]["speaker"] or self.voice
        if speaker != "narrator" and speaker not in self.work.cast:
            raise ValueError(f"{line['id']}: speaker {speaker!r} is not in [cast]")
        return speaker

    # -- cues ----------------------------------------------------------------------

    def apply_cue(self, cue: dict[str, Any]) -> None:
        changes = False
        new_mode = cue.get("mode", self.mode)
        if self.mode == "nvl" and new_mode != "nvl":
            self.emit("nvl hide dissolve")
        if new_mode == "nvl" or cue.get("hide_all"):
            changes |= self.hide_sprites(list(self.shown))
        self.mode = new_mode
        if cue.get("tint") is not None:
            if cue["tint"] not in TINTS:
                raise ValueError(f"cue at {cue['at']}: unknown tint {cue['tint']!r}")
            self.tint = cue["tint"]
        if cue.get("bg") or cue.get("tint") is not None:
            self.bg = cue.get("bg") or self.bg
            self.emit(f"show {bg_image_name(self.bg)} at tint_{self.tint}")
            changes = True
        if changes:
            transition = cue.get("transition", "dissolve")
            if transition not in TRANSITIONS:
                raise ValueError(f"cue at {cue['at']}: unknown transition {transition!r}")
            self.emit(f"with {TRANSITIONS[transition]}")
        if "music" in cue:
            self.music_spec = None if cue["music"] == "auto" else cue["music"]
        if cue.get("shake"):
            self.emit("show layer master at vn_shake")
        moved = False
        for cid, position in cue.get("move", {}).items():
            self.positions[cid] = position
            if cid in self.shown:
                self.emit(f"show {cid} {self.shown[cid]} at vn_{position}")
                moved = True
        if moved:
            self.emit("with vn_move")

    def hide_sprites(self, characters: list[str]) -> bool:
        for cid in characters:
            self.emit(f"hide {cid}")
            del self.shown[cid]
        return bool(characters)

    # -- boxes ----------------------------------------------------------------------

    def limit(self) -> int:
        return NVL_MAX_LINES if self.mode == "nvl" else ADV_MAX_LINES

    def feed(self, line: dict[str, Any]) -> None:
        if line["kind"] == "cast":
            return
        cue = self.cues.get(line["id"])
        if cue:
            self.flush()
            self.apply_cue(cue)
        direction = _is_direction(line)
        key = (self.speaker_of(line), line["speech"], line["stanza"])
        split = self.mode == "adv" and not direction and line["indent"] >= SPLIT_INDENT
        box = self.box
        if box and (
            direction
            or box.is_direction
            or key != box.key
            or len(box.lines) >= self.limit()
            or split
            or (self.mode == "adv" and box.lines[-1]["indent"] >= SPLIT_INDENT)
        ):
            self.flush()
        if self.box is None:
            self.box = Box(key=key, speaker=key[0], is_direction=direction)
        self.box.lines.append(line)

    def flush(self) -> None:
        box = self.box
        if not box:
            return
        self.box = None
        first, last = box.lines[0], box.lines[-1]
        ids = first["id"] if first is last else f"{first['id']} .. {last['id']}"
        self.emit(f"# {ids}")
        self.music(self.music_spec or first["ann"].get("music"))
        if self.mode == "adv":
            self.stage(box)
        text = "\\n".join(escape_text(line["text"]) for line in box.lines)
        variable = char_var(box.speaker, nvl=self.mode == "nvl")
        self.emit(f'{variable} "{text}"')
        if self.mode == "nvl":
            self.emit("nvl clear")

    def music(self, spec: str | None) -> None:
        """Play/stop for a mood, ``track:<path>`` or ``none``; never restarts what already plays."""
        if spec is None or (not self.audio.files and spec != "none"):
            return  # nothing to play (a work without audio)
        if spec == "none":
            if self.music_state["playing"] is not None:
                self.emit("stop music fadeout 1.0")
                self.music_state["playing"] = None
            return
        if spec.startswith("track:"):
            rel = "audio/" + spec.removeprefix("track:")
            if rel not in self.audio.files:
                raise ValueError(f"{self.scene['id']}: music track {rel!r} not found in audio/")
            files = [rel]
        else:
            files = self.audio.moods.get(spec, [])
            if not files:
                if self.audio.moods and (self.music_spec == spec):
                    raise ValueError(f"{self.scene['id']}: no audio for music mood {spec!r}")
                return  # annotation mood without audio: keep what plays
        if self.music_state["playing"] == spec:
            return
        target = _plain_quote(files[0]) if len(files) == 1 else _playlist(files)
        # if_changed: no restart if the engine already plays it (e.g. entered via scene select)
        self.emit(f"play music {target} {MUSIC_FADE} if_changed")
        self.music_state["playing"] = spec

    # -- sprites ---------------------------------------------------------------------

    def stage(self, box: Box) -> None:
        first = box.lines[0]
        ann = first["ann"]
        desired: list[str] = []
        # stage directions never change who is on stage
        on_stage = self.last_on_stage if box.is_direction else ann["on_stage"]
        if not box.is_direction:
            self.last_on_stage = on_stage
        for cid in self.pinned:
            if self.work.cast[cid].get("sprite"):
                desired.append(cid)
        for entry in on_stage:
            cid = entry["character"]
            if cid not in self.work.cast:
                raise ValueError(f"{first['id']}: on_stage character {cid!r} is not in [cast]")
            if not self.work.cast[cid].get("sprite"):
                continue
            self.positions.setdefault(cid, entry["position"])
            if cid not in desired:
                desired.append(cid)
        changed = self.hide_sprites([c for c in list(self.shown) if c not in desired])
        for cid in desired:
            expressions = self.work.cast[cid].get("expressions", {})
            neutral = expressions.get("neutral", "neutral")
            if cid == box.speaker:
                expression = expressions.get(ann["emotion"], neutral)
            else:
                expression = self.shown.get(cid, neutral)
            if self.shown.get(cid) != expression:
                self.emit(f"show {cid} {expression} at vn_{self.positions[cid]}")
                self.shown[cid] = expression
                changed = True
        if changed:
            self.emit("with dissolve")

    # -- scene ---------------------------------------------------------------------------

    def render(self) -> list[str]:
        scene = self.scene
        self.out.append(f"label scene_{scene['id']}:")
        self.emit(f'$ vn_mark_scene("scene_{scene["id"]}")')  # unlocks it in the scene select
        self.emit("window auto")  # also when entered from the scene select, not via `start`
        self.emit("scene black")
        self.emit(f"show card_{scene['id']} at truecenter")
        self.emit("with fade")
        self.emit(f"pause {TITLE_PAUSE}")
        self.emit(f"hide card_{scene['id']}")
        if self.bg:
            self.emit(f"scene {bg_image_name(self.bg)} at tint_{self.tint}")
        self.emit("with fade")
        for line in scene["lines"]:
            self.feed(line)
        self.flush()
        if self.mode == "nvl":
            self.emit("nvl hide dissolve")
        self.out.append("")
        return self.out


def validate_staging(work: Work, annotated: dict[str, Any]) -> None:
    """Check the human-judgment layer (staging.cast, cue move, overrides) against the data."""
    cast = work.cast
    scene_ids = [sc["id"] for sc in annotated["scenes"]]
    for sid, cfg in work.data.get("staging", {}).items():
        if sid not in scene_ids:
            raise ValueError(f"[staging.{sid}]: unknown scene")
        for cid, pos in cfg.get("cast", {}).items():
            if cid not in cast:
                raise ValueError(f"[staging.{sid}].cast: unknown character {cid!r}")
            if pos not in VALID_POSITIONS:
                raise ValueError(f"[staging.{sid}].cast: unknown position {pos!r}")
        if "music" in cfg:
            check_music_spec(cfg["music"], f"[staging.{sid}]", allow_auto=False)
    for cue in work.data.get("cues", []):
        if "music" in cue:
            check_music_spec(cue["music"], f"cue at {cue['at']}", allow_auto=True)
        for cid, pos in cue.get("move", {}).items():
            if cid not in cast:
                raise ValueError(f"cue at {cue['at']}: move of unknown character {cid!r}")
            if pos not in VALID_POSITIONS:
                raise ValueError(f"cue at {cue['at']}: unknown position {pos!r}")


def apply_overrides(work: Work, annotated: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``annotated`` with ``[[overrides]]`` emotions applied (inclusive ranges)."""
    result = json.loads(json.dumps(annotated))
    where = {ln["id"]: (sc, i) for sc in result["scenes"] for i, ln in enumerate(sc["lines"])}
    for ov in work.data.get("overrides", []):
        if ov["emotion"] not in EMOTIONS:
            raise ValueError(
                f"override {ov['from']}..{ov['to']}: unknown emotion {ov['emotion']!r}"
            )
        for key in ("from", "to"):
            if ov[key] not in where:
                raise ValueError(f"override: unknown line id {ov[key]!r}")
        (scene, start), (scene_to, end) = where[ov["from"]], where[ov["to"]]
        if scene is not scene_to or end < start:
            raise ValueError(f"override {ov['from']}..{ov['to']}: not a range within one scene")
        for line in scene["lines"][start : end + 1]:
            line["ann"]["emotion"] = ov["emotion"]
    return result


def render_script(work: Work, annotated: dict[str, Any], audio: Audio | None = None) -> str:
    validate_staging(work, annotated)
    annotated = apply_overrides(work, annotated)
    out = [
        "## Generated by `vnpipe renpy`. Do not edit: regenerate instead.",
        "",
        "label start:",
        "    call vn_pick_art_style",
        "    window auto",
        "",
    ]
    known = {c["at"] for c in work.data.get("cues", [])}
    seen: set[str] = set()
    audio = audio or Audio()
    music_state: dict[str, str | None] = {"playing": UNKNOWN_MUSIC}
    for scene in annotated["scenes"]:
        out.extend(SceneWriter(work, scene, audio, music_state).render())
        seen |= {line["id"] for line in scene["lines"]}
    missing = known - seen
    if missing:
        raise ValueError(f"cues refer to unknown line ids: {sorted(missing)}")
    out += [
        "label the_end:",
        *(["    stop music fadeout 3.0"] if audio.files else []),
        "    scene black",
        "    with fade",
        "    show card_end at truecenter",
        "    with dissolve",
        f"    pause {END_PAUSE}",
        "    return",
        "",
    ]
    return "\n".join(out)


# --- GUI images the SDK template leaves out --------------------------------------------------
# `generate_gui` only makes a few GUI images; button, bar, scrollbar, slider, check and radio
# images are expected to exist (Ren'Py's prefix search falls back to the un-prefixed name).

ACCENT = (201, 162, 91)
IVORY = (239, 230, 210)
DARK = (20, 17, 14)


def _solid(size: tuple[int, int], color: tuple[int, ...]) -> Image.Image:
    return Image.new("RGBA", size, color)


def _check_image(selected: bool, round_: bool) -> Image.Image:
    img = Image.new("RGBA", (38, 38), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    box = [4, 4, 33, 33]
    shape = draw.ellipse if round_ else draw.rectangle
    shape(box, outline=IVORY + (255,), width=3)
    if selected:
        shape([12, 12, 25, 25], fill=ACCENT + (255,))
    return img


def _slot_image(border_alpha: int, fill_alpha: int) -> Image.Image:
    """Save-slot frame (Ren'Py scales it with gui.slot_button_borders): thin outline plus tint."""
    img = Image.new("RGBA", (48, 48), IVORY + (fill_alpha,))
    ImageDraw.Draw(img).rectangle([0, 0, 47, 47], outline=ACCENT + (border_alpha,), width=2)
    return img


def gui_extra_images() -> dict[str, Image.Image]:
    """Deterministic simple GUI images, keyed by path relative to ``game/``."""
    base: dict[str, Image.Image] = {
        "button/background.png": _solid((24, 24), (0, 0, 0, 0)),
        "button/hover_background.png": _solid((24, 24), ACCENT + (60,)),
        "button/selected_background.png": _solid((24, 24), ACCENT + (35,)),
        "button/slot_idle_background.png": _slot_image(110, 14),
        "button/slot_hover_background.png": _slot_image(255, 40),
        "button/check_foreground.png": _check_image(False, False),
        "button/check_selected_foreground.png": _check_image(True, False),
        "button/radio_foreground.png": _check_image(False, True),
        "button/radio_selected_foreground.png": _check_image(True, True),
        "bar/left.png": _solid((48, 48), ACCENT + (255,)),
        "bar/right.png": _solid((48, 48), IVORY + (60,)),
        "bar/top.png": _solid((48, 48), IVORY + (60,)),
        "bar/bottom.png": _solid((48, 48), ACCENT + (255,)),
        "slider/horizontal_bar.png": _solid((48, 48), IVORY + (60,)),
        "slider/horizontal_thumb.png": _solid((20, 38), ACCENT + (255,)),
        "slider/vertical_bar.png": _solid((48, 48), IVORY + (60,)),
        "slider/vertical_thumb.png": _solid((38, 20), ACCENT + (255,)),
        "scrollbar/horizontal_bar.png": _solid((48, 48), IVORY + (40,)),
        "scrollbar/horizontal_thumb.png": _solid((48, 48), ACCENT + (255,)),
        "scrollbar/vertical_bar.png": _solid((48, 48), IVORY + (40,)),
        "scrollbar/vertical_thumb.png": _solid((48, 48), ACCENT + (255,)),
    }
    result = dict(base)
    for path, img in base.items():
        result[f"phone/{path}"] = img
    return {f"gui/{path}": img for path, img in sorted(result.items())}


E2E_FILE = "e2e_test.rpy"
E2E_TESTCASE = """## Generated by `vnpipe renpy --with-test`: end-to-end playthrough (testing only).
## Run with `renpy.sh <project> test` (see docs/running.md).

# The test runner only advances when frames are drawn; an always-running transform keeps
# the software renderer (used headless) drawing, including on the main menu.
transform e2e_tick:
    xpos 0
    linear 0.05 xpos 1
    repeat

screen e2e_ticker():
    zorder 1000
    add Null(1, 1) at e2e_tick

init 999 python:
    config.always_shown_screens.append("e2e_ticker")
    e2e_state = {"end": False}

    def e2e_label(name, abnormal):
        if name == "the_end":
            e2e_state["end"] = True

    config.label_callbacks.append(e2e_label)

testsuite global:
    teardown:
        exit

testcase default:
    $ _test.transition_timeout = 0.05
    $ _test.timeout = 30.0
    $ preferences.text_cps = 0
    pause 1.0
    # Actions, not label text: stock menu labels may be translated.
    run Start()
@@PICK@@    advance until eval (e2e_state["end"]) timeout 900
    advance until screen "main_menu" timeout 60
@@MENUS@@    assert eval (e2e_state["end"])
"""


def render_e2e(styles: dict[str, Any], ui: dict[str, str]) -> str:
    """The playthrough testcase; with a style choice it also exercises the first-launch picker
    (picking the last style, so the non-default switch branch is the one in use)."""
    pick = ""
    if len(styles) > 1:
        sid, cfg = list(styles.items())[-1]
        pick = (
            "    pause 1.0\n"
            '    assert screen "vn_art_style_picker"\n'
            f"    click {_plain_quote(cfg['label'])}\n"
            f"    assert eval (persistent.art_style == {sid!r})\n"
        )
    menus = (
        f"    click {_plain_quote(ui['credits'])}\n"
        '    assert screen "vn_credits"\n'
        "    run Return()\n"
        '    assert screen "main_menu"\n'
        f"    click {_plain_quote(ui['scenes'])}\n"
        '    assert screen "vn_scene_select"\n'
        "    run Return()\n"
        '    assert screen "main_menu"\n'
    )
    return E2E_TESTCASE.replace("@@PICK@@", pick).replace("@@MENUS@@", menus)


# --- static project files -----------------------------------------------------------------


def render_characters(work: Work) -> str:
    cast = dict(work.cast)
    cast.setdefault("narrator", {"name": "", "color": "#d8cfc0"})
    out = ["## Generated by `vnpipe renpy`. Do not edit: regenerate instead.", ""]
    for cid, info in cast.items():
        name = info.get("name") or ""
        color = info.get("color")
        who = quote(name) if name else "None"
        props: list[str] = []
        if name and color:
            props.append(f"who_color={_plain_quote(color)}")
        if not name and color:
            props.append(f"what_color={_plain_quote(color)}")
        if cid == "narrator":
            props.append("what_italic=True")
        props += ['ctc="vn_ctc"', 'ctc_position="nestled"']
        adv = ", ".join([who, *props])
        nvl_props = ["kind=nvl", *props]
        if not name:
            nvl_props.append('what_style="nvl_thought"')
        out.append(f"define {char_var(cid)} = Character({adv})")
        out.append(f"define {char_var(cid, nvl=True)} = Character({', '.join([who, *nvl_props])})")
    out.append("")
    return "\n".join(out)


def title_cards(work: Work, annotated: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """Image name -> (heading, optional subtitle line: place/time, from ``[[scenes]].subtitle``)."""
    subtitles = {sc["id"]: sc.get("subtitle") for sc in work.data.get("scenes", [])}
    cards: dict[str, tuple[str, str | None]] = {
        f"card_{sc['id']}": (sc["heading"], subtitles.get(sc["id"])) for sc in annotated["scenes"]
    }
    cards["card_end"] = (work.data["work"].get("end_text", DEFAULT_END_TEXT), None)
    return cards


def render_card(heading: str, subtitle: str | None) -> str:
    """A spaced-out heading; with a subtitle, a thin gold rule and a smaller italic line below."""
    props = "size=76, kerning=3, text_align=0.5, xmaximum=1600"
    if not subtitle:
        return f'Text("{escape_text(heading)}", {props})'
    sub_props = 'size=34, italic=True, color="#c9a25b", text_align=0.5, xmaximum=1600'
    return (
        f'VBox(Text("{escape_text(heading)}", {props}, xalign=0.5), '
        'Solid("#c9a25b", xsize=200, ysize=2, xalign=0.5), '
        f'Text("{escape_text(subtitle)}", {sub_props}, xalign=0.5), spacing=26)'
    )


def render_image_entry(e: ImageEntry) -> str:
    """Plain image, or a ConditionSwitch on ``persistent.art_style`` with the default last."""
    default_rel = e.variants[0][1]
    if len(e.variants) == 1:
        return f"image {e.name} = {_plain_quote('images/' + default_rel)}"
    parts = []
    for style, rel, _ in e.variants[1:]:
        parts.append(
            f"{_plain_quote(f'persistent.art_style == {style!r}')}, {_plain_quote('images/' + rel)}"
        )
    parts.append(f'"True", {_plain_quote("images/" + default_rel)}')
    return f"image {e.name} = ConditionSwitch({', '.join(parts)})"


def render_images(entries: list[ImageEntry], cards: dict[str, tuple[str, str | None]]) -> str:
    out = ["## Generated by `vnpipe renpy`. Do not edit: regenerate instead.", ""]
    for name, (heading, subtitle) in cards.items():  # title cards: plain images, not `show text`
        out.append(f"image {name} = {render_card(heading, subtitle)}")
    out.extend(render_image_entry(e) for e in entries)
    out.append("")
    return "\n".join(out)


def _about(work: Work) -> str:
    info = work.data["work"]
    text_l, trans_l, tail = ABOUT_LABELS.get(info.get("language", "es"), ABOUT_LABELS["en"])
    parts = [info.get("subtitle", "")]
    if info.get("author"):
        parts.append(f"{text_l}: {info['author']}")
    if info.get("translator"):
        parts.append(f"{trans_l}: {info['translator']}")
    parts.append(tail)
    return "\\n\\n".join(escape_text(p) for p in parts if p)


def ui_labels(work: Work) -> dict[str, str]:
    return UI_LABELS.get(work.data["work"].get("language", "es"), UI_LABELS["en"])


def _markup(text: str) -> str:
    """Escape Ren'Py text markup only (for strings shown by `text`, not quoted in a script)."""
    return text.replace("[", "[[").replace("{", "{{")


def credit_tracks(work: Work) -> list[dict[str, Any]]:
    """Tracks of ``<work>/audio/credits.toml`` whose file ships with the game."""
    path = work.path("audio/credits.toml")
    if not path.is_file():
        return []
    with path.open("rb") as fh:
        tracks = tomllib.load(fh).get("track", [])
    return [t for t in tracks if work.path(t["file"]).is_file()]


def art_sources(manifest: dict[str, Any] | None) -> list[str]:
    """Display names of the generators behind the shipped (non-placeholder) renditions."""
    found = set()
    for asset in (manifest or {}).get("assets", []):
        for rendition in asset["renditions"].values():
            source = rendition.get("source")
            if source and source != "placeholder" and rendition["status"] != "placeholder":
                found.add(ART_SOURCE_NAMES.get(source, source))
    return sorted(found)


def credits_sections(work: Work, manifest: dict[str, Any] | None) -> list[tuple[str, list[str]]]:
    info = work.data["work"]
    ui = ui_labels(work)
    sections: list[tuple[str, list[str]]] = []
    text = [info["author"]] if info.get("author") else []
    if info.get("translator"):
        text.append(f"{ui['translation']}: {info['translator']}")
    gutenberg = work.data.get("source", {}).get("gutenberg_id")
    if gutenberg:
        text.append(
            f"{ui['source']}: {ui['gutenberg'].format(id=gutenberg)}"
            f" (https://www.gutenberg.org/ebooks/{gutenberg})"
        )
    if text:
        sections.append((ui["text"], text))
    sources = art_sources(manifest)
    if sources:
        sections.append((ui["art"], [ui["art_with"].format(", ".join(sources))]))
    tracks = credit_tracks(work)
    if tracks:  # attributions are printed verbatim (CC BY-SA requires them)
        lang = info.get("language", "es")
        # `attribution_<language>` is the same credit translated, used when present
        sections.append(
            (ui["music"], [t.get(f"attribution_{lang}") or t["attribution"] for t in tracks])
        )
    about_tail = ABOUT_LABELS.get(info.get("language", "es"), ABOUT_LABELS["en"])[2]
    sections.append(("vnpipe", [about_tail]))
    return [(title, [_markup(e) for e in entries]) for title, entries in sections]


def scene_menu_titles(work: Work, annotated: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, title) per scene; a repeated heading gets its subtitle, else its number."""
    subtitles = {sc["id"]: sc.get("subtitle") for sc in work.data.get("scenes", [])}
    headings = [sc["heading"] for sc in annotated["scenes"]]
    seen: dict[str, int] = {}
    result = []
    for sc in annotated["scenes"]:
        title = sc["heading"]
        if headings.count(title) > 1:
            seen[title] = seen.get(title, 0) + 1
            title += f" ({subtitles.get(sc['id']) or seen[title]})"
        result.append((f"scene_{sc['id']}", _markup(title)))
    return result


def render_menu_data(work: Work, annotated: dict[str, Any], manifest: dict[str, Any] | None) -> str:
    def py(value: Any) -> str:  # JSON strings are valid Python literals
        return json.dumps(value, ensure_ascii=False)

    subtitle = _markup(work.data["work"].get("subtitle", ""))
    return "\n".join(
        [
            "## Generated by `vnpipe renpy`. Do not edit: regenerate instead.",
            "",
            f"define vn_subtitle = {py(subtitle)}",
            f"define vn_scenes = {py(scene_menu_titles(work, annotated))}",
            f"define vn_credits = {py(credits_sections(work, manifest))}",
            "",
        ]
    )


def _substitute(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace(f"@@{key}@@", value)
    if "@@" in text:
        raise ValueError("unreplaced template token")
    return text


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def generate(
    work: Work, annotated: dict[str, Any], out: Path, with_test: bool = False
) -> list[Path]:
    """Write the Ren'Py project into ``out``; returns every generated file (sorted)."""
    info = work.data["work"]
    manifest_path = work.path("assets/assets.json")
    manifest = load_manifest(work.root) if manifest_path.is_file() else None
    entries = collect_images(work, manifest)
    game = out / "game"
    for sub in ("images", "gui", "fonts"):
        shutil.rmtree(game / sub, ignore_errors=True)
    game.mkdir(parents=True, exist_ok=True)

    # GUI template: copy binaries, substitute tokens in .rpy files.
    menu_bg = MENU_BG_FALLBACK
    staging = work.data.get("staging", {})
    first_bg = (
        work.data.get("ui", {}).get("main_menu_bg")
        or info.get("menu_bg")
        or next((c["bg"] for c in staging.values() if c.get("bg")), None)
    )
    if first_bg:
        menu_bg = bg_image_name(first_bg)
    styles, default_style = art_styles(manifest)
    picker_title, picker_hint = ART_STYLE_LABELS.get(
        info.get("language", "es"), ART_STYLE_LABELS["en"]
    )
    ui = ui_labels(work)
    values = {
        "ART_STYLES": repr([(sid, cfg["label"]) for sid, cfg in styles.items()]),
        "DEFAULT_STYLE": default_style,
        "ART_STYLE_TITLE": picker_title,
        "ART_STYLE_HINT": picker_hint,
        "TITLE": escape_text(info["title"]),
        "VERSION": "0.1",
        "ABOUT": _about(work),
        "BUILD_NAME": f"{info['id']}-vn",
        "SAVE_DIR": f"{info['id']}-vn",
        "MENU_BG": menu_bg,
        "UI_SCENES": ui["scenes"],
        "UI_CREDITS": ui["credits"],
        "LOCKED": LOCKED_SCENE,
    }
    written: list[Path] = []
    for src in sorted((TEMPLATE_DIR / "game").rglob("*")):
        if not src.is_file():
            continue
        dest = game / src.relative_to(TEMPLATE_DIR / "game")
        dest.parent.mkdir(parents=True, exist_ok=True)
        written.append(dest)
        if src.suffix == ".rpy":
            write_text(dest, _substitute(src.read_text(encoding="utf-8"), values))
        else:
            shutil.copyfile(src, dest)

    for rel, img in gui_extra_images().items():
        dest = game / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():  # never override an image shipped by the template
            img.save(dest, "PNG")
            written.append(dest)
    written.extend(copy_audio(work, out))
    written.extend(copy_translations(work, out))
    test_file = game / E2E_FILE
    test_file.unlink(missing_ok=True)
    if with_test:
        write_text(test_file, render_e2e(styles, ui))
        written.append(test_file)
    for name, text in (
        ("characters.rpy", render_characters(work)),
        ("images.rpy", render_images(entries, title_cards(work, annotated))),
        ("menu_data.rpy", render_menu_data(work, annotated, manifest)),
        ("script.rpy", render_script(work, annotated, scan_audio(out))),
    ):
        write_text(game / name, text)
        written.append(game / name)

    for e in entries:
        for _, rel, source in e.variants:
            dest = game / "images" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            written.append(dest)
            if source:
                shutil.copyfile(source, dest)
            else:
                placeholder(e.asset, label=False).save(dest, "WEBP", quality=90)

    return sorted(written)


# --- CLI ----------------------------------------------------------------------------------


def cmd_renpy(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    if args.from_parsed:
        parsed = json.loads(work.path("data/parsed.json").read_text(encoding="utf-8"))
        validate(parsed, "parsed")
        annotated = annotations_from_parsed(work, parsed)
    else:
        path = work.path("data/annotated.json")
        if not path.is_file():
            raise SystemExit(f"renpy: {path} missing; run annotation or pass --from-parsed")
        annotated = json.loads(path.read_text(encoding="utf-8"))
    validate(annotated, "annotated")
    out = args.out or work.path("vn")
    files = generate(work, annotated, out, with_test=args.with_test)
    print(f"renpy: wrote {len(files)} files under {out}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("renpy", help="Generate the Ren'Py project")
    parser.add_argument(
        "--from-parsed",
        action="store_true",
        help="Build trivial annotations from parsed.json instead of reading annotated.json",
    )
    parser.add_argument(
        "--with-test",
        action="store_true",
        help=f"Also write game/{E2E_FILE}: an end-to-end playthrough for `renpy.sh <dir> test`",
    )
    parser.add_argument("--out", type=Path, help="Output directory (default: <work root>/vn)")
    parser.set_defaults(handler=cmd_renpy)
