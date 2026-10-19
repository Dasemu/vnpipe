"""Text stage: fetch the Gutenberg source, clean/scope it and parse it into parsed.json."""

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from vnpipe.config import Work, load_work
from vnpipe.schema import validate

BASE_INDENT = 4
UPPER = "A-ZÁÉÍÓÚÜÑ"
ILLUSTRATION_START_RE = re.compile(r"^\[Ilustración")
FOOTNOTE_START_RE = re.compile(r"^ {1,3}\[\d+\]\s")
FOOTNOTE_MARK_RE = re.compile(r"(?<=\S)\[\d+\]")
# A speaker heading: NAME, optionally followed by an italic note (separated by a comma or a
# space) that may mix italics and upper-case addressees: "MEFISTÓFELES, _a_ FAUSTO",
# "FAUSTO (_solo_)".
_NOTE = rf"_[^_]+_(?:\s*(?:_[^_]+_|[{UPPER}][{UPPER} ]*))*"
HEADING_RE = re.compile(
    rf"^(?P<name>[{UPPER}][{UPPER} ]*?)"
    rf"(?:(?:,\s*|\s+)(?P<note1>{_NOTE})|\s*\(_(?P<note2>[^_]+)_\))?\.?$"
)

Line = tuple[int, str]  # (1-based raw line number, text without line ending)


class ParseError(ValueError):
    """The source text has a structure the parser does not know."""


def _split(raw_text: str) -> list[str]:
    text = raw_text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def clean(raw_text: str, scope_cfg: dict[str, Any]) -> list[Line]:
    """Cut the raw text to the configured scope and drop noise.

    The start marker is the first line whose stripped text equals ``start_marker``; the end
    marker is the ``end_occurrence``-th such line after it (exclusive). Dropped as noise:
    illustration markers (also multi-line ones), footnote definitions (``  [12] text``, with
    their indented continuation lines) and footnote markers (``word[12]``). Trailing whitespace
    is stripped and blank runs collapse to a single blank line. Returned line numbers refer to
    the raw file.
    """
    lines = _split(raw_text)
    start_marker, end_marker = scope_cfg["start_marker"], scope_cfg["end_marker"]
    occurrence = scope_cfg.get("end_occurrence", 1)
    start = next((i for i, ln in enumerate(lines) if ln.strip() == start_marker), None)
    if start is None:
        raise ParseError(f"scope start marker {start_marker!r} not found")
    matches = [i for i in range(start + 1, len(lines)) if lines[i].strip() == end_marker]
    if len(matches) < occurrence:
        raise ParseError(f"scope end marker {end_marker!r} occurrence {occurrence} not found")
    end = matches[occurrence - 1]

    out: list[Line] = []
    skip_footnote = False
    in_illustration = False
    for i in range(start, end):
        text = lines[i].rstrip()
        stripped = text.strip()
        if in_illustration:
            in_illustration = not stripped.endswith("]")
            continue
        if ILLUSTRATION_START_RE.match(stripped):
            in_illustration = not stripped.endswith("]")
            continue
        if FOOTNOTE_START_RE.match(text):
            skip_footnote = True
            continue
        if skip_footnote:
            if stripped and _indent(text) < BASE_INDENT:
                continue
            skip_footnote = False
        text = FOOTNOTE_MARK_RE.sub("", text)
        if not text and out and not out[-1][1]:
            continue
        out.append((i + 1, text))
    while out and not out[-1][1]:
        out.pop()
    return out


def clean_text(lines: list[Line]) -> str:
    """Render cleaned lines as the contents of play.txt."""
    return "\n".join(text for _, text in lines) + "\n"


