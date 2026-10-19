"""Annotation stage: a cheap LLM labels speeches; everything else is deterministic.

The model only decides the emotion of each speech (with optional changes inside it), the music
mood and entrances/exits of sprite characters, answering in a tiny line-based format. Speaker,
kind, location, time of day and stage positions are derived from ``parsed.json`` and
``work.toml`` when the answers are expanded into ``data/annotated.json``.

    parsed.json --chunks--> rendered text --runner--> annotations/cache/<chunk>.txt
                                                      --expand--> data/annotated.json
"""

import argparse
import fnmatch
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Any

from vnpipe import PACKAGE_DIR
from vnpipe.config import Work, load_work
from vnpipe.schema import validate

MAX_CHUNK_LINES = 300
NARRATOR = "narrator"
PROMPT_PATH = PACKAGE_DIR / "prompts" / "annotate.md"
EMOTIONS = ("neutral", "joyful", "melancholic", "solemn", "ironic", "angry", "fearful", "ecstatic")
MUSIC = ("contemplative", "comic", "sacred", "ominous", "mystical", "none")
POSITIONS = ("left", "center", "right")
DEFAULT_MODEL = "haiku"
# MAX_THINKING_TOKENS=0 matters: with thinking on, Haiku spends ~10k hidden output tokens per
# chunk (50x the answer) and the call takes minutes.
DEFAULT_RUNNER = (
    'env MAX_THINKING_TOKENS=0 claude -p --model {model} --tools "" --strict-mcp-config '
    '--setting-sources "" '
    "--system-prompt-file {prompt_file} --output-format json --no-session-persistence"
)
RUNNER_TIMEOUT = 900
ANN_ORDER = ("speaker", "kind", "emotion", "location", "time_of_day", "on_stage", "music")

OK, PENDING, STALE, FAILED = "ok", "pending", "stale", "failed"


class AnnotateError(ValueError):
    """Inconsistent input (parsed.json against work.toml)."""


class ParseError(ValueError):
    """Model output that does not follow the record format for its chunk."""


class RunnerError(RuntimeError):
    """The runner command failed or returned an error envelope."""


