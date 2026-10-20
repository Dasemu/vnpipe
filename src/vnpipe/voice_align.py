"""Voice track, pure logic: parse a spoken-language source text into speeches, align it with
ASR words, and map those speeches onto the game's speeches by speaker sequence.

No I/O here; ``vnpipe.voice`` wires it to files, ffmpeg and the CLI.
"""

import math
import re
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# --- tokens -----------------------------------------------------------------------------

_FOLD = str.maketrans({"ß": "ss", "ä": "a", "ö": "o", "ü": "u"})
_VOWELS = re.compile(r"[aeiouyäöü]+", re.IGNORECASE)


def normalise(word: str) -> str:
    """Lowercase letters only; ß/ss and umlauts folded, so ASR spelling variants still match."""
    word = unicodedata.normalize("NFC", word).lower().translate(_FOLD)
    return re.sub(r"[^a-z]", "", word)


def tokenize(text: str) -> list[str]:
    """Normalised tokens of a text (hyphens and dashes split words)."""
    parts = re.split(r"[\s\-–—]+", text)
    return [t for t in (normalise(p) for p in parts) if t]


def syllables(text: str) -> int:
    """Rough syllable count: vowel groups (a diphthong counts once)."""
    return sum(len(_VOWELS.findall(t)) for t in text.split())


# --- source text -------------------------------------------------------------------------


def find_sections(text: str, headings: list[str]) -> list[str]:
    """Cut ``text`` at each heading, searched in order; a heading is a whole line (not
    indented, so the table of contents never matches) preceded by a blank line.
    The last section ends at the Project Gutenberg end marker, if present."""
    lines = text.splitlines()
    starts: list[int] = []
    pos = 0
    for heading in headings:
        for i in range(pos, len(lines)):
            if lines[i] == heading and i > 0 and not lines[i - 1].strip():
                starts.append(i)
                pos = i + 1
                break
        else:
            raise ValueError(f"heading {heading!r} not found in the source text")
    end = next(
        (i for i, ln in enumerate(lines) if ln.startswith("*** END OF")),
        len(lines),
    )
    bounds = [*starts, end]
    return ["\n".join(lines[a + 1 : b]) for a, b in zip(bounds, bounds[1:], strict=False)]


_LABEL = re.compile(
    r"^([A-ZÄÖÜ][A-ZÄÖÜ ]*[A-ZÄÖÜ])(?:\.? ?\([^)]*\))?\.?[ \t]*$",
    re.MULTILINE,
)


@dataclass
class Speech:
    """One speech of the source text."""

    index: int
    label: str  # speaker heading as printed (upper case)
    speaker: str | None  # cast id (None: not in the mapping)
    text: str
    tokens: list[str] = field(default_factory=list)


def parse_speeches(
    section: str,
    speakers: dict[str, str],
    default_speaker: str | None = None,
    skip_paragraphs: int = 0,
) -> list[Speech]:
    """Speeches of one section. ``speakers`` maps the printed upper-case label (without its
    parenthetical) to a cast id. Stage directions ``( ... )`` are dropped from the text; the
    setting block before the first label is ignored.

    A section with no speaker label at all (a lyric, a song) is one speech by
    ``default_speaker``, after dropping its first ``skip_paragraphs`` paragraphs (the setting)."""
    marked = _LABEL.sub(lambda m: f"\x00{m.group(1)}\x00", section)
    marked = re.sub(r"\([^)]*\)", " ", marked)
    speeches: list[Speech] = []
    label: str | None = None
    body: list[str] = []

    def close() -> None:
        if label is None:
            return
        text = " ".join(" ".join(body).split())
        tokens = tokenize(text)
        if tokens:
            speeches.append(Speech(len(speeches), label, speakers.get(label), text, tokens))

    if default_speaker and "\x00" not in marked:
        paragraphs = [p for p in re.split(r"\n\s*\n", marked.strip()) if p.strip()]
        text = " ".join(" ".join(paragraphs[skip_paragraphs:]).split())
        return [Speech(0, "", default_speaker, text, tokenize(text))]
    for raw in marked.splitlines():
        line = raw.strip()
        if line.startswith("\x00") and line.endswith("\x00"):
            close()
            label, body = line.strip("\x00"), []
        elif label is not None and line:
            body.append(line)
    close()
    return speeches


