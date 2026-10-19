"""Annotation stage: chunk parsed.json for LLM annotators, track the cache and merge.

LLM sub-agents (run by the orchestrator) read ``annotations/chunks/<chunk_id>.json`` and write
``annotations/cache/<chunk_id>.json``. This module only prepares chunks, checks cache state and
merges; everything here is deterministic and idempotent.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Any

from vnpipe import PACKAGE_DIR, SCHEMA_DIR
from vnpipe.config import Work, load_work
from vnpipe.schema import SchemaError, validate

MAX_CHUNK_LINES = 60
CONTEXT_LINES = 6
NARRATOR = "narrator"
PROMPT_PATH = PACKAGE_DIR / "prompts" / "annotate.md"
PRESET_POSITIONS = ("left", "right", "center")  # order used by the fallback
DEFAULT_FIELDS = ("speaker", "kind", "location", "time_of_day")
ANN_ORDER = ("speaker", "kind", "emotion", "location", "time_of_day", "on_stage", "music")

OK, MISSING, STALE, INVALID = "ok", "missing", "stale", "invalid"


class AnnotateError(ValueError):
    """Inconsistent input (parsed.json against work.toml)."""


# --------------------------------------------------------------------------- helpers


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def canonical_sha256(data: Any) -> str:
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def prompt_version() -> str:
    return hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()


def chunks_dir(work: Work) -> Path:
    return work.path("annotations/chunks")


def cache_dir(work: Work) -> Path:
    return work.path("annotations/cache")


def _schema_enums() -> dict[str, list[str]]:
    defs = json.loads((SCHEMA_DIR / "annotation.schema.json").read_text(encoding="utf-8"))["$defs"]
    props = defs["annotation"]["properties"]
    return {
        "emotions": list(props["emotion"]["enum"]),
        "positions": list(defs["position"]["enum"]),
        "music": [m for m in props["music"]["enum"] if m is not None],
    }


def lyric_voice(work: Work) -> str:
    return work.data["work"].get("lyric_voice", NARRATOR)


def allowed_for(work: Work) -> dict[str, list[str]]:
    """Vocabularies an annotation may use, derived from work.toml and the schema."""
    cast = list(work.cast)
    voice = lyric_voice(work)
    if NARRATOR not in cast:
        cast.insert(0, NARRATOR)
    if voice not in cast:
        raise AnnotateError(f"work.lyric_voice {voice!r} is not a cast id")
    locations: list[str] = []
    for scene in work.scenes:
        if scene["location"] not in locations:
            locations.append(scene["location"])
    return {
        "speakers": cast,
        "characters": [c for c in cast if c not in (NARRATOR, voice)],
        "locations": locations,
        **_schema_enums(),
    }


def has_sprite(work: Work, character: str) -> bool:
    return bool(work.cast.get(character, {}).get("sprite", True))


# --------------------------------------------------------------------------- chunking


def _pack(lines: list[dict], keys: tuple[str, ...], limit: int) -> list[list[dict]]:
    """Split ``lines`` into pieces of at most ``limit``, preferring boundaries of ``keys[0]``,
    then ``keys[1]`` ..., and finally cutting anywhere."""
    if len(lines) <= limit:
        return [lines]
    if not keys:
        return [lines[i : i + limit] for i in range(0, len(lines), limit)]
    pieces: list[list[dict]] = []
    current: list[dict] = []
    for _, group in groupby(lines, key=lambda ln: ln[keys[0]]):
        block = list(group)
        if len(block) > limit:
            if current:
                pieces.append(current)
                current = []
            pieces.extend(_pack(block, keys[1:], limit))
        elif len(current) + len(block) > limit:
            pieces.append(current)
            current = block
        else:
            current += block
    if current:
        pieces.append(current)
    return pieces


def _line_defaults(work: Work, scene_cfg: dict, line: dict, voice: str) -> dict[str, str]:
    kind, speaker = line["kind"], line["speaker"]
    if kind == "verse" and speaker:
        ann_kind, ann_speaker = "dialogue", speaker
    elif kind == "verse":
        ann_kind, ann_speaker = "narration", voice
    else:
        ann_kind, ann_speaker = "stage_direction", NARRATOR
    if ann_speaker not in work.cast and ann_speaker != NARRATOR:
        raise AnnotateError(f"line {line['id']}: speaker {ann_speaker!r} is not in work.toml cast")
    return {
        "speaker": ann_speaker,
        "kind": ann_kind,
        "location": scene_cfg["location"],
        "time_of_day": scene_cfg["time_of_day"],
    }


def build_chunks(work: Work, parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministically split parsed scenes into annotation chunks (with input hashes)."""
    scene_cfgs = {s["id"]: s for s in work.scenes}
    allowed = allowed_for(work)
    voice = lyric_voice(work)
    version = prompt_version()
    chunks: list[dict[str, Any]] = []
    for scene in parsed["scenes"]:
        cfg = scene_cfgs.get(scene["id"])
        if cfg is None:
            raise AnnotateError(f"scene {scene['id']!r} is not configured in work.toml")
        lines = scene["lines"]
        start = 0
        for number, piece in enumerate(_pack(lines, ("speech", "stanza"), MAX_CHUNK_LINES), 1):
            before = [
                {
                    "id": ln["id"],
                    "speaker": _line_defaults(work, cfg, ln, voice)["speaker"],
                    "text": ln["text"],
                }
                for ln in lines[max(0, start - CONTEXT_LINES) : start]
            ]
            start += len(piece)
            chunk: dict[str, Any] = {
                "chunk_id": f"{scene['id']}_c{number:02d}",
                "prompt_version": version,
                "scene": {
                    "id": scene["id"],
                    "heading": scene["heading"],
                    "location": cfg["location"],
                    "time_of_day": cfg["time_of_day"],
                },
                "allowed": allowed,
                "context_before": before,
                "lines": [
                    {
                        "id": ln["id"],
                        "kind": ln["kind"],
                        "speaker": ln["speaker"],
                        "speaker_note": ln["speaker_note"],
                        "text": ln["text"],
                        "defaults": _line_defaults(work, cfg, ln, voice),
                    }
                    for ln in piece
                ],
            }
            chunk["input_sha256"] = canonical_sha256(chunk)
            chunks.append(chunk)
    return chunks


