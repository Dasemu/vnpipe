"""Text-stage mechanisms for full-length texts: footnotes, repeated headings, prose, hints."""

from pathlib import Path

import pytest

from vnpipe import text
from vnpipe.schema import validate

RAW = (Path(__file__).parent / "fixtures" / "mini_ext_raw.txt").read_text(encoding="utf-8")

CFG = {
    "work": {"id": "ext"},
    "scope": {"start_marker": "PRELUDIO", "end_marker": "FIN"},
    "parser": {"direction_headings": ["ENTRA UN VECINO"]},
    "speakers": {
        "ANA": "ana",
        "BRUNO": "bruno",
        "EL VECINO": "neighbour",
        "CORO, TUTTI": "chorus",
    },
    "scenes": [
        {"heading": "PRELUDIO", "id": "preludio"},
        {"heading": "CALLE", "id": "calle_1", "occurrence": 1},
        {"heading": "CALLE", "id": "calle_2", "occurrence": 2},
        {"heading": "INTERMEDIO", "id": "intermedio"},
        {"heading": "NOCHE", "id": "noche", "format": "prose"},
    ],
}
SHA = "0" * 64


def parse(cfg=CFG, raw=RAW):
    return text.parse(text.clean(raw, cfg["scope"]), cfg, SHA)


@pytest.fixture(scope="module")
def parsed():
    return parse()


def scene(parsed, scene_id):
    return next(s for s in parsed["scenes"] if s["id"] == scene_id)


def test_footnotes_and_multiline_illustrations_are_dropped():
    lines = text.clean(RAW, CFG["scope"])
    joined = "\n".join(t for _, t in lines)
    assert "Nota" not in joined and "pie de foto" not in joined.lower()
    assert "[1]" not in joined and "[2]" not in joined
    assert "Buenos días al sol que nace," in joined
    assert "INTERMEDIO\n" in joined  # footnote marker removed from the heading
    assert "\n\n\n" not in joined
    assert "fuera de alcance" not in joined


def test_src_lines_survive_dropped_blocks():
    raw_lines = RAW.split("\n")
    for ln in (x for s in parse()["scenes"] for x in s["lines"] if x["kind"] == "verse"):
        raw = raw_lines[ln["src_line"] - 1]
        assert ln["text"].startswith(raw.strip().replace("_", "")[:10].replace("[1]", ""))


def test_repeated_heading_uses_occurrence(parsed):
    assert [s["id"] for s in parsed["scenes"]] == [
        "preludio",
        "calle_1",
        "calle_2",
        "intermedio",
        "noche",
    ]
    assert scene(parsed, "calle_1")["heading"] == scene(parsed, "calle_2")["heading"] == "CALLE"
    assert scene(parsed, "calle_2")["lines"][0]["id"] == "calle_2_0001"


def test_repeated_heading_without_occurrence_is_a_config_error():
    scenes = [dict(s) for s in CFG["scenes"]]
    del scenes[2]["occurrence"]
    with pytest.raises(text.ParseError, match="occurrence"):
        parse({**CFG, "scenes": scenes})


def test_more_occurrences_than_configured_fail():
    scenes = [s for s in CFG["scenes"] if s["id"] != "calle_2"]
    with pytest.raises(text.ParseError, match="CALLE.*2 times"):
        parse({**CFG, "scenes": scenes})


def test_duplicate_scene_id_is_a_config_error():
    scenes = [dict(s) for s in CFG["scenes"]]
    scenes[2]["id"] = "calle_1"
    with pytest.raises(text.ParseError, match="duplicate scene id"):
        parse({**CFG, "scenes": scenes})


def test_speaker_notes_with_addressee_and_separators(parsed):
    notes = {
        (ln["speaker"], ln["speaker_note"])
        for ln in scene(parsed, "preludio")["lines"] + scene(parsed, "calle_1")["lines"]
        if ln["kind"] == "verse"
    }
    assert ("bruno", "a ANA") in notes  # ", _a_ ANA"
    assert ("ana", "aparte") in notes  # "(_aparte_)"
    assert ("neighbour", "a ANA") in notes  # "EL VECINO _a_ ANA" (no comma)


def test_wrapped_note_and_trailing_period(parsed):
    wrapped = [ln for ln in scene(parsed, "calle_1")["lines"] if ln["text"] == "¿Quién viene?"][0]
    assert wrapped["speaker"] == "bruno"
    assert wrapped["speaker_note"] == "en voz baja y mirando alrededor"
    assert wrapped["indent"] == 2
    assert scene(parsed, "calle_1")["lines"][-1]["speaker"] == "chorus"  # exact-line speaker key


def test_direction_headings_apply_after_the_first_speech(parsed):
    texts = [ln["text"] for ln in scene(parsed, "calle_1")["lines"] if ln["kind"] == "direction"]
    assert "ENTRA UN VECINO" in texts
    assert texts[0] == "Amanece en la plaza."  # italic block before the first speech
    assert "Suena una campana" in texts  # italic block between speeches


def test_direction_heading_is_cast_in_a_preamble():
    cfg = {**CFG, "parser": {"direction_headings": ["ANA, BRUNO", "ENTRA UN VECINO"]}}
    first = parse(cfg)["scenes"][0]["lines"][0]
    assert first["kind"] == "cast" and first["text"] == "ANA, BRUNO"


def test_parenthesised_directions(parsed):
    texts = [ln["text"] for ln in scene(parsed, "calle_1")["lines"] if ln["kind"] == "direction"]
    assert "VECINO se quita el sombrero." in texts  # "(NAME _..._)"
    assert "Pasan ANA y BRUNO." in texts  # tolerates the misplaced ")_"


def test_heading_inside_a_cast_block(parsed):
    first = scene(parsed, "calle_2")["lines"][0]
    assert first["kind"] == "cast"
    assert first["text"] == "ANA entra corriendo, sin aliento"
    assert scene(parsed, "calle_2")["lines"][1]["speaker"] == "bruno"


def test_prose_scene_paragraphs_are_verse_lines(parsed):
    lines = scene(parsed, "noche")["lines"]
    assert [ln["kind"] for ln in lines] == ["direction", "verse", "verse", "direction", "verse"]
    ana = [ln for ln in lines if ln["speaker"] == "ana"]
    assert ana[0]["text"] == "Primer párrafo de la prosa, que se parte en dos líneas."
    assert [ln["stanza"] for ln in ana] == [0, 1]
    assert all(ln["indent"] == 0 for ln in ana)
    assert lines[-1]["speaker"] == "bruno" and lines[-1]["speech"] == 1


def test_unindented_text_fails_outside_prose_scenes():
    scenes = [dict(s) for s in CFG["scenes"]]
    scenes[-1].pop("format")
    with pytest.raises(text.ParseError, match="prose scene"):
        parse({**CFG, "scenes": scenes})


def test_unlisted_caps_line_still_fails():
    cfg = {**CFG, "parser": {}}
    with pytest.raises(text.ParseError, match="ENTRA UN VECINO"):
        parse(cfg)


def test_schema_valid_and_deterministic(parsed):
    validate(parsed, "parsed")
    assert parse() == parsed