# --- ASR alignment -----------------------------------------------------------------------


@dataclass
class Span:
    """Where one source speech sits in the audio."""

    start: float
    end: float
    first: int  # ASR word index range [first, last]
    last: int
    matched: int
    total: int

    @property
    def coverage(self) -> float:
        return self.matched / self.total if self.total else 0.0


def match_tokens(a: list[str], b: list[str], n: int = 3) -> list[tuple[int, int]]:
    """Monotonic token matches ``(i, j)`` with ``a[i] == b[j]``.

    Plain ``SequenceMatcher`` is far too slow on a whole section (thousands of tokens, most of
    them "und"/"die"), so first anchor on word n-grams that occur exactly once on each side
    (longest increasing run), then match only the stretches between anchors."""
    keys_a: dict[tuple[str, ...], list[int]] = {}
    keys_b: dict[tuple[str, ...], list[int]] = {}
    for i in range(len(a) - n + 1):
        keys_a.setdefault(tuple(a[i : i + n]), []).append(i)
    for j in range(len(b) - n + 1):
        keys_b.setdefault(tuple(b[j : j + n]), []).append(j)
    pairs = sorted(
        (ia[0], keys_b[k][0])
        for k, ia in keys_a.items()
        if len(ia) == 1 and len(keys_b.get(k, ())) == 1
    )
    # longest increasing subsequence on the b positions
    tails: list[int] = []
    ends: list[int] = []
    prev = [-1] * len(pairs)
    for idx, (_, j) in enumerate(pairs):
        pos = bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j)
            ends.append(idx)
        else:
            tails[pos] = j
            ends[pos] = idx
        prev[idx] = ends[pos - 1] if pos else -1
    chain: list[tuple[int, int]] = []
    idx = ends[-1] if ends else -1
    while idx >= 0:
        chain.append(pairs[idx])
        idx = prev[idx]
    chain.reverse()

    matches: list[tuple[int, int]] = []
    ia = jb = 0  # next unmatched positions

    def fill(a_end: int, b_end: int) -> None:
        blocks = SequenceMatcher(None, a[ia:a_end], b[jb:b_end], autojunk=False)
        for x, y, size in blocks.get_matching_blocks():
            matches.extend((ia + x + k, jb + y + k) for k in range(size))

    for i, j in chain:
        if i < ia or j < jb:
            continue  # overlaps the previous anchor
        fill(i, j)
        matches.extend((i + k, j + k) for k in range(n))
        ia, jb = i + n, j + n
    fill(len(a), len(b))
    return matches


def align(
    speeches: list[Speech], asr_words: list[list[Any]], max_gap: int = 12
) -> list[Span | None]:
    """Align the speeches' tokens with ASR words ([word, start, end, prob]) and return one
    ``Span`` per speech (None when nothing matched).

    Sequence alignment on normalised tokens; matched ASR words of a speech are clustered
    (a stray match far from the rest is dropped) and the span is trimmed to the cluster, then
    grown by the unmatched edge tokens that sit right next to it in time."""
    de: list[str] = []
    owner: list[int] = []
    for sp in speeches:
        de.extend(sp.tokens)
        owner.extend([sp.index] * len(sp.tokens))
    asr = [normalise(w[0]) for w in asr_words]
    matches: dict[int, list[tuple[int, int]]] = {sp.index: [] for sp in speeches}
    claimed = set()
    for i, j in match_tokens(de, asr):
        matches[owner[i]].append((i, j))
        claimed.add(j)
    offsets: dict[int, int] = {}
    pos = 0
    for sp in speeches:
        offsets[sp.index] = pos
        pos += len(sp.tokens)

    spans: list[Span | None] = []
    for sp in speeches:
        found = matches[sp.index]
        if not found:
            spans.append(None)
            continue
        # cluster by ASR index: split where consecutive matches are > max_gap words apart
        clusters: list[list[tuple[int, int]]] = [[found[0]]]
        for item in found[1:]:
            if item[1] - clusters[-1][-1][1] > max_gap:
                clusters.append([])
            clusters[-1].append(item)
        best = max(clusters, key=len)
        first, last = best[0][1], best[-1][1]
        lead = best[0][0] - offsets[sp.index]  # unmatched source tokens before the cluster
        trail = offsets[sp.index] + len(sp.tokens) - 1 - best[-1][0]
        for _ in range(min(lead, 2)):  # misheard first words: take adjacent unclaimed words
            prev = first - 1
            if prev < 0 or prev in claimed or asr_words[first][1] - asr_words[prev][2] > 0.3:
                break
            first = prev
        for _ in range(min(trail, 2)):
            nxt = last + 1
            if nxt >= len(asr_words) or nxt in claimed:
                break
            if asr_words[nxt][1] - asr_words[last][2] > 0.3:
                break
            last = nxt
        spans.append(
            Span(
                start=asr_words[first][1],
                end=asr_words[last][2],
                first=first,
                last=last,
                matched=len(best),
                total=len(sp.tokens),
            )
        )
    return spans


