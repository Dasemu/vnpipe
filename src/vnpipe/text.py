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
ILLUSTRATION_RE = re.compile(r"^\[Ilustración[^\]]*\]$")
HEADING_RE = re.compile(
    rf"^(?P<name>[{UPPER}][{UPPER} ]*?)"
    r"(?:,\s*_(?P<note1>[^_]+)_|\s*\(_(?P<note2>[^_]+)_\))?$"
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
    marker is the ``end_occurrence``-th such line after it (exclusive). Illustration markers
    are removed, trailing whitespace stripped and blank runs collapsed to a single blank line.
    Returned line numbers refer to the raw file.
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
    for i in range(start, end):
        text = lines[i].rstrip()
        if ILLUSTRATION_RE.match(text.strip()):
            continue
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


def _match_heading(text: str) -> re.Match[str] | None:
    return HEADING_RE.match(text) if text and not text[0].isspace() else None


class _SceneBuilder:
    def __init__(self, scene_cfg: dict[str, Any]):
        self.id: str = scene_cfg["id"]
        self.heading: str = scene_cfg["heading"]
        self.lines: list[dict[str, Any]] = []
        self.speaker: str | None = None
        self.note: str | None = None
        self.speech = -1
        self.stanza = 0
        self.has_verse = False
        self.pending_blank = False

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

    def verse(self, text: str, src: int) -> None:
        if self.pending_blank and self.has_verse:
            self.stanza += 1
        self.pending_blank, self.has_verse = False, True
        self.add("verse", _strip_markup(text), src, max(_indent(text) - BASE_INDENT, 0))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "heading": self.heading, "lines": self.lines}


def parse(lines: list[Line], work_cfg: dict[str, Any], source_sha256: str) -> dict[str, Any]:
    """Parse cleaned lines into the structure described by parsed.schema.json."""
    scenes_by_heading = {s["heading"]: s for s in work_cfg["scenes"]}
    speakers: dict[str, str] = work_cfg["speakers"]
    scenes: list[_SceneBuilder] = []
    cur: _SceneBuilder | None = None
    expect_block = False
    i = 0

    def fail(src: int, msg: str) -> ParseError:
        return ParseError(f"raw line {src}: {msg}")

    while i < len(lines):
        src, text = lines[i]
        stripped = text.strip()
        i += 1
        if not stripped:
            if cur:
                cur.pending_blank = True
            continue

        if not text[0].isspace() and stripped in scenes_by_heading:
            cur = _SceneBuilder(scenes_by_heading[stripped])
            scenes.append(cur)
            expect_block = True
            continue
        if cur is None:
            raise fail(src, f"text before any scene heading: {stripped!r}")

        indent = _indent(text)
        match = _match_heading(text)
        is_heading = match is not None
        if indent and indent < BASE_INDENT:
            # Indented heading (a typesetting quirk): only accepted for known speakers.
            m = HEADING_RE.match(stripped)
            nxt = lines[i][1].strip() if i < len(lines) else ""
            if m and m["name"] in speakers and not nxt:
                match, is_heading = m, True

        if expect_block:
            expect_block = False
            if indent < BASE_INDENT and (stripped.startswith("_") or not is_heading or indent):
                block = [stripped]
                while i < len(lines) and lines[i][1].strip():
                    block.append(lines[i][1].strip())
                    i += 1
                kind = "direction" if stripped.startswith("_") else "cast"
                cur.add(kind, _strip_markup(" ".join(block)), src, speaker=None)
                continue

        if stripped.startswith("(_"):
            block = [stripped]
            while not block[-1].endswith("_)"):
                if i >= len(lines) or not lines[i][1].strip():
                    raise fail(src, "unterminated stage direction")
                block.append(lines[i][1].strip())
                i += 1
            joined = " ".join(block)
            cur.add("direction", _strip_markup(joined[2:-2]), src, speaker=None)
            continue

        if is_heading and match is not None:
            name = match["name"].strip()
            if name not in speakers:
                raise fail(src, f"unknown speaker or scene heading {stripped!r}")
            note = match["note1"] or match["note2"]
            cur.start_speech(speakers[name], note.strip() if note else None)
            continue

        if indent == 0:
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