def load_parsed(work: Work) -> tuple[dict[str, Any], bytes]:
    path = work.path("data/parsed.json")
    if not path.is_file():
        raise SystemExit(f"{path} missing; run `vnpipe parse` first")
    raw = path.read_bytes()
    parsed = json.loads(raw)
    validate(parsed, "parsed")
    return parsed, raw


def write_chunks(work: Work, chunks: list[dict[str, Any]]) -> list[Path]:
    """Write chunk files and remove stale ones; returns the removed paths."""
    out = chunks_dir(work)
    out.mkdir(parents=True, exist_ok=True)
    keep = {f"{c['chunk_id']}.json" for c in chunks}
    for chunk in chunks:
        _write(out / f"{chunk['chunk_id']}.json", dumps(chunk))
    removed = sorted(p for p in out.glob("*.json") if p.name not in keep)
    for path in removed:
        path.unlink()
    return removed


# --------------------------------------------------------------------------- checking


def _schema_reason(exc: SchemaError) -> str:
    return "; ".join(s.strip() for s in str(exc).splitlines()[1:4])


def check_cache(work: Work, chunk: dict[str, Any]) -> tuple[str, list[str]]:
    """Classify the cache file of ``chunk``; returns (state, problems)."""
    path = cache_dir(work) / f"{chunk['chunk_id']}.json"
    if not path.is_file():
        return MISSING, ["no cache file"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return INVALID, [f"unreadable JSON: {exc}"]
    try:
        validate(data, "annotation")
    except SchemaError as exc:
        return INVALID, [f"schema: {_schema_reason(exc)}"]
    if data["chunk_id"] != chunk["chunk_id"]:
        return INVALID, [f"chunk_id is {data['chunk_id']!r}"]
    if data["input_sha256"] != chunk["input_sha256"]:
        return STALE, ["input_sha256 does not match the current chunk"]
    problems = _semantic_problems(chunk, data["annotations"])
    return (INVALID, problems) if problems else (OK, [])


def _semantic_problems(chunk: dict[str, Any], annotations: list[dict]) -> list[str]:
    problems: list[str] = []
    expected = [ln["id"] for ln in chunk["lines"]]
    got = [a["id"] for a in annotations]
    if got != expected:
        missing = [i for i in expected if i not in got]
        extra = [i for i in got if i not in expected]
        dupes = sorted({i for i in got if got.count(i) > 1})
        if missing:
            problems.append(
                f"missing lines: {', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}"
            )
        if extra:
            problems.append(f"unknown line ids: {', '.join(extra[:5])}")
        if dupes:
            problems.append(f"duplicate line ids: {', '.join(dupes[:5])}")
        if not (missing or extra or dupes):
            problems.append("lines are not in chunk order")
        return problems
    characters = set(chunk["allowed"]["characters"])
    for line, ann in zip(chunk["lines"], annotations, strict=True):
        for key in DEFAULT_FIELDS:
            if ann[key] != line["defaults"][key]:
                problems.append(
                    f"{line['id']}: {key} is {ann[key]!r}, expected {line['defaults'][key]!r}"
                )
        seen: set[str] = set()
        for slot in ann["on_stage"]:
            who = slot["character"]
            if who not in characters:
                problems.append(f"{line['id']}: unknown character {who!r} on stage")
            elif who in seen:
                problems.append(f"{line['id']}: {who!r} on stage more than once")
            seen.add(who)
    return problems


# --------------------------------------------------------------------------- fallback


def fallback_annotations(work: Work, scene_chunks: list[dict[str, Any]]) -> dict[str, list[dict]]:
    """Deterministic annotations for every chunk of one scene, keyed by chunk id."""
    seen: list[str] = []  # speakers with a sprite, in order of appearance in the scene
    result: dict[str, list[dict]] = {}
    for chunk in scene_chunks:
        anns = []
        for line in chunk["lines"]:
            who = line["defaults"]["speaker"]
            if (
                who in chunk["allowed"]["characters"]
                and has_sprite(work, who)
                and who not in seen
                and len(seen) < len(PRESET_POSITIONS)
            ):
                seen.append(who)
            anns.append(
                {
                    "id": line["id"],
                    **line["defaults"],
                    "emotion": "neutral",
                    "on_stage": [
                        {"character": c, "position": PRESET_POSITIONS[i]}
                        for i, c in enumerate(seen)
                    ],
                    "music": None,
                }
            )
        result[chunk["chunk_id"]] = anns
    return result


def _normalise(ann: dict[str, Any]) -> dict[str, Any]:
    out = {key: ann.get(key) for key in ANN_ORDER}
    out["on_stage"] = [
        {"character": s["character"], "position": s["position"]} for s in ann["on_stage"]
    ]
    return out


# --------------------------------------------------------------------------- merge


@dataclass
class MergeResult:
    document: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def merge(work: Work, fallback: bool = False) -> MergeResult:
    parsed, raw = load_parsed(work)
    chunks = build_chunks(work, parsed)
    result = MergeResult()
    states = {c["chunk_id"]: check_cache(work, c) for c in chunks}
    bad = [c["chunk_id"] for c in chunks if states[c["chunk_id"]][0] != OK]
    if bad and not fallback:
        for cid in bad:
            state, why = states[cid]
            result.problems.append(f"{cid}: {state}: {'; '.join(why)}")
        return result

    by_scene: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_scene.setdefault(chunk["scene"]["id"], []).append(chunk)
    anns: dict[str, dict[str, Any]] = {}  # line id -> normalised annotation
    for scene_chunks in by_scene.values():
        fb = fallback_annotations(work, scene_chunks) if bad else {}
        for chunk in scene_chunks:
            cid = chunk["chunk_id"]
            if states[cid][0] == OK:
                data = json.loads((cache_dir(work) / f"{cid}.json").read_text(encoding="utf-8"))
                items = data["annotations"]
            else:
                items = fb[cid]
                state, why = states[cid]
                result.warnings.append(f"{cid}: {state} ({'; '.join(why)}), using fallback")
            for item in items:
                anns[item["id"]] = _normalise(item)

    doc = {
        "work": parsed["work"],
        "parsed_sha256": hashlib.sha256(raw).hexdigest(),
        "fallback_chunks": bad,
        "scenes": [
            {
                "id": scene["id"],
                "heading": scene["heading"],
                "lines": [{**ln, "ann": anns[ln["id"]]} for ln in scene["lines"]],
            }
            for scene in parsed["scenes"]
        ],
    }
    validate(doc, "annotated")
    result.document = doc
    return result


# --------------------------------------------------------------------------- commands


def cmd_chunk(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    parsed, _ = load_parsed(work)
    chunks = build_chunks(work, parsed)
    removed = write_chunks(work, chunks)
    for chunk in chunks:
        print(f"chunk: {chunk['chunk_id']}: {len(chunk['lines'])} lines")
    for path in removed:
        print(f"chunk: removed stale {path.name}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    parsed, _ = load_parsed(work)
    chunks = build_chunks(work, parsed)
    wanted = set(args.chunk_ids)
    unknown = wanted - {c["chunk_id"] for c in chunks}
    if unknown:
        print(f"status: unknown chunk id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 0
    counts: dict[str, int] = {}
    for chunk in chunks:
        if wanted and chunk["chunk_id"] not in wanted:
            continue
        state, why = check_cache(work, chunk)
        counts[state] = counts.get(state, 0) + 1
        suffix = f"  ({'; '.join(why)})" if why and state != MISSING else ""
        print(f"{chunk['chunk_id']}: {state}{suffix}")
    stale_files = {p.name for p in chunks_dir(work).glob("*.json")} ^ {
        f"{c['chunk_id']}.json" for c in chunks
    }
    on_disk_differs = bool(stale_files) or any(
        _read_json(chunks_dir(work) / f"{c['chunk_id']}.json") != c for c in chunks
    )
    if on_disk_differs:
        print("note: chunk files are out of date; run `vnpipe annotate chunk`")
    summary = ", ".join(f"{counts.get(s, 0)} {s}" for s in (OK, MISSING, STALE, INVALID))
    print(f"summary: {sum(counts.values())} chunks: {summary}")
    return 0


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cmd_merge(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    result = merge(work, fallback=args.fallback)
    if result.document is None:
        print("merge: refusing to merge, problems found:", file=sys.stderr)
        for problem in result.problems:
            print(f"  {problem}", file=sys.stderr)
        print("merge: fix them or use --fallback; nothing written", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    out = work.path("data/annotated.json")
    _write(out, dumps(result.document))
    n_lines = sum(len(s["lines"]) for s in result.document["scenes"])
    n_fb = len(result.document["fallback_chunks"])
    print(f"merge: wrote {out} ({n_lines} lines, {n_fb} fallback chunks)")
    return 0


def render_prompt(work: Work, chunk_id: str) -> str:
    parsed, _ = load_parsed(work)
    ids = {c["chunk_id"] for c in build_chunks(work, parsed)}
    if chunk_id not in ids:
        raise SystemExit(f"prompt: unknown chunk id {chunk_id!r}")
    repo = PACKAGE_DIR.parent.parent
    values = {
        "chunk_id": chunk_id,
        "chunk_path": str((chunks_dir(work) / f"{chunk_id}.json").resolve()),
        "cache_path": str((cache_dir(work) / f"{chunk_id}.json").resolve()),
        "status_cmd": f"{repo / '.venv/bin/vnpipe'} --work {work.root.resolve()} annotate status",
    }
    text = PROMPT_PATH.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def cmd_prompt(args: argparse.Namespace) -> int:
    sys.stdout.write(render_prompt(load_work(args.work), args.chunk_id))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    annotate = subparsers.add_parser("annotate", help="LLM annotation stage tooling")
    sub = annotate.add_subparsers(dest="annotate_command", required=True)
    sub.add_parser("chunk", help="Write annotation chunks").set_defaults(handler=cmd_chunk)
    status = sub.add_parser("status", help="Show cache state per chunk")
    status.add_argument("chunk_ids", nargs="*", help="Limit to these chunks")
    status.set_defaults(handler=cmd_status)
    merge_cmd = sub.add_parser("merge", help="Validate caches and write data/annotated.json")
    merge_cmd.add_argument(
        "--fallback", action="store_true", help="Fill non-ok chunks with deterministic defaults"
    )
    merge_cmd.set_defaults(handler=cmd_merge)
    prompt = sub.add_parser("prompt", help="Print the annotator instructions for one chunk")
    prompt.add_argument("chunk_id")
    prompt.set_defaults(handler=cmd_prompt)
