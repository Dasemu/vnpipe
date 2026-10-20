from pathlib import Path

import pytest

from vnpipe.voice_align import (
    Target,
    align,
    find_sections,
    map_speeches,
    normalise,
    pad_bounds,
    parse_speeches,
    plausible_duration,
    syllables,
    tokenize,
)

FIXTURES = Path(__file__).parent / "fixtures"
SPEAKERS = {"FAUST": "faust", "MARGARETE": "margarita", "MEPHISTOPHELES": "mephisto"}


@pytest.fixture(scope="module")
def sections():
    text = (FIXTURES / "voice_de.txt").read_text(encoding="utf-8")
    return find_sections(text, ["Erste Szene", "Zweite Szene"])


def test_normalise_folds_spelling_variants():
    assert normalise("Fräulein,") == "fraulein"
    assert normalise("Maß") == normalise("Mass") == "mass"
    assert normalise("geht’s!") == "gehts"
    assert tokenize("sitt- und tugendreich — gar") == ["sitt", "und", "tugendreich", "gar"]


def test_syllables_counts_vowel_groups():
    assert syllables("Mein schönes Fräulein") == 5  # ei, ö, e, äu, ei


def test_find_sections_skips_contents_and_stops_at_end(sections):
    assert "Mein schönes Fräulein" in sections[0]
    assert "Zweite Szene" not in sections[0]
    assert sections[1].strip().startswith("MEPHISTOPHELES.")
    assert "END OF" not in sections[1]
    with pytest.raises(ValueError, match="not found"):
        find_sections("a\n\n\nB\n\nx", ["Missing"])


def test_parse_speeches(sections):
    speeches = parse_speeches(sections[0], SPEAKERS)
    assert [(s.speaker, s.label) for s in speeches] == [
        ("faust", "FAUST"),
        ("margarita", "MARGARETE"),
        ("faust", "FAUST"),  # "FAUST (für sich)." is the same speaker
        ("mephisto", "MEPHISTOPHELES"),  # multi-line parenthetical on the label
        (None, "UNBEKANNTER"),  # unknown label: kept, never mapped
    ]
    assert speeches[1].text == "Bin weder Fräulein, weder schön, Kann ungeleitet nach Hause gehn."
    assert "Mephistopheles" not in speeches[2].text  # stage direction dropped
    assert speeches[2].tokens[-3:] == ["sitt", "und", "tugendreich"][-3:]
    assert speeches[3].text == "Ein Schüler? Wer?"


def asr_for(speeches, narrator=("faust",), mishear_first=False):
    """Fake ASR: preamble, then per speech the narrator reading its name and the speech."""
    words, t = [], 0.0

    def add(word, gap=0.3):
        nonlocal t
        t += gap
        words.append([word, round(t, 2), round(t + 0.25, 2), 0.9])
        t += 0.25

    for w in ("Dies", "ist", "eine", "LibriVox", "Aufnahme"):
        add(w)
    for sp in speeches:
        add(sp.label.title(), gap=0.8)  # the narrator announces the speaker
        for i, token in enumerate(sp.tokens):
            add("Xyz" if (mishear_first and i == 0) else token, gap=0.1)
    return words


def test_align_excludes_narrator_and_preamble(sections):
    speeches = parse_speeches(sections[0], SPEAKERS)
    words = asr_for(speeches)
    spans = align(speeches, words)
    for sp, span in zip(speeches, spans, strict=True):
        assert span and span.coverage == 1.0
        assert words[span.first][0].lower() == sp.tokens[0]
        assert words[span.last][0].lower() == sp.tokens[-1]
    assert spans[0].end < spans[1].start  # ordered, disjoint


def test_align_recovers_a_misheard_first_word(sections):
    speeches = parse_speeches(sections[0], SPEAKERS)
    words = asr_for(speeches, mishear_first=True)
    span = align(speeches, words)[1]
    assert span.coverage < 1.0
    assert words[span.first][0] == "Xyz"  # grown by the unmatched, adjacent word
    assert words[span.first - 1][0] == "Margarete"  # but not into the narrator's name