def _strip_markup(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("_", "")).strip()


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _scene_index(work_cfg: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    """Map (heading, occurrence) to the scene config; ids and keys must be unique."""
    index: dict[tuple[str, int], dict[str, Any]] = {}
    ids: set[str] = set()
    for cfg in work_cfg["scenes"]:
        key = (cfg["heading"], cfg.get("occurrence", 1))
        if key in index:
            raise ParseError(
                f"scenes {index[key]['id']!r} and {cfg['id']!r} share heading {key[0]!r}: "
                "give each a distinct `occurrence` (1-based, in order of appearance)"
            )
        if cfg["id"] in ids:
            raise ParseError(f"duplicate scene id {cfg['id']!r}")
        index[key] = cfg
        ids.add(cfg["id"])
    return index


class _SceneBuilder:
    def __init__(self, scene_cfg: dict[str, Any]):
        self.id: str = scene_cfg["id"]
        self.heading: str = scene_cfg["heading"]
        self.prose: bool = scene_cfg.get("format", "verse") == "prose"
        self.lines: list[dict[str, Any]] = []
        self.speaker: str | None = None
        self.note: str | None = None
        self.speech = -1
        self.stanza = 0
        self.has_verse = False
        self.pending_blank = False
        self.started = False  # a speech or verse line has begun: no more preamble

    def add(self, kind: str, text: str, src: int, indent: int = 0, speaker: Any = False) -> None:
        self.lines.append(
            {
                "id": f"{self.id}_{len(self.lines) + 1:04d}",
                "kind": kind,
                "speaker": self.speaker if speaker is False else speaker,
                "speaker_note": self.note if speaker is False else None,
                "speech": max(self.speech, 0),
                "stanza": self.stanza,
                "indent": indent,
                "text": text,
                "src_line": src,
            }
        )

    def start_speech(self, speaker: str, note: str | None) -> None:
        self.speech += 1
        self.speaker, self.note = speaker, note
        self.stanza, self.has_verse, self.pending_blank = 0, False, False
        self.started = True

    def verse(self, text: str, src: int) -> None:
        if self.pending_blank and self.has_verse:
            self.stanza += 1
        self.pending_blank, self.has_verse = False, True
        self.started = True
        self.add("verse", _strip_markup(text), src, max(_indent(text) - BASE_INDENT, 0))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "heading": self.heading, "lines": self.lines}


def _block(lines: list[Line], i: int) -> tuple[list[str], int]:
    """Consecutive non-blank stripped lines starting at index ``i``; returns (block, next i)."""
    block: list[str] = []
    while i < len(lines) and lines[i][1].strip():
        block.append(lines[i][1].strip())
        i += 1
    return block, i


def parse(lines: list[Line], work_cfg: dict[str, Any], source_sha256: str) -> dict[str, Any]:
    """Parse cleaned lines into the structure described by parsed.schema.json.

    Line types, in the order they are tried (see docs/work-config.md):
    scene heading (``[[scenes]]``), ``(_ ... _)`` direction, ``direction_headings`` entry,
    italic direction block, speaker heading (``[speakers]``), preamble block (cast list or
    setting before the first speech), prose paragraph (``format = "prose"`` scenes), verse.
    """
    scene_cfgs = _scene_index(work_cfg)
    headings = {h for h, _ in scene_cfgs}
    speakers: dict[str, str] = work_cfg["speakers"]
    direction_headings: set[str] = set(work_cfg.get("parser", {}).get("direction_headings", []))
    seen_headings: dict[str, int] = {}
    scenes: list[_SceneBuilder] = []
    cur: _SceneBuilder | None = None
    i = 0

    def fail(src: int, msg: str) -> ParseError:
        return ParseError(f"raw line {src}: {msg}")

    def speaker_heading(at: int, cur: _SceneBuilder) -> tuple[re.Match[str] | str, int] | None:
        """Speaker heading at index ``at`` (a wrapped note is joined): (match, next index)."""
        src, text = lines[at]
        stripped, nxt = text.strip(), at + 1
        indent = _indent(text)
        if indent >= BASE_INDENT:
            return None
        while stripped.count("_") % 2 and nxt < len(lines) and lines[nxt][1].strip():
            stripped += " " + lines[nxt][1].strip()
            nxt += 1
        if stripped in speakers:
            match: re.Match[str] | str | None = stripped
        else:
            match = HEADING_RE.match(stripped)
        if match is None:
            return None
        name = match if isinstance(match, str) else match["name"].strip()
        after = lines[nxt][1] if nxt < len(lines) else ""
        if indent and (name not in speakers or after.strip()):
            return None  # indented lines are verse unless they are known, isolated headings
        # a heading is followed by a blank line or by verse; anything else makes it part of a
        # cast/setting block ("MARGARITA\n_entra de un salto..._")
        peek = nxt
        while peek < len(lines) and not lines[peek][1].strip():
            peek += 1
        if peek < len(lines):
            follow = lines[peek][1]
            adjacent = peek == nxt
            if follow.strip().startswith(("_", "(_")):
                ok = not adjacent or follow.startswith(" ") or indent
            elif _indent(follow) > 0:
                ok = True
            else:
                nxt_head = follow.strip() in speakers or follow.strip() in headings
                ok = cur.prose and not adjacent and not nxt_head
                if not ok and name in speakers and not adjacent and not nxt_head and not cur.prose:
                    raise fail(src, f"{stripped!r} is followed by unindented text; prose scene?")
            if not ok:
                return None
        return match, nxt

    while i < len(lines):
        src, text = lines[i]
        stripped = text.strip()
        i += 1
        if not stripped:
            if cur:
                cur.pending_blank = True
            continue

        if not text[0].isspace() and stripped in headings:
            n = seen_headings[stripped] = seen_headings.get(stripped, 0) + 1
            if (stripped, n) not in scene_cfgs:
                raise fail(
                    src, f"scene heading {stripped!r} occurs {n} times, fewer are configured"
                )
            cur = _SceneBuilder(scene_cfgs[(stripped, n)])
            scenes.append(cur)
            continue
        if cur is None:
            raise fail(src, f"text before any scene heading: {stripped!r}")

        indent = _indent(text)
        if stripped.startswith("(_") or (indent < BASE_INDENT and stripped.startswith("(")):
            block = [stripped]
            while not (
                block[-1].rstrip("_").endswith(")")
                and " ".join(block).count("(") == " ".join(block).count(")")
            ):
                if i >= len(lines) or not lines[i][1].strip():
                    raise fail(src, "unterminated stage direction")
                block.append(lines[i][1].strip())
                i += 1
            inner = " ".join(block)[1:].rstrip("_")  # tolerate a misplaced closing "_" after ")"
            cur.add("direction", _strip_markup(inner[:-1]), src, speaker=None)
            continue

        if cur.started and indent < BASE_INDENT and stripped in direction_headings:
            cur.add("direction", _strip_markup(stripped), src, speaker=None)
            continue

        if indent < BASE_INDENT and stripped.startswith("_"):
            block, i = _block(lines, i - 1)
            cur.add("direction", _strip_markup(" ".join(block)), src, speaker=None)
            continue

        found = speaker_heading(i - 1, cur)
        if found is not None:
            match, i = found
            if isinstance(match, str):
                name, note = match, None
            else:
                name = match["name"].strip()
                note = match["note1"] or match["note2"]
            if name not in speakers:
                raise fail(src, f"unknown speaker or scene heading {stripped!r}")
            note = _strip_markup(note) if note else None
            cur.start_speech(speakers[name], note)
            continue

        if not cur.started and indent < BASE_INDENT:
            block, i = _block(lines, i - 1)
            cur.add("cast", _strip_markup(" ".join(block)), src, speaker=None)
            continue

        if indent == 0:
            if cur.prose and cur.speaker:
                block, i = _block(lines, i - 1)
                cur.verse(" ".join(block), src)
                continue
            raise fail(src, f"unrecognised line {stripped!r}")
        cur.verse(text, src)

    return {
        "work": work_cfg["work"]["id"],
        "source_sha256": source_sha256,
        "scenes": [s.to_dict() for s in scenes],
    }