def pad_bounds(
    span: Span, asr_words: list[list[Any]], pre: float = 0.1, post: float = 0.25
) -> tuple[float, float]:
    """Cut window around a span: the paddings, shrunk to 45% of the silence to the neighbouring
    ASR word so a neighbour (another voice) is never pulled in."""
    start, end = span.start - pre, span.end + post
    if span.first > 0:
        gap = span.start - asr_words[span.first - 1][2]
        start = span.start - min(pre, max(0.0, gap) * 0.45)
    if span.last + 1 < len(asr_words):
        gap = asr_words[span.last + 1][1] - span.end
        end = span.end + min(post, max(0.0, gap) * 0.45)
    return max(0.0, start), end


def plausible_duration(duration: float, text: str) -> bool:
    """Reading speed sanity check: between 1.6 and 9 syllables per second (plus a margin)."""
    syl = max(1, syllables(text))
    return syl / 9.0 - 0.3 <= duration <= syl / 1.6 + 1.0


# --- speaker-sequence mapping --------------------------------------------------------------


@dataclass
class Target:
    """A speech of the game text: its index in the scene, speaker and size in words."""

    speech: int
    speaker: str
    words: int
    first_line: str


def map_speeches(
    source: list[Speech],
    targets: list[Target],
    ensembles: frozenset[str] = frozenset(),
    min_ratio: float = 0.4,
    max_ratio: float = 2.5,
    short: int = 4,
) -> dict[int, int]:
    """Map target speech index -> source speech index.

    Global alignment over the two speaker sequences: only equal speaker ids pair up (or two
    ids of ``ensembles``, at a penalty), the others are gaps. Pairs score higher the closer
    their lengths are, so a merge/split by the translator resolves to the best partner. Pairs
    whose length ratio (target words / source tokens) is outside ``[min_ratio, max_ratio]``
    are dropped afterwards (merges or splits, where a clip would not match the text), unless
    the two speeches differ
    by at most ``short`` words (interjections: the ratio means nothing there)."""
    n, m = len(targets), len(source)
    gap = -1.0

    def score(i: int, j: int) -> float | None:
        a, b = targets[i].speaker, source[j].speaker
        if a != b and not (a in ensembles and b in ensembles):
            return None
        ratio = targets[i].words / max(1, len(source[j].tokens))
        # two different ensemble ids may be the same crowd (labels are ordinal: "the others")
        return 4.0 - (0.0 if a == b else 1.5) - min(3.0, abs(math.log2(max(ratio, 1e-3))) * 2)

    table = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        table[i][0] = i * gap
    for j in range(1, m + 1):
        table[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = max(table[i - 1][j] + gap, table[i][j - 1] + gap)
            s = score(i - 1, j - 1)
            if s is not None:
                best = max(best, table[i - 1][j - 1] + s)
            table[i][j] = best
    pairs: dict[int, int] = {}
    i, j = n, m
    while i > 0 and j > 0:
        s = score(i - 1, j - 1)
        if s is not None and table[i][j] == table[i - 1][j - 1] + s:
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif table[i][j] == table[i - 1][j] + gap:
            i -= 1
        else:
            j -= 1
    result = {}
    for ti, sj in pairs.items():
        size = len(source[sj].tokens)
        ratio = targets[ti].words / max(1, size)
        if min_ratio <= ratio <= max_ratio or abs(targets[ti].words - size) <= short:
            result[targets[ti].speech] = sj
    return result
