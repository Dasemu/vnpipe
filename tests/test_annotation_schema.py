import copy
import json

import pytest

from vnpipe import SCHEMA_DIR
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


def _cache(**over):
    doc = {"chunk_id": "s_c01", "input_sha256": SHA, "annotations": [_ann()]}
    doc.update(over)
    return doc


def _load(name):
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def test_valid_annotation_cache():
    validate(_cache(), "annotation")


def test_music_is_optional():
    ann = _ann()
    del ann["music"]
    validate(_cache(annotations=[ann]), "annotation")


@pytest.mark.parametrize("key", ["chunk_id", "input_sha256", "annotations"])
def test_cache_required_fields(key):
    doc = _cache()
    del doc[key]
    with pytest.raises(SchemaError):
        validate(doc, "annotation")


@pytest.mark.parametrize(
    "key", ["id", "speaker", "kind", "emotion", "location", "time_of_day", "on_stage"]
)
def test_annotation_required_fields(key):
    ann = _ann()
    del ann[key]
    with pytest.raises(SchemaError):
        validate(_cache(annotations=[ann]), "annotation")


@pytest.mark.parametrize(
    "over",
    [
        {"emotion": "happy"},
        {"kind": "verse"},
        {"time_of_day": "noon"},
        {"music": "loud"},
        {"extra": 1},
        {"on_stage": [{"character": "faust", "position": "top"}]},
        {"on_stage": [{"character": "faust"}]},
        {"on_stage": [{"character": "faust", "position": "left", "scale": 2}]},
    ],
)
def test_annotation_bad_values(over):
    with pytest.raises(SchemaError):
        validate(_cache(annotations=[_ann(**over)]), "annotation")


def test_cache_rejects_extra_field_and_bad_hash():
    with pytest.raises(SchemaError):
        validate(_cache(extra=1), "annotation")
    with pytest.raises(SchemaError):
        validate(_cache(input_sha256="xyz"), "annotation")


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
    source = _load("annotation")["$defs"]
    expected = copy.deepcopy(source["annotation"])
    expected["required"].remove("id")
    del expected["properties"]["id"]
    assert annotated["ann"] == expected
    assert annotated["position"] == source["position"]

    parsed_line = _load("parsed")["properties"]["scenes"]["items"]["properties"]["lines"]["items"]
    line = copy.deepcopy(annotated["line"])
    assert line["required"] == [*parsed_line["required"], "ann"]
    del line["properties"]["ann"]
    assert line["properties"] == parsed_line["properties"]
