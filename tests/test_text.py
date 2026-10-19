import json
from pathlib import Path

import pytest

from vnpipe import text
from vnpipe.schema import validate

FIXTURE = Path(__file__).parent / "fixtures" / "mini_raw.txt"

CFG = {
    "work": {"id": "mini"},
    "scope": {"start_marker": "DEDICATORIA", "end_marker": "(_Llaman._)", "end_occurrence": 1},
    "speakers": {
        "EL DIRECTOR": "director",
        "EL POETA": "poet",
        "MEFISTÓFELES": "mephisto",
        "EL SEÑOR": "lord",
        "FAUSTO": "faust",
        "EL ESPÍRITU": "spirit",
    },
    "scenes": [
        {"heading": "DEDICATORIA", "id": "dedicatoria"},
        {"heading": "PRÓLOGO EN EL TEATRO", "id": "prologo_teatro"},
        {"heading": "PRÓLOGO EN EL CIELO", "id": "prologo_cielo"},
        {"heading": "DE NOCHE", "id": "noche"},
    ],
}
SHA = "0" * 64


@pytest.fixture(scope="module")
def raw() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed(raw):
    return text.parse(text.clean(raw, CFG["scope"]), CFG, SHA)


def scene(parsed, scene_id):
    return next(s for s in parsed["scenes"] if s["id"] == scene_id)


def test_scope_cut(raw):
    lines = text.clean(raw, CFG["scope"])
    joined = "\n".join(t for _, t in lines)
    assert lines[0][1] == "DEDICATORIA"
    assert lines[0][0] == 8  # first whole-line match, not the indented TOC entry
    assert "Gutenberg" not in joined
    assert "TEODORO LLORENTE" not in joined
    assert "Llaman" not in joined
    assert "WAGNER" not in joined
    assert "Ilustración" not in joined
    assert "\n\n\n" not in joined


def test_scope_missing_marker(raw):
    with pytest.raises(text.ParseError):
        text.clean(raw, {**CFG["scope"], "start_marker": "NOPE"})
    with pytest.raises(text.ParseError):
        text.clean(raw, {**CFG["scope"], "end_occurrence": 5})


def test_crlf_is_normalised(raw):
    crlf = raw.replace("\n", "\r\n")
    assert text.clean(crlf, CFG["scope"]) == text.clean(raw, CFG["scope"])


def test_scenes_in_order_with_ids(parsed):
    assert [s["id"] for s in parsed["scenes"]] == [
        "dedicatoria",
        "prologo_teatro",
        "prologo_cielo",
        "noche",
    ]
    first = scene(parsed, "noche")["lines"][0]
    assert first["id"] == "noche_0001"


def test_dedicatoria_has_no_speaker(parsed):
    lines = scene(parsed, "dedicatoria")["lines"]
    assert all(ln["speaker"] is None and ln["kind"] == "verse" for ln in lines)
    assert [ln["stanza"] for ln in lines] == [0, 0, 0, 0, 1, 1]
    assert [ln["indent"] for ln in lines] == [2, 0, 0, 0, 2, 0]


def test_cast_list_detection(parsed):
    teatro = scene(parsed, "prologo_teatro")["lines"][0]
    assert teatro["kind"] == "cast"
    assert teatro["text"] == "EL DIRECTOR, EL POETA DRAMÁTICO, EL GRACIOSO"
    assert teatro["speaker"] is None
    cielo = scene(parsed, "prologo_cielo")["lines"][0]
    assert cielo["kind"] == "cast"
    assert cielo["text"] == (
        "EL SEÑOR, los EJÉRCITOS CELESTIALES. Después MEFISTÓFELES. "
        "Los tres ARCÁNGELES se adelantan"
    )


def test_setting_is_joined_direction(parsed):
    first = scene(parsed, "noche")["lines"][0]
    assert first["kind"] == "direction"
    assert first["text"] == (
        "En un aposento gótico, estrecho, con elevada bóveda, FAUSTO "
        "intranquilo sentado a su pupitre."
    )


