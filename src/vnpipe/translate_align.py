"""Translations, pure logic: parse a published translation of the play into speeches and stage
directions, and map them onto the game's speeches and directions by speaker sequence.

The game text is never translated by vnpipe: every English (or other) line comes verbatim from
a public-domain translation. Where the two translations split or merge speeches differently,
the units are grouped: a group's text goes on its first game unit, the others show nothing.

No I/O here; ``vnpipe.translate`` wires it to files and the CLI.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any

# --- source text ----------------------------------------------------------------------------

ILLUSTRATION_RE = re.compile(r"^\[Illustration[^\]]*\]?$", re.IGNORECASE)
ROMAN_RE = re.compile(r"^[IVXLC]+\.?$")
UPPER = "A-ZÀ-ÖØ-Þ"
# A speaker label: an upper-case name, optionally with a parenthetical note
# ("MEPHISTOPHELES (_to_ FAUST)", "FAUST _(passing by)_").
LABEL_RE = re.compile(
    rf"^(?P<name>[{UPPER}][{UPPER}’'\-,.& ]*?[{UPPER}])\.?"
    r"(?:\s*_?\((?P<note>[^)]*)\)_?)?\.?$"
)
ITALIC_RE = re.compile(r"^_[^_]+_\W*$")  # a paragraph wholly in italics
EXIT_RE = re.compile(r"^_?\[.*$")  # "[_Exit_.", "[He sets the goblet to his mouth."
# "...the matter. _[Exit_.": an exit printed at the end of a verse line
TRAILING_EXIT_RE = re.compile(r"^(?P<text>.*?\S)\s*(?P<exit>_?\[_?(?:Exit|Exeunt)(?![a-z]).*)$")
FOOTNOTE_RE = re.compile(r"(?<=[^\s\d])\s?\d{1,3}$")  # "my stand.27": a note reference
SETTING = "setting"  # label of the unit holding the place and cast lines before the first speech


def strip_gutenberg(text: str) -> str:
    """The text between the Project Gutenberg start and end markers (header, footer and
    licence removed); the whole text if the markers are missing."""
    lines = text.replace("\r\n", "\n").removeprefix("﻿").split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith("*** START OF")), -1)
    end = next((i for i, ln in enumerate(lines) if ln.startswith("*** END OF")), len(lines))
    return "\n".join(lines[start + 1 : end])


def find_section_lines(text: str, headings: list[str]) -> list[list[tuple[int, str]]]:
    """Cut ``text`` at each heading line, searched in order: a whole line (not indented, so a
    table of contents never matches) preceded by a blank line. Returns, per heading, the
    numbered lines (1-based within ``text``) after it up to the next heading."""
    lines = text.split("\n")
    starts: list[int] = []
    pos = 0
    for heading in headings:
        for i in range(pos, len(lines)):
            if lines[i] == heading and i > 0 and not lines[i - 1].strip():
                starts.append(i)
                pos = i + 1
                break
        else:
            raise ValueError(f"heading {heading!r} not found in the translation")
    bounds = [*starts, len(lines)]
    return [
        [(n + 1, lines[n]) for n in range(a + 1, b)]
        for a, b in zip(bounds, bounds[1:], strict=False)
    ]


@dataclass
class Unit:
    """A speech (or part of one, between stage directions) or a stage direction."""

    index: int
    kind: str  # "say" or "dir"
    speaker: str | None  # cast id; None for a direction
    label: str  # speaker label as printed ("" for directions)
    stanzas: list[list[str]] = field(default_factory=list)
    src_line: int = 0

    @property
    def lines(self) -> list[str]:
        return [ln for st in self.stanzas for ln in st]

    @property
    def words(self) -> int:
        return sum(len(ln.split()) for ln in self.lines)

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def clean_markup(text: str) -> str:
    """Drop italic/bold markers and collapse spaces."""
    text = re.sub(r"(?<!\w)=|=(?!\w)", "", text.replace("_", ""))
    return re.sub(r"\s+", " ", text).strip()


def _is_label(stripped: str) -> re.Match[str] | None:
    if not stripped or stripped.startswith(("(", "[", "_")):
        return None
    match = LABEL_RE.match(stripped)
    if match is None or not re.search(rf"[{UPPER}]{{2}}", match["name"]):
        return None
    return match


def _paragraph(rows: list[tuple[int, str]], i: int) -> str:
    """The stripped, joined non-blank lines from index ``i``."""
    block = []
    while i < len(rows) and rows[i][1].strip():
        block.append(rows[i][1].strip())
        i += 1
    return " ".join(block)


def _whole_speech(rows: list[tuple[int, str]], i: int, after_label: bool) -> bool:
    """An italic paragraph right after a label and followed by another label (or the end) is
    the whole speech (a sung refrain, a Latin hymn line), not a stage direction."""
    if not after_label:
        return False
    while i < len(rows) and rows[i][1].strip():
        i += 1
    while i < len(rows) and not rows[i][1].strip():
        i += 1
    return i >= len(rows) or (
        not rows[i][1].startswith(" ") and _is_label(rows[i][1].strip()) is not None
    )


class UnknownLabel(ValueError):
    """A speaker label of the translation has no cast id in the configuration."""


def parse_units(
    numbered: list[tuple[int, str]],
    speakers: dict[str, str],
    default_speaker: str | None = None,
    prose: bool = False,
    ignore: frozenset[str] = frozenset(),
    direction_labels: dict[str, str] | None = None,
) -> list[Unit]:
    """Units of one section, in order.

    - Before the first speaker label (the setting): a ``(...)`` or ``_..._`` paragraph is a
      direction; anything else (cast lists, place names) goes into one direction labelled
      ``SETTING`` (one stanza per paragraph), which the mapper only uses when an override
      names it (the game drops its own cast lines).
    - ``(...)`` blocks, ``[_Exit_`` lines and ``_..._`` paragraphs are directions.
    - An upper-case line followed by a blank line is a speaker label (indented only for a known
      speaker: the chorus inside a song), mapped through
      ``speakers`` (printed name without the note -> cast id); an unknown label directly
      followed by another label is a cast list: dropped in the setting, a direction (an
      entrance) after it.
    - Everything else is verse (one line per printed line, stanzas split at blank lines) or, in
      a ``prose`` section, paragraphs (one line each).
    - A label listed in ``direction_labels`` ("PEASANTS, UNDER THE LINDEN-TREE") is printed as
      a direction; the text after it is spoken by the cast id it maps to (none if empty).
    - Lines in ``ignore`` (exact stripped text) and illustration markers are dropped; a
      trailing Roman numeral (the next scene's number) is dropped, and so are footnote
      reference numbers at the end of a line (the notes are not part of the play).

    A section with no speaker label is one speech by ``default_speaker``.
    """
    direction_labels = direction_labels or {}
    rows = []
    for n, t in numbered:
        t = FOOTNOTE_RE.sub("", t.rstrip())
        match = TRAILING_EXIT_RE.match(t)
        if match and not EXIT_RE.match(t.strip()):
            parts = [(n, match["text"]), (n, match["exit"])]
            if match["text"].lstrip().startswith("("):  # "(_Enter_ X.) [_Exit_.": the exit
                parts.reverse()  # belongs to the speech before the entrance
            rows += parts
        else:
            rows.append((n, t))
    rows = [
        (n, t) for n, t in rows if not ILLUSTRATION_RE.match(t.strip()) and t.strip() not in ignore
    ]
    while rows and (not rows[-1][1].strip() or ROMAN_RE.match(rows[-1][1].strip())):
        rows.pop()
    units: list[Unit] = []
    cur: Unit | None = None  # the open speech
    speaker: tuple[str, str] | None = None  # (label, cast id) of the current speech
    pending_blank = False
    started = False
    setting: Unit | None = None
    has_labels = any(
        _is_label(t.strip()) and t.strip() not in ignore and not t.startswith(" ") for _, t in rows
    )

    def new_dir(text: str, src: int) -> None:
        nonlocal cur
        cur = None
        units.append(Unit(len(units), "dir", None, "", [[clean_markup(text)]], src))

    def add_setting(text: str, src: int) -> None:
        nonlocal setting
        if setting is None:
            setting = Unit(len(units), "dir", None, SETTING, [], src)
            units.append(setting)
        setting.stanzas.append([clean_markup(text)])

    def add_line(text: str, src: int) -> None:
        nonlocal cur, pending_blank
        if cur is None:
            if speaker is None:
                if default_speaker is None:
                    raise ValueError(f"line {src}: text before any speaker label: {text!r}")
                label, cast = "", default_speaker
            else:
                label, cast = speaker
            cur = Unit(len(units), "say", cast, label, [[]], src)
            units.append(cur)
        elif pending_blank and cur.stanzas[-1]:
            cur.stanzas.append([])
        pending_blank = False
        cur.stanzas[-1].append(clean_markup(text))

    i = 0
    while i < len(rows):
        src, text = rows[i]
        stripped = text.strip()
        i += 1
        if not stripped:
            pending_blank = True
            continue
        indented = text.startswith(" ")
        if stripped.startswith("(") and indented and "_" not in stripped and cur is not None:
            started = True  # "(The witch's tricks)" inside an indented verse stanza
            add_line(stripped, src)
            continue
        if stripped.startswith("("):
            block = [stripped]
            while " ".join(block).count("(") > " ".join(block).count(")"):
                if i >= len(rows) or not rows[i][1].strip():
                    raise ValueError(f"line {src}: unterminated stage direction")
                block.append(rows[i][1].strip())
                i += 1
            joined = " ".join(block).rstrip("._ ")
            if re.search(r"\)[,;:—–-]+$", joined):  # "(These elements),—": verse
                started = True
                add_line(joined, src)
                continue
            if joined.endswith(")"):
                joined = joined[1:-1]
            if clean_markup(joined)[:1].islower() and cur is None and speaker is not None:
                joined = (
                    f"{speaker[0]} ({clean_markup(joined)})"  # the label's note: "WITCH (declaims)"
                )
            new_dir(joined, src)
            pending_blank = False
            continue
        if EXIT_RE.match(stripped):
            new_dir(clean_markup(stripped).lstrip("[").rstrip("]"), src)
            continue
        if stripped.startswith("_(") and stripped.rstrip("._").endswith(")"):
            new_dir(stripped.rstrip("._ ").lstrip("_")[1:-1], src)
            pending_blank = False
            continue
        if (
            not indented
            and has_labels
            and (i < 2 or not rows[i - 2][1].strip())  # starts a paragraph
            and ITALIC_RE.match(_paragraph(rows, i - 1))
            and not _whole_speech(rows, i - 1, cur is None and speaker is not None)
        ):
            block = [stripped]
            while i < len(rows) and rows[i][1].strip():
                block.append(rows[i][1].strip())
                i += 1
            new_dir(" ".join(block), src)
            continue
        match = _is_label(stripped)
        if match is not None and indented:  # only a known speaker, inside a song
            known = match["name"].strip().rstrip(".,") in speakers
            if not known or (i < len(rows) and rows[i][1].strip()):
                match = None
        if match is not None and match["name"].strip().rstrip(".,") not in speakers:
            nxt = rows[i][1] if i < len(rows) else ""
            if nxt.strip() and not nxt.startswith(" "):
                match = None  # caps text running on: not a label
        if match is not None and has_labels:
            name = match["name"].strip().rstrip(".,")
            after = i
            while after < len(rows) and not rows[after][1].strip():
                after += 1
            follow = rows[after][1].strip() if after < len(rows) else ""
            if name in direction_labels:
                new_dir(stripped, src)
                cid = direction_labels[name]
                speaker = (name, cid) if cid else None
                started = True
                continue
            if not started and (not follow or _is_label(follow) or name not in speakers):
                add_setting(stripped, src)  # cast list or place line
                continue
            if name not in speakers and _is_label(follow):
                new_dir(stripped, src)  # an entrance: the names of who comes on
                speaker = None
                continue
            if name not in speakers:
                raise UnknownLabel(f"line {src}: unknown speaker label {name!r}")
            speaker = (name, speakers[name])
            cur = None
            started = True
            pending_blank = False
            continue
        if not started and has_labels:  # cast list or place line of the setting
            add_setting(_paragraph(rows, i - 1), src)
            while i < len(rows) and rows[i][1].strip():
                i += 1
            continue
        if prose and not indented:
            block = [stripped]
            while i < len(rows) and rows[i][1].strip() and not rows[i][1].startswith(" "):
                block.append(rows[i][1].strip())
                i += 1
            add_line(" ".join(block), src)
            pending_blank = True
            continue
        started = True
        add_line(stripped, src)
    for unit in units:
        unit.stanzas = [st for st in unit.stanzas if st]
    return [u for u in units if u.stanzas]


# --- game side ----------------------------------------------------------------------------


def is_direction(line: dict[str, Any]) -> bool:
    """Same rule as the Ren'Py generator: a direction line, or one annotated as such."""
    return line["kind"] == "direction" or line["ann"]["kind"] == "stage_direction"


@dataclass
class GameUnit:
    """A run of game lines that starts a new box: a speech part or a stage direction."""

    index: int
    kind: str  # "say" or "dir"
    speaker: str | None
    first_line: str
    words: int = 0
    line_ids: list[str] = field(default_factory=list)


def game_units(scene: dict[str, Any], lyric_voice: str) -> list[GameUnit]:
    """The scene's units: every stage direction line, and every run of lines with the same
    (speaker, speech) between directions. Cast lines are skipped (they are never shown)."""
    units: list[GameUnit] = []
    key: tuple[str, int] | None = None
    for line in scene["lines"]:
        if line["kind"] == "cast":
            continue
        words = len(line["text"].split())
        if is_direction(line):
            units.append(GameUnit(len(units), "dir", None, line["id"], words, [line["id"]]))
            key = None
            continue
        speaker = line["ann"]["speaker"] or lyric_voice
        if key != (speaker, line["speech"]):
            key = (speaker, line["speech"])
            units.append(GameUnit(len(units), "say", speaker, line["id"]))
        units[-1].words += words
        units[-1].line_ids.append(line["id"])
    return units


# --- mapping ------------------------------------------------------------------------------


@dataclass
class Mapping:
    """Result of mapping one scene: game unit index -> source unit indices it carries.

    ``pairs`` are the direct matches (same speaker, or two directions); ``carried`` lists, per
    game unit, every source unit whose text it shows (its pair plus unmatched neighbours);
    game units absent from ``carried`` show nothing (they belong to a group)."""

    pairs: dict[int, int]
    carried: dict[int, list[int]]
    forced: set[int] = field(default_factory=set)


def _pair_score(g: GameUnit, s: Unit, ratio: float, ensembles: frozenset[str]) -> float | None:
    if g.kind != s.kind or s.label == SETTING:
        return None
    size = g.words / max(1, s.words) / ratio
    penalty = min(3.0, abs(math.log2(max(size, 1e-3))) * 2)
    if g.kind == "dir":
        return 2.0 - penalty / 2
    if g.speaker != s.speaker:
        if not (g.speaker in ensembles and s.speaker in ensembles):
            return None
        return 2.5 - penalty
    return 4.0 - penalty


def align_units(
    game: list[GameUnit],
    source: list[Unit],
    ensembles: frozenset[str] = frozenset(),
    anchors: dict[int, int] | None = None,
) -> dict[int, int]:
    """Global alignment of the two unit sequences (game index -> source index).

    Speeches pair only with speeches of the same speaker (or two crowd ids of ``ensembles``,
    at a penalty), directions with directions; closer sizes (relative to the scene's overall
    size ratio) score higher. ``anchors`` are forced pairs: the alignment runs separately
    between them."""
    anchors = dict(sorted((anchors or {}).items()))
    pairs: dict[int, int] = {}
    prev_g = prev_s = 0
    for g_i, s_i in [*anchors.items(), (len(game), len(source))]:
        if g_i < prev_g or s_i < prev_s:
            raise ValueError("translation overrides are not in order")
        pairs.update(_align_block(game, source, prev_g, g_i, prev_s, s_i, ensembles))
        if g_i < len(game):
            pairs[g_i] = s_i
        prev_g, prev_s = g_i + 1, s_i + 1
    return pairs


def _align_block(
    game: list[GameUnit],
    source: list[Unit],
    g0: int,
    g1: int,
    s0: int,
    s1: int,
    ensembles: frozenset[str],
) -> dict[int, int]:
    gs, ss = game[g0:g1], source[s0:s1]
    g_words = sum(u.words for u in game) or 1
    s_words = sum(u.words for u in source) or 1
    ratio = g_words / s_words
    n, m = len(gs), len(ss)

    def gap(unit: GameUnit | Unit) -> float:
        return -0.4 if unit.kind == "dir" else -1.0

    table = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        table[i][0] = table[i - 1][0] + gap(gs[i - 1])
    for j in range(1, m + 1):
        table[0][j] = table[0][j - 1] + gap(ss[j - 1])
    scores: dict[tuple[int, int], float] = {}
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = max(table[i - 1][j] + gap(gs[i - 1]), table[i][j - 1] + gap(ss[j - 1]))
            s = _pair_score(gs[i - 1], ss[j - 1], ratio, ensembles)
            if s is not None:
                scores[i, j] = s
                best = max(best, table[i - 1][j - 1] + s)
            table[i][j] = best
    pairs: dict[int, int] = {}
    i, j = n, m
    while i > 0 and j > 0:
        s = scores.get((i, j))
        if s is not None and table[i][j] == table[i - 1][j - 1] + s:
            pairs[g0 + i - 1] = s0 + j - 1
            i, j = i - 1, j - 1
        elif table[i][j] == table[i - 1][j] + gap(gs[i - 1]):
            i -= 1
        else:
            j -= 1
    return pairs


def group_units(n_game: int, n_source: int, pairs: dict[int, int]) -> dict[int, list[int]]:
    """Which source units each game unit shows, so every source unit is shown once and in
    order. Between two pairs, unmatched source units go to the first unmatched game unit of
    the stretch, else to the preceding pair (or, before the first pair, to the next one)."""
    carried: dict[int, list[int]] = {}
    anchors = sorted(pairs.items())
    bounds = [(-1, -1), *anchors, (n_game, n_source)]
    for (ga, sa), (gb, sb) in zip(bounds, bounds[1:], strict=False):
        if gb < n_game:
            carried.setdefault(gb, []).append(sb)
        loose_s = list(range(sa + 1, sb))
        if not loose_s:
            continue
        loose_g = list(range(ga + 1, gb))
        if loose_g:
            carried.setdefault(loose_g[0], []).extend(loose_s)
        elif ga >= 0:
            carried.setdefault(ga, []).extend(loose_s)
        elif gb < n_game:
            carried[gb] = loose_s + carried[gb]
        elif n_game:  # no pair at all: everything on the first game unit
            carried.setdefault(0, []).extend(loose_s)
    return {g: sorted(v) for g, v in sorted(carried.items())}
