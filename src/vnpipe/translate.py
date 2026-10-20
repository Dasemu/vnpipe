"""Translations: show the play in another language, from a published public-domain translation.

Stages (deterministic; see docs/translations.md):
    fetch   download the translation's text (a Project Gutenberg plain-text ebook)
    build   parse it into speeches and stage directions, map them onto the game's speeches by
            speaker sequence -> data/translations/<code>.json
    status  coverage per scene (worst first); ``--scene`` prints the alignment side by side

``vnpipe renpy`` then writes ``game/tl/<language>/`` from the JSON. Nothing is machine
translated: every line comes verbatim from the configured translation.
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from vnpipe.config import Work, load_work
from vnpipe.schema import validate
from vnpipe.translate_align import (
    SETTING,
    GameUnit,
    Unit,
    align_units,
    find_section_lines,
    game_units,
    group_units,
    parse_units,
    strip_gutenberg,
)
from vnpipe.voice import download

OUT_DIR = "data/translations"
SUSPECT_RATIO = 2.5  # a group whose size ratio is this far from the scene's is flagged
SUSPECT_WORDS = 25  # ... when it is at least this big (game + source words)


# --- configuration -------------------------------------------------------------------------


def translations(work: Work) -> dict[str, dict[str, Any]]:
    """``[translations.<code>]`` tables, by code (e.g. ``en``)."""
    return work.data.get("translations", {})


def translation_cfg(work: Work, code: str) -> dict[str, Any]:
    cfg = translations(work).get(code)
    if cfg is None:
        raise SystemExit(f"no [translations.{code}] table in work.toml")
    return cfg


# The keys of [translations.<code>] that change the mapping (the others, such as names and
# strings, are read by `vnpipe renpy` directly).
MAPPING_KEYS = (
    "text_file",
    "sections",
    "speakers",
    "scene_speakers",
    "direction_labels",
    "ignore_lines",
    "ensembles",
    "overrides",
)


def config_digest(cfg: dict[str, Any]) -> str:
    """Hash of the mapping configuration, stored in the JSON to detect a stale build."""
    picked = {k: cfg[k] for k in MAPPING_KEYS if k in cfg}
    return hashlib.sha256(json.dumps(picked, sort_keys=True).encode("utf-8")).hexdigest()


def output_path(work: Work, code: str) -> Path:
    return work.path(f"{OUT_DIR}/{code}.json")


def load_translation(work: Work, code: str) -> dict[str, Any] | None:
    path = output_path(work, code)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    validate(data, "translation")
    return data


def lyric_voice(work: Work) -> str:
    return work.data["work"].get("lyric_voice", "poet_voice")


# --- build ---------------------------------------------------------------------------------


def find_override(source: list[Unit], quote: str, scene_id: str) -> int:
    """Index of the source unit whose text starts with ``quote`` (verbatim, after markup
    cleaning); exactly one must match."""
    hits = [u.index for u in source if u.text.startswith(quote)]
    if len(hits) != 1:
        raise SystemExit(
            f"translate: override in {scene_id}: {len(hits)} source units start with {quote!r}"
        )
    return hits[0]


def build_scene(
    work: Work,
    cfg: dict[str, Any],
    scene: dict[str, Any],
    section: dict[str, Any],
    numbered: list[tuple[int, str]],
) -> dict[str, Any]:
    """Map one scene; returns its entry of the translation file."""
    sid = scene["id"]
    speakers = {**cfg.get("speakers", {}), **cfg.get("scene_speakers", {}).get(sid, {})}
    cast = work.cast
    for label, value in {**speakers, **cfg.get("direction_labels", {})}.items():
        for cid in value if isinstance(value, list) else [value]:
            if cid and cid not in cast:
                raise SystemExit(f"translate: label {label!r} maps to unknown cast id {cid!r}")
    scene_cfg = next((s for s in work.data.get("scenes", []) if s["id"] == sid), {})
    source = parse_units(
        numbered,
        speakers,
        default_speaker=section.get("default_speaker"),
        prose=scene_cfg.get("format") == "prose",
        ignore=frozenset(cfg.get("ignore_lines", [])),
        direction_labels=cfg.get("direction_labels", {}),
    )
    game = game_units(scene, lyric_voice(work))
    by_line = {u.first_line: u.index for u in game}
    forced: dict[int, Unit] = {}
    for ov in cfg.get("overrides", []):
        if ov["scene"] != sid:
            continue
        if ov["game"] not in by_line:
            raise SystemExit(f"translate: override {ov['game']!r} is not the start of a unit")
        forced[by_line[ov["game"]]] = source[find_override(source, ov["source"], sid)]
    # The setting (place and cast lines) is shown only where an override puts it.
    used = {id(u) for u in forced.values()}
    source = [u for u in source if u.label != SETTING or id(u) in used]
    for k, u in enumerate(source):
        u.index = k
    anchors = {g: u.index for g, u in forced.items()}
    ensembles = frozenset(cfg.get("ensembles", work.data.get("voice", {}).get("ensembles", [])))
    pairs = align_units(game, source, ensembles, anchors)
    carried = group_units(len(game), len(source), pairs)
    units: dict[str, list[dict[str, Any]]] = {}
    for g, sources in carried.items():
        units[game[g].first_line] = [
            {
                "kind": source[s].kind,
                "speaker": source[s].speaker or "narrator",
                "src_line": source[s].src_line,
                "stanzas": source[s].stanzas,
            }
            for s in sources
        ]
    return {
        "title": section.get("title", section["heading"]),
        "subtitle": section.get("subtitle"),
        "units": units,
        "stats": scene_stats(game, source, pairs, carried, set(anchors)),
    }


def scene_stats(
    game: list[GameUnit],
    source: list[Unit],
    pairs: dict[int, int],
    carried: dict[int, list[int]],
    forced: set[int],
) -> dict[str, Any]:
    """Counts for ``status``, and the groups whose sizes look wrong (to review)."""
    ratio = sum(u.words for u in game) / max(1, sum(u.words for u in source))
    heads = sorted(carried)
    suspects = []
    for k, head in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(game)
        g_words = sum(u.words for u in game[head:end])
        s_words = sum(source[s].words for s in carried[head])
        rel = (g_words / max(1, s_words)) / ratio
        if g_words + s_words >= SUSPECT_WORDS and not (1 / SUSPECT_RATIO <= rel <= SUSPECT_RATIO):
            suspects.append(game[head].first_line)
    paired = len(pairs)
    carrying = sum(1 for g in carried if g not in pairs)
    return {
        "game_units": len(game),
        "paired": paired,
        "carrying": carrying,
        "grouped": len(game) - paired - carrying,
        "source_units": len(source),
        "source_attached": len(source) - paired,
        "forced": len(forced),
        "coverage": round((paired + carrying) / len(game), 3) if game else 1.0,
        "complete": not game or 0 in carried,
        "suspects": suspects,
    }


def source_sections(work: Work, cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    path = work.path(cfg["text_file"])
    if not path.is_file():
        raise SystemExit(f"translate: {path} missing; run `vnpipe translate fetch`")
    raw = path.read_bytes()
    text = strip_gutenberg(raw.decode("utf-8"))
    sections = cfg["sections"]
    found = find_section_lines(text, [s["heading"] for s in sections])
    return hashlib.sha256(raw).hexdigest(), {
        s["scene"]: (s, lines) for s, lines in zip(sections, found, strict=True)
    }


def build(work: Work, code: str) -> dict[str, Any]:
    cfg = translation_cfg(work, code)
    digest, sections = source_sections(work, cfg)
    annotated = json.loads(work.path("data/annotated.json").read_text(encoding="utf-8"))
    scenes = {}
    for scene in annotated["scenes"]:
        if scene["id"] not in sections:
            raise SystemExit(f"translate: scene {scene['id']!r} has no [[sections]] entry")
        section, numbered = sections[scene["id"]]
        scenes[scene["id"]] = build_scene(work, cfg, scene, section, numbered)
    data = {
        "version": 1,
        "code": code,
        "language": cfg["language"],
        "source_sha256": digest,
        "annotated_sha256": hashlib.sha256(
            work.path("data/annotated.json").read_bytes()
        ).hexdigest(),
        "config_sha256": config_digest(cfg),
        "scenes": scenes,
    }
    validate(data, "translation")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )


# --- status --------------------------------------------------------------------------------


def status_lines(data: dict[str, Any]) -> list[str]:
    """One line per scene, worst coverage first."""
    rows = sorted(data["scenes"].items(), key=lambda kv: (kv[1]["stats"]["coverage"], kv[0]))
    out = [
        f"{'scene':<20} {'coverage':>8} {'paired':>7} {'carry':>6} {'grouped':>8} "
        f"{'src':>5} {'attached':>8} {'forced':>6}  suspects"
    ]
    for sid, sc in rows:
        st = sc["stats"]
        out.append(
            f"{sid:<20} {st['coverage']:>8.1%} {st['paired']:>7} {st['carrying']:>6} "
            f"{st['grouped']:>8} {st['source_units']:>5} {st['source_attached']:>8} "
            f"{st['forced']:>6}  {' '.join(st['suspects'])}"
        )
    return out


def alignment_lines(work: Work, data: dict[str, Any], scene_id: str) -> list[str]:
    """Side-by-side view of one scene: each game unit and the source text it shows."""
    annotated = json.loads(work.path("data/annotated.json").read_text(encoding="utf-8"))
    scene = next(s for s in annotated["scenes"] if s["id"] == scene_id)
    lines = {ln["id"]: ln for ln in scene["lines"]}
    units = data["scenes"][scene_id]["units"]
    suspects = set(data["scenes"][scene_id]["stats"]["suspects"])
    out = []
    for unit in game_units(scene, lyric_voice(work)):
        first = lines[unit.first_line]["text"]
        mark = "!" if unit.first_line in suspects else " "
        out.append(f"{mark}{unit.first_line} [{unit.speaker or 'dir'} {unit.words}w] {first[:60]}")
        for item in units.get(unit.first_line, []):
            text = " / ".join(ln for st in item["stanzas"] for ln in st)
            words = len(text.split())
            out.append(f"      -> {item['speaker']} {words}w @{item['src_line']}: {text[:70]}")
        if unit.first_line not in units:
            out.append("      (grouped)")
    return out


# --- CLI -----------------------------------------------------------------------------------


def codes(work: Work, wanted: str | None) -> list[str]:
    all_codes = list(translations(work))
    if not all_codes:
        raise SystemExit("this work has no [translations.<code>] table in work.toml")
    picked = wanted.split(",") if wanted else all_codes
    for code in picked:
        translation_cfg(work, code)
    return picked


def cmd_fetch(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    for code in codes(work, args.lang):
        cfg = translation_cfg(work, code)
        dest = work.path(cfg["text_file"])
        if dest.exists():
            print(f"fetch: {dest} already present")
        else:
            download(cfg["text_url"], dest)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    for code in codes(work, args.lang):
        data = build(work, code)
        write_json(output_path(work, code), data)
        print(f"build: wrote {output_path(work, code)}")
        for line in status_lines(data):
            print(line)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    for code in codes(work, args.lang):
        data = load_translation(work, code)
        if data is None:
            raise SystemExit(f"status: {output_path(work, code)} missing; run `translate build`")
        print(f"[{code}] {data['language']}")
        lines = alignment_lines(work, data, args.scene) if args.scene else status_lines(data)
        for line in lines:
            print(line)
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("translate", help="Map a published translation onto the game")
    sub = parser.add_subparsers(dest="translate_command", required=True)

    def add(name: str, help_: str, handler: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--lang", help="Comma-separated translation codes (default: all)")
        p.set_defaults(handler=handler)
        return p

    add("fetch", "Download the translation text", cmd_fetch)
    add("build", "Parse and map the translation -> data/translations/<code>.json", cmd_build)
    status = add("status", "Coverage per scene, worst first", cmd_status)
    status.add_argument("--scene", help="Print one scene's alignment side by side")