def build(raw_bytes: bytes, work_cfg: dict[str, Any]) -> tuple[list[Line], dict[str, Any]]:
    """Clean and parse raw file bytes; returns (clean lines, validated parsed dict)."""
    lines = clean(raw_bytes.decode("utf-8"), work_cfg["scope"])
    parsed = parse(lines, work_cfg, hashlib.sha256(raw_bytes).hexdigest())
    validate(parsed, "parsed")
    return lines, parsed


def _raw_path(work: Work) -> Path:
    return work.path(work.data["source"]["raw_file"])


def cmd_fetch(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    dest = _raw_path(work)
    if dest.exists():
        print(f"fetch: {dest} already present, nothing to do")
        return 0
    url = work.data["source"]["url"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "vnpipe/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        payload = response.read()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)
    print(f"fetch: wrote {dest} ({len(payload)} bytes)")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    raw = _raw_path(work)
    if not raw.is_file():
        raise SystemExit(f"parse: {raw} missing; run `vnpipe fetch` first")
    lines, parsed = build(raw.read_bytes(), work.data)
    out_dir = work.path("data")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "play.txt").write_text(clean_text(lines), encoding="utf-8", newline="\n")
    payload = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    (out_dir / "parsed.json").write_text(payload, encoding="utf-8", newline="\n")
    for scene in parsed["scenes"]:
        kinds: dict[str, int] = {}
        for ln in scene["lines"]:
            kinds[ln["kind"]] = kinds.get(ln["kind"], 0) + 1
        print(f"parse: {scene['id']}: {kinds}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    fetch = subparsers.add_parser("fetch", help="Download the Gutenberg source if missing")
    fetch.set_defaults(handler=cmd_fetch)
    parse_cmd = subparsers.add_parser("parse", help="Clean, scope and parse the source text")
    parse_cmd.set_defaults(handler=cmd_parse)