# --------------------------------------------------------------------------- helpers


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _sha(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def prompt_version() -> str:
    return hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()


def cache_dir(work: Work) -> Path:
    return work.path("annotations/cache")


def usage_path(work: Work) -> Path:
    return work.path("annotations/usage.jsonl")


def lyric_voice(work: Work) -> str:
    return work.data["work"].get("lyric_voice", NARRATOR)


def has_sprite(work: Work, character: str) -> bool:
    return bool(work.cast.get(character, {}).get("sprite", True))


def line_no(line_id: str) -> int:
    return int(line_id.rsplit("_", 1)[1])


def pinned_cast(work: Work, scene_id: str) -> dict[str, str]:
    """Sprite characters pinned on stage for the whole scene: ``[staging.<scene>].cast``."""
    cast = work.data.get("staging", {}).get(scene_id, {}).get("cast", {})
    return {c: p for c, p in cast.items() if has_sprite(work, c)}


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


# --------------------------------------------------------------------------- chunking


@dataclass
class Chunk:
    id: str
    scene_id: str
    lines: list[dict]  # parsed lines, each with its deterministic "defaults"
    text: str  # rendered model input
    input_sha: str  # sha256(prompt version + text)
    speeches: list[int]  # speeches that have verse lines here, in order
    sprite_cast: list[str]
    on_stage_start: list[str]
    pinned: list[str]
    first: int = 0
    first_verse: int = 0
    last: int = 0

    @property
    def numbers(self) -> set[int]:
        return {line_no(ln["id"]) for ln in self.lines}


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


def render_chunk(
    work: Work,
    scene: dict,
    cfg: dict,
    part: tuple[int, int],
    lines: list[dict],
    sprite_cast: list[str],
    on_stage: list[str],
    pinned: list[str],
) -> str:
    cast = ", ".join(
        f"{c} ({work.cast[c]['name']})" if work.cast[c].get("name") else c for c in sprite_cast
    )
    start = ", ".join(c + "*" * (c in pinned) for c in on_stage) or "-"
    head = (
        f'SCENE {scene["id"]} "{scene["heading"]}" | {cfg["location"]} | {cfg["time_of_day"]}'
        f" | sprite cast: {cast or '-'} | on stage at start: {start}"
    )
    if part[1] > 1:
        head += f" | part {part[0]}/{part[1]}"
    out = [head]
    shown = None  # speech whose header was printed last
    for ln in lines:
        number = f"{line_no(ln['id']):04d}"
        if ln["kind"] == "verse":
            if ln["speech"] != shown:
                who = (
                    (work.cast[ln["speaker"]].get("name") or ln["speaker"]).upper()
                    if ln["speaker"]
                    else ""
                )
                out.append(f"[S{ln['speech']} {who}]".replace(" ]", "]"))
                shown = ln["speech"]
            out.append(f"{number} {ln['text']}")
        else:
            out.append(f"(D{number} {ln['text']})")
    return "\n".join(out) + "\n"


def build_chunks(work: Work, parsed: dict[str, Any]) -> list[Chunk]:
    """Deterministically split parsed scenes into whole-speech chunks (<= MAX_CHUNK_LINES)."""
    scene_cfgs = {s["id"]: s for s in work.scenes}
    voice = lyric_voice(work)
    version = prompt_version()
    chunks: list[Chunk] = []
    for scene in parsed["scenes"]:
        cfg = scene_cfgs.get(scene["id"])
        if cfg is None:
            raise AnnotateError(f"scene {scene['id']!r} is not configured in work.toml")
        lines = [{**ln, "defaults": _line_defaults(work, cfg, ln, voice)} for ln in scene["lines"]]
        pinned = list(pinned_cast(work, scene["id"]))
        speakers = [
            ln["speaker"] for ln in lines if ln["kind"] == "verse" and ln["speaker"] is not None
        ]
        sprite_cast = [c for c in dict.fromkeys([*pinned, *speakers]) if has_sprite(work, c)]
        pieces = _pack(lines, ("speech", "stanza"), MAX_CHUNK_LINES)
        spoken_before: list[str] = []
        for number, piece in enumerate(pieces, 1):
            on_stage = list(dict.fromkeys([*pinned, *spoken_before]))
            text = render_chunk(
                work, scene, cfg, (number, len(pieces)), piece, sprite_cast, on_stage, pinned
            )
            verse = [ln for ln in piece if ln["kind"] == "verse"]
            chunks.append(
                Chunk(
                    id=f"{scene['id']}_c{number:02d}",
                    scene_id=scene["id"],
                    lines=piece,
                    text=text,
                    input_sha=_sha(version, text),
                    speeches=list(dict.fromkeys(ln["speech"] for ln in verse)),
                    sprite_cast=sprite_cast,
                    on_stage_start=on_stage,
                    pinned=pinned,
                    first=line_no(piece[0]["id"]),
                    first_verse=line_no(verse[0]["id"]) if verse else line_no(piece[-1]["id"]),
                    last=line_no(piece[-1]["id"]),
                )
            )
            for ln in verse:
                who = ln["speaker"]
                if who and has_sprite(work, who) and who not in spoken_before:
                    spoken_before.append(who)
    return chunks


def load_parsed(work: Work) -> tuple[dict[str, Any], bytes]:
    path = work.path("data/parsed.json")
    if not path.is_file():
        raise SystemExit(f"{path} missing; run `vnpipe parse` first")
    raw = path.read_bytes()
    parsed = json.loads(raw)
    validate(parsed, "parsed")
    return parsed, raw


# --------------------------------------------------------------------------- parsing


@dataclass
class Records:
    speech: dict[int, str] = field(default_factory=dict)  # speech -> emotion at its start
    change: dict[int, str] = field(default_factory=dict)  # line -> emotion from there on
    music: dict[int, str] = field(default_factory=dict)  # line -> mood from there on
    events: dict[int, list[tuple[str, str]]] = field(default_factory=dict)  # line -> (+/-, id)


def _int(token: str) -> int | None:
    return int(token) if token.isdigit() else None


def parse_records(text: str, chunk: Chunk) -> Records:
    """Strictly parse and validate model output for ``chunk``; ParseError lists the problems."""
    rec = Records()
    errors: list[str] = []
    verse_lines = {line_no(ln["id"]) for ln in chunk.lines if ln["kind"] == "verse"}
    numbers = chunk.numbers
    first: tuple[str, int | None] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        kind = parts[0]
        n = _int(parts[1]) if len(parts) > 1 else None
        first = first or (kind, n)
        if kind not in ("S", "L", "M", "E") or len(parts) < 3:
            errors.append(f"unrecognised record: {line!r}")
        elif kind == "S":
            if len(parts) != 3 or n is None:
                errors.append(f"bad record: {line!r}")
            elif n not in chunk.speeches:
                errors.append(f"unknown speech {n} in {line!r}")
            elif n in rec.speech:
                errors.append(f"duplicate S record for speech {n}")
            elif parts[2] not in EMOTIONS:
                errors.append(f"unknown emotion {parts[2]!r} in {line!r}")
            else:
                rec.speech[n] = parts[2]
        elif n is None or n not in numbers:
            errors.append(f"line outside the chunk in {line!r}")
        elif kind == "E":
            moves = []
            for token in parts[2:]:
                sign, cid = token[:1], token[1:]
                if sign not in ("+", "-") or cid not in chunk.sprite_cast:
                    errors.append(f"bad or unknown sprite id {token!r} in {line!r}")
                else:
                    moves.append((sign, cid))
            rec.events.setdefault(n, []).extend(moves)
        elif len(parts) != 3:
            errors.append(f"bad record: {line!r}")
        elif kind == "L":
            if n not in verse_lines:
                errors.append(f"L needs a spoken line, {n:04d} is not one")
            elif parts[2] not in EMOTIONS:
                errors.append(f"unknown emotion {parts[2]!r} in {line!r}")
            elif n in rec.change:
                errors.append(f"duplicate L record for line {n:04d}")
            else:
                rec.change[n] = parts[2]
        else:  # M
            if parts[2] not in MUSIC:
                errors.append(f"unknown music mood {parts[2]!r} in {line!r}")
            elif n in rec.music:
                errors.append(f"duplicate M record for line {n:04d}")
            else:
                rec.music[n] = parts[2]
    if first is None:
        errors.append("empty output")
    elif first[0] != "M" or first[1] is None or not chunk.first <= first[1] <= chunk.first_verse:
        errors.append(
            f"the first record must be an M between line {chunk.first} and {chunk.first_verse}"
        )
    errors += _check_events(rec, chunk)
    for n in chunk.speeches:
        if n not in rec.speech:
            errors.append(f"missing S record for speech {n}")
    if errors:
        raise ParseError("; ".join(errors[:12]) + (" ..." if len(errors) > 12 else ""))
    return rec


def _check_events(rec: Records, chunk: Chunk) -> list[str]:
    """Validate exits and drop entrances that auto-enter makes redundant (speakers)."""
    spoke: dict[str, list[int]] = {}
    for ln in chunk.lines:
        if ln["kind"] == "verse" and ln["speaker"] in chunk.sprite_cast:
            spoke.setdefault(ln["speaker"], []).append(line_no(ln["id"]))
    present = set(chunk.on_stage_start)
    errors = []
    for n in sorted(rec.events):
        present |= {c for c, lines in spoke.items() if lines[0] <= n}
        kept = []
        for sign, cid in rec.events[n]:
            if sign == "+":
                present.add(cid)
                if not any(m > n for m in spoke.get(cid, [])):  # a speaker enters when it speaks
                    kept.append((sign, cid))
                continue
            if cid in chunk.pinned:
                errors.append(f"E {n:04d} -{cid}: {cid} is fixed on stage (marked *)")
            elif cid not in present:
                errors.append(f"E {n:04d} -{cid}: {cid} is not on stage at that line")
            present.discard(cid)
            kept.append((sign, cid))
        rec.events[n] = kept
    return errors


# --------------------------------------------------------------------------- expansion


@dataclass
class Stage:
    """Mutable staging state of a scene, carried from chunk to chunk."""

    pinned: dict[str, str]
    on_stage: list[str] = field(init=False)
    positions: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.on_stage = list(self.pinned)
        self.positions = dict(self.pinned)

    def enter(self, cid: str) -> None:
        if cid in self.on_stage:
            return
        if cid not in self.positions:
            # Positions are permanent within a scene (the generator keeps the first one it
            # sees): prefer a slot nobody has used yet, then one nobody on stage holds.
            for taken in (set(self.positions.values()), {self.positions[c] for c in self.on_stage}):
                free = [p for p in POSITIONS if p not in taken]
                if free:
                    self.positions[cid] = free[0]
                    break
            else:
                self.positions[cid] = POSITIONS[len(self.positions) % len(POSITIONS)]
        self.on_stage.append(cid)

    def leave(self, cid: str) -> None:
        if cid in self.on_stage and cid not in self.pinned:
            self.on_stage.remove(cid)


def expand_chunk(
    work: Work, chunk: Chunk, rec: Records | None, stage: Stage, music: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """Per-line annotations for ``chunk``; ``rec`` None expands the deterministic fallback."""
    rec = rec or Records()
    out: list[dict[str, Any]] = []
    emotion, speech = "neutral", None
    if rec.music:  # the first mood of a chunk also covers the lines before it (a lead-in direction)
        music = rec.music[min(rec.music)]
    for ln in chunk.lines:
        n = line_no(ln["id"])
        verse = ln["kind"] == "verse"
        if verse:
            if ln["speech"] != speech:
                speech, emotion = ln["speech"], rec.speech.get(ln["speech"], "neutral")
            emotion = rec.change.get(n, emotion)
        music = rec.music.get(n, music)
        for sign, cid in rec.events.get(n, []):
            if sign == "+":
                stage.enter(cid)
            else:
                stage.leave(cid)
        who = ln["defaults"]["speaker"]
        if verse and who in work.cast and has_sprite(work, who):
            stage.enter(who)
        out.append(
            {
                **ln["defaults"],
                "emotion": emotion if verse else "neutral",
                "on_stage": [
                    {"character": c, "position": stage.positions[c]} for c in stage.on_stage
                ],
                "music": music,
            }
        )
    return out, music


# --------------------------------------------------------------------------- cache


def _header(chunk: Chunk, model: str) -> str:
    return f"# input={chunk.input_sha} model={model}\n"


def _parse_header(text: str) -> tuple[dict[str, str], str]:
    first, _, rest = text.partition("\n")
    if not first.startswith("# "):
        return {}, text
    return dict(t.split("=", 1) for t in first[2:].split() if "=" in t), rest


def cache_file(work: Work, chunk: Chunk, suffix: str = "txt") -> Path:
    return cache_dir(work) / f"{chunk.id}.{suffix}"


def read_cache(work: Work, chunk: Chunk) -> tuple[str, str, Records | None, str]:
    """Return (state, detail, records, model) for ``chunk``'s cache.

    The cache is keyed by sha256(prompt version + chunk input) plus the model that produced it.
    """
    path = cache_file(work, chunk)
    if path.is_file():
        meta, body = _parse_header(path.read_text(encoding="utf-8"))
        if meta.get("input") != chunk.input_sha:
            return STALE, "input changed", None, meta.get("model", "")
        try:
            return OK, "", parse_records(body, chunk), meta.get("model", "")
        except ParseError as exc:
            return FAILED, str(exc), None, meta.get("model", "")
    bad = cache_file(work, chunk, "failed")
    if bad.is_file():
        meta, body = _parse_header(bad.read_text(encoding="utf-8"))
        if meta.get("input") == chunk.input_sha:
            return FAILED, body.splitlines()[0] if body else "failed", None, meta.get("model", "")
    return PENDING, "", None, ""


# --------------------------------------------------------------------------- runner


@dataclass
class Reply:
    text: str
    usage: dict[str, Any]
    cost: float
    seconds: float


def run_command(template: str, model: str, user_text: str) -> Reply:
    """Run the runner command with ``user_text`` on stdin; understands the claude JSON envelope."""
    args = [
        a.replace("{model}", model).replace("{prompt_file}", str(PROMPT_PATH))
        for a in shlex.split(template)
    ]
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vnpipe-run-") as cwd:
        try:
            proc = subprocess.run(
                args,
                input=user_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=RUNNER_TIMEOUT,
                cwd=cwd,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"runner failed to run: {exc}") from exc
    seconds = time.monotonic() - start
    if proc.returncode != 0:
        raise RunnerError(f"runner exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    try:
        env = json.loads(proc.stdout)
    except ValueError:
        return Reply(proc.stdout, {}, 0.0, seconds)
    if not isinstance(env, dict) or "result" not in env:
        return Reply(proc.stdout, {}, 0.0, seconds)
    if env.get("is_error"):
        raise RunnerError(f"runner reported an error: {str(env['result'])[:300]}")
    cost = float(env.get("total_cost_usd") or 0)
    return Reply(str(env["result"]), env.get("usage") or {}, cost, seconds)


_usage_lock = threading.Lock()


def log_usage(
    work: Work, chunk: Chunk, model: str, reply: Reply, attempt: int, error: str = ""
) -> None:
    u = reply.usage
    row = {
        "chunk": chunk.id,
        "model": model,
        "attempt": attempt,
        "input_tokens": u.get("input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": u.get("cache_read_input_tokens", 0),
        "cost_usd": reply.cost,
        "seconds": round(reply.seconds, 2),
    }
    if error:
        row["rejected"] = error
    with _usage_lock:
        path = usage_path(work)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def annotate_chunk(work: Work, chunk: Chunk, runner: str, model: str) -> tuple[bool, str]:
    """Call the runner (one retry on invalid output) and cache the result."""
    text, error, raw = chunk.text, "", ""
    for attempt in (1, 2):
        try:
            reply = run_command(runner, model, text)
        except RunnerError as exc:
            return False, str(exc)
        raw = reply.text.strip()
        try:
            parse_records(raw, chunk)
        except ParseError as exc:
            error = str(exc)
            log_usage(work, chunk, model, reply, attempt, error)
            text = (
                f"{chunk.text}\nYour previous answer was rejected: {error}\n"
                f"Previous answer:\n{raw}\nReply again with corrected records only.\n"
            )
            continue
        log_usage(work, chunk, model, reply, attempt)
        _write(cache_file(work, chunk), _header(chunk, model) + raw + "\n")
        cache_file(work, chunk, "failed").unlink(missing_ok=True)
        return True, ""
    _write(cache_file(work, chunk, "failed"), _header(chunk, model) + error + "\n" + raw + "\n")
    return False, error


# --------------------------------------------------------------------------- merge


@dataclass
class MergeResult:
    document: dict[str, Any]
    scenes: dict[str, tuple[int, int]]  # scene id -> (annotated chunks, total chunks)
    fallback: list[str]


def merge(work: Work) -> MergeResult:
    """Expand cached records into the annotated document; missing chunks get the fallback."""
    parsed, raw = load_parsed(work)
    anns: dict[str, dict[str, Any]] = {}
    scenes: dict[str, tuple[int, int]] = {}
    fallback: list[str] = []
    stages: dict[str, Stage] = {}
    music: str | None = None
    for chunk in build_chunks(work, parsed):
        stage = stages.setdefault(chunk.scene_id, Stage(pinned_cast(work, chunk.scene_id)))
        state, _, rec, _ = read_cache(work, chunk)
        if state != OK:
            fallback.append(chunk.id)
        items, music = expand_chunk(work, chunk, rec, stage, music)
        for ln, item in zip(chunk.lines, items, strict=True):
            anns[ln["id"]] = {k: item[k] for k in ANN_ORDER}
        done, total = scenes.get(chunk.scene_id, (0, 0))
        scenes[chunk.scene_id] = (done + (state == OK), total + 1)
    doc = {
        "work": parsed["work"],
        "parsed_sha256": hashlib.sha256(raw).hexdigest(),
        "fallback_chunks": fallback,
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
    return MergeResult(doc, scenes, fallback)


# --------------------------------------------------------------------------- commands


def _chunks(work: Work) -> list[Chunk]:
    parsed, _ = load_parsed(work)
    return build_chunks(work, parsed)


def usage_rows(work: Work) -> list[dict[str, Any]]:
    path = usage_path(work)
    if not path.is_file():
        return []
    return [json.loads(r) for r in path.read_text(encoding="utf-8").splitlines() if r.strip()]


def cmd_run(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    chunks = [c for c in _chunks(work) if not args.only or fnmatch.fnmatch(c.id, args.only)]
    if not chunks:
        print("run: no chunk matches", file=sys.stderr)
        return 1
    todo = []
    for chunk in chunks:
        state, _, _, model = read_cache(work, chunk)
        if state == OK and model == args.model:
            print(f"{chunk.id}: cached")
        else:
            todo.append(chunk)

    def job(chunk: Chunk) -> tuple[Chunk, bool, str]:
        ok, why = annotate_chunk(work, chunk, args.runner, args.model)
        return chunk, ok, why

    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for chunk, ok, why in pool.map(job, todo):
            print(f"{chunk.id}: {'done' if ok else 'FAILED: ' + why}", flush=True)
            failed += not ok
    print(f"run: {len(todo) - failed} annotated, {failed} failed, {len(chunks) - len(todo)} cached")
    return 1 if failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    counts: dict[str, int] = {}
    for chunk in _chunks(work):
        state, why, _, _ = read_cache(work, chunk)
        counts[state] = counts.get(state, 0) + 1
        if state in (FAILED, STALE):
            print(f"{chunk.id}: {state} ({why})")
        elif args.verbose:
            print(f"{chunk.id}: {state}")
    pending = counts.get(PENDING, 0) + counts.get(STALE, 0)
    print(
        f"chunks: {sum(counts.values())}: {counts.get(OK, 0)} done, {pending} pending, "
        f"{counts.get(FAILED, 0)} failed"
    )
    rows = usage_rows(work)
    total = {k: sum(r[k] for r in rows) for k in ("input_tokens", "output_tokens", "cost_usd")}
    cache = sum(r["cache_creation_tokens"] + r["cache_read_tokens"] for r in rows)
    print(
        f"usage: {len(rows)} calls, {int(total['input_tokens'])} input + "
        f"{int(total['output_tokens'])} output + {cache} cache tokens, ${total['cost_usd']:.4f}"
    )
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    result = merge(work)
    if args.strict and result.fallback:
        print(f"merge: chunks without output: {', '.join(result.fallback)}", file=sys.stderr)
        return 1
    for sid, (done, total) in result.scenes.items():
        if done < total:
            kind = "NOT annotated" if done == 0 else f"partial ({done}/{total} chunks)"
            print(f"warning: scene {sid}: {kind}, deterministic fallback used", file=sys.stderr)
    out = work.path("data/annotated.json")
    _write(out, dumps(result.document))
    n_lines = sum(len(s["lines"]) for s in result.document["scenes"])
    print(f"merge: wrote {out} ({n_lines} lines, {len(result.fallback)} fallback chunks)")
    return 0


def render_prompt(work: Work, chunk_id: str) -> str:
    for chunk in _chunks(work):
        if chunk.id == chunk_id:
            return PROMPT_PATH.read_text(encoding="utf-8") + "\n\n=== INPUT ===\n\n" + chunk.text
    raise SystemExit(f"prompt: unknown chunk id {chunk_id!r}")


def cmd_prompt(args: argparse.Namespace) -> int:
    sys.stdout.write(render_prompt(load_work(args.work), args.chunk_id))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    annotate = subparsers.add_parser("annotate", help="LLM annotation stage tooling")
    sub = annotate.add_subparsers(dest="annotate_command", required=True)
    run = sub.add_parser("run", help="Annotate pending chunks with a runner command")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--jobs", type=int, default=4)
    run.add_argument("--only", help="Only chunks whose id matches this glob")
    run.add_argument(
        "--runner",
        default=DEFAULT_RUNNER,
        help="Command reading the chunk on stdin ({model}, {prompt_file} are substituted); "
        "prints the claude JSON envelope or plain records",
    )
    run.set_defaults(handler=cmd_run)
    status = sub.add_parser("status", help="Show done/pending/failed chunks and token usage")
    status.add_argument("-v", "--verbose", action="store_true", help="List every chunk")
    status.set_defaults(handler=cmd_status)
    merge_cmd = sub.add_parser("merge", help="Expand cached records into data/annotated.json")
    merge_cmd.add_argument(
        "--strict",
        action="store_true",
        help="Fail instead of using the fallback for missing chunks",
    )
    merge_cmd.set_defaults(handler=cmd_merge)
    prompt = sub.add_parser(
        "prompt", help="Print the full prompt (instructions + input) of a chunk"
    )
    prompt.add_argument("chunk_id")
    prompt.set_defaults(handler=cmd_prompt)