def test_stanza_counting(parsed):
    lines = [ln for ln in scene(parsed, "prologo_teatro")["lines"] if ln["speaker"] == "director"]
    assert [ln["stanza"] for ln in lines] == [0, 0, 0, 1, 1]
    assert {ln["speech"] for ln in lines} == {0}


def test_split_verse_indentation(parsed):
    poet = [ln for ln in scene(parsed, "prologo_teatro")["lines"] if ln["speaker"] == "poet"]
    assert poet[0]["indent"] == 26
    assert poet[1]["indent"] == 0
    assert poet[0]["speech"] == 1


def test_indented_speaker_heading(parsed):
    lines = scene(parsed, "prologo_cielo")["lines"]
    mephisto = [ln for ln in lines if ln["text"] in ("¿A Fausto", "el Doctor?")]
    assert [ln["speaker"] for ln in mephisto] == ["mephisto", "mephisto"]
    assert [ln["indent"] for ln in mephisto] == [19, 0]
    assert [ln["speech"] for ln in lines if ln["speaker"]] == [0, 1, 1, 2, 3, 3]


def test_speaker_note(parsed):
    solo = [ln for ln in scene(parsed, "prologo_cielo")["lines"] if ln["speaker_note"]]
    assert {ln["speaker_note"] for ln in solo} == {"solo"}
    assert {ln["speaker"] for ln in solo} == {"mephisto"}
    noche = scene(parsed, "noche")["lines"]
    terrified = [ln for ln in noche if ln["speaker_note"] == "aterrado"]
    assert len(terrified) == 2
    assert terrified[0]["speaker"] == "faust"


def test_multiline_stage_direction(parsed):
    directions = [ln for ln in scene(parsed, "noche")["lines"] if ln["speaker"] is None][1:]
    assert len(directions) == 1
    assert directions[0]["kind"] == "direction"
    assert directions[0]["text"] == (
        "Toma el libro y pronuncia misteriosamente el nombre del Espíritu. "
        "Enciéndese una luz rojiza y trémula. El Espíritu aparece en ella."
    )
    assert directions[0]["src_line"] == 86
    inline = [ln for ln in scene(parsed, "prologo_cielo")["lines"] if ln["kind"] == "direction"]
    assert inline[0]["text"] == "El cielo se cierra y los Arcángeles se dispersan."


def test_italics_stripped(parsed):
    texts = [ln["text"] for ln in scene(parsed, "noche")["lines"]]
    assert "la Razón lo nombra" in texts
    assert not any("_" in t for s in parsed["scenes"] for t in (ln["text"] for ln in s["lines"]))


def test_src_lines_point_to_raw(raw, parsed):
    raw_lines = raw.split("\n")
    for s in parsed["scenes"]:
        for ln in s["lines"]:
            if ln["kind"] == "verse":
                assert raw_lines[ln["src_line"] - 1].strip().replace("_", "") == ln["text"]


def test_unknown_speaker_fails(raw):
    cfg = {**CFG, "speakers": {k: v for k, v in CFG["speakers"].items() if k != "FAUSTO"}}
    with pytest.raises(text.ParseError, match="FAUSTO"):
        text.parse(text.clean(raw, CFG["scope"]), cfg, SHA)


def test_unknown_scene_heading_fails(raw):
    cfg = {**CFG, "scenes": CFG["scenes"][:3]}
    with pytest.raises(text.ParseError, match="DE NOCHE"):
        text.parse(text.clean(raw, CFG["scope"]), cfg, SHA)


def test_deterministic_and_schema_valid(raw, parsed):
    again = text.parse(text.clean(raw, CFG["scope"]), CFG, SHA)
    assert json.dumps(again) == json.dumps(parsed)
    validate(parsed, "parsed")


def test_build_hashes_raw_bytes():
    data = FIXTURE.read_bytes()
    _, result = text.build(data, CFG)
    import hashlib

    assert result["source_sha256"] == hashlib.sha256(data).hexdigest()