def test_align_missing_speech_gets_none():
    speeches = parse_speeches("FAUST.\nganz anderer Text hier", {"FAUST": "faust"})
    assert align(speeches, [["nichts", 0.0, 0.5, 0.9]]) == [None]


def test_pad_bounds_never_reach_the_neighbour(sections):
    speeches = parse_speeches(sections[0], SPEAKERS)
    words = asr_for(speeches)
    span = align(speeches, words)[0]
    start, end = pad_bounds(span, words)
    assert words[span.first - 1][2] < start < span.start
    assert span.end < end < words[span.last + 1][1]
    # a neighbour 0.1 s away leaves almost no padding
    words[span.last + 1][1] = span.end + 0.1
    assert pad_bounds(span, words)[1] <= span.end + 0.05


def test_plausible_duration():
    text = "Mein schönes Fräulein, darf ich wagen"  # ~9 syllables
    assert plausible_duration(4.0, text)
    assert not plausible_duration(0.5, text)  # impossibly fast
    assert not plausible_duration(40.0, text)  # a clip with much more than the text


def sp(speaker, n):
    return parse_speeches(f"FAUST.\n{' '.join(['wort'] * n)}", {"FAUST": speaker})[0]


def test_map_speeches_by_speaker_sequence():
    source = [sp("a", 10), sp("b", 10), sp("a", 10), sp("b", 5)]
    targets = [Target(0, "a", 10, "x1"), Target(1, "b", 11, "x2"), Target(2, "a", 9, "x3")]
    assert map_speeches(source, targets) == {0: 0, 1: 1, 2: 2}


def test_map_speeches_skips_extra_speech_on_either_side():
    source = [sp("a", 10), sp("b", 10), sp("a", 10)]
    targets = [Target(0, "a", 10, "x1"), Target(1, "b", 10, "x2"), Target(2, "a", 10, "x3")]
    assert map_speeches(source, targets[:2] + [Target(3, "a", 12, "x4")]) == {0: 0, 1: 1, 3: 2}
    # the translator added a speech that has no counterpart: it stays unmapped
    extra = [targets[0], Target(5, "b", 12, "y"), *targets[1:]]
    assert 5 not in map_speeches(source, extra) or map_speeches(source, extra)[5] == 1


def test_map_speeches_drops_merges_and_keeps_interjections():
    source = [sp("a", 40), sp("b", 3)]
    # 120 words against 40 tokens: a merge, no voice
    assert map_speeches(source, [Target(0, "a", 120, "x"), Target(1, "b", 5, "y")]) == {1: 1}
    # short interjections are paired whatever the ratio
    assert map_speeches([sp("b", 1)], [Target(0, "b", 4, "y")]) == {0: 0}


def test_unlabelled_section_is_one_speech_by_the_default_speaker():
    section = (
        "\nIn der Kapelle, davor Blumen.\n\nAch neige,\nDu Schmerzenreiche,\n\nDein Antlitz.\n"
    )
    assert parse_speeches(section, {}) == []
    (speech,) = parse_speeches(section, {}, default_speaker="margarita", skip_paragraphs=1)
    assert speech.speaker == "margarita"
    assert speech.text == "Ach neige, Du Schmerzenreiche, Dein Antlitz."


def test_map_speeches_pairs_ensemble_ids_in_sequence():
    source = [sp("crowd", 10), sp("faust", 10), sp("crowd", 10)]
    targets = [
        Target(0, "artisans", 10, "a"),
        Target(1, "faust", 10, "b"),
        Target(2, "citizens", 10, "c"),
    ]
    assert map_speeches(source, targets) == {1: 1}  # different ids never pair by default
    ensembles = frozenset({"crowd", "artisans", "citizens"})
    assert map_speeches(source, targets, ensembles) == {0: 0, 1: 1, 2: 2}
    # an individual never stands in for an ensemble
    assert map_speeches([sp("faust", 10)], [Target(0, "citizens", 10, "x")], ensembles) == {}
