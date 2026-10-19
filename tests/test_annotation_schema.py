import copy
import json

import pytest

from vnpipe import SCHEMA_DIR, annotate
from vnpipe.schema import SchemaError, validate

SHA = "a" * 64


def _ann(**over):
    ann = {
        "id": "s_0001",
        "speaker": "faust",
        "kind": "dialogue",
        "emotion": "neutral",
        "location": "study",
        "time_of_day": "night",
        "on_stage": [{"character": "faust", "position": "left"}],
        "music": None,
    }
    ann.update(over)
    return ann


def _load(name):
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _annotated():
    ann = _ann()
    del ann["id"]
    line = {
        "id": "s_0001",
        "kind": "verse",
        "speaker": "faust",
        "speaker_note": None,
        "speech": 0,
        "stanza": 0,
        "indent": 0,
        "text": "Hola",
        "src_line": 1,
        "ann": ann,
    }
    return {
        "work": "w",
        "parsed_sha256": SHA,
        "fallback_chunks": [],
        "scenes": [{"id": "s", "heading": "H", "lines": [line]}],
    }


def test_annotated_valid():
    validate(_annotated(), "annotated")


def test_annotated_rejects_problems():
    doc = _annotated()
    del doc["scenes"][0]["lines"][0]["ann"]
    with pytest.raises(SchemaError):
        validate(doc, "annotated")
    doc = _annotated()
    doc["scenes"][0]["lines"][0]["ann"]["emotion"] = "happy"
    with pytest.raises(SchemaError):
        validate(doc, "annotated")
    doc = _annotated()
    doc["scenes"][0]["lines"][0]["ann"]["id"] = "s_0001"
    with pytest.raises(SchemaError):
        validate(doc, "annotated")
    doc = _annotated()
    del doc["fallback_chunks"]
    with pytest.raises(SchemaError):
        validate(doc, "annotated")


def test_annotated_definitions_match_source_schemas():
    """annotated.schema.json duplicates definitions from the other schemas; keep them in sync."""
    annotated = _load("annotated")["$defs"]
    assert annotated["ann"]["properties"]["emotion"]["enum"] == list(annotate.EMOTIONS)
    assert annotated["ann"]["properties"]["music"]["enum"] == [*annotate.MUSIC, None]
    assert annotated["position"]["enum"] == list(annotate.POSITIONS)

    parsed_line = _load("parsed")["properties"]["scenes"]["items"]["properties"]["lines"]["items"]
    line = copy.deepcopy(annotated["line"])
    assert line["required"] == [*parsed_line["required"], "ann"]
    del line["properties"]["ann"]
    assert line["properties"] == parsed_line["properties"]


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "verse"},
        {"time_of_day": "noon"},
        {"music": "loud"},
        {"extra": 1},
        {"on_stage": [{"character": "faust", "position": "top"}]},
        {"on_stage": [{"character": "faust"}]},
    ],
)
def test_annotated_bad_ann_values(over):
    doc = _annotated()
    doc["scenes"][0]["lines"][0]["ann"].update(over)
    with pytest.raises(SchemaError):
        validate(doc, "annotated")
