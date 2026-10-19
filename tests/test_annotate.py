import json
from pathlib import Path

import pytest

from vnpipe import annotate
from vnpipe.cli import main
from vnpipe.config import Work, load_work

WORK_TOML = """
[work]
id = "t"
lyric_voice = "voice"

[[scenes]]
heading = "ONE"
id = "one"
location = "hall"
time_of_day = "day"

[[scenes]]
heading = "TWO"
id = "two"
location = "yard"
time_of_day = "night"

[cast.narrator]
name = ""
[cast.voice]
name = ""
sprite = false
[cast.alice]
name = "Alice"
sprite = true
[cast.bob]
name = "Bob"
sprite = true
[cast.god]
name = "God"
sprite = false
"""


def _line(scene, n, speech, stanza=0, kind="verse", speaker="alice", note=None):
    return {
        "id": f"{scene}_{n:04d}",
        "kind": kind,
        "speaker": speaker if kind == "verse" else None,
        "speaker_note": note,
        "speech": speech,
        "stanza": stanza,
        "indent": 0,
        "text": f"text {scene} {n}",
        "src_line": n + 1,
    }


def _scene_lines(scene, speeches):
    """speeches: list of (speaker, line count)."""
    lines = []
    for si, (speaker, count) in enumerate(speeches):
        for _ in range(count):
            lines.append(_line(scene, len(lines) + 1, si, speaker=speaker))
    return lines


def make_work(tmp_path: Path, parsed_scenes) -> Work:
    root = tmp_path / "work"
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "work.toml").write_text(WORK_TOML, encoding="utf-8")
    parsed = {"work": "t", "source_sha256": "b" * 64, "scenes": parsed_scenes}
    (root / "data/parsed.json").write_text(json.dumps(parsed), encoding="utf-8")
    return load_work(root)


@pytest.fixture
def work(tmp_path):
    one = _scene_lines("one", [("alice", 3), ("bob", 2)])
    one.insert(0, _line("one", 0, 0, kind="cast"))
    one.insert(2, _line("one", 99, 0, kind="direction"))
    two = [_line("two", i + 1, 0, speaker=None) for i in range(2)]
    scenes = [
        {"id": "one", "heading": "ONE", "lines": one},
        {"id": "two", "heading": "TWO", "lines": two},
    ]
    return make_work(tmp_path, scenes)


def chunks_of(work):
    parsed, _ = annotate.load_parsed(work)
    return annotate.build_chunks(work, parsed)


def good_cache(work, chunk, **line_over):
    anns = []
    for line in chunk["lines"]:
        anns.append(
            {
                "id": line["id"],
                **line["defaults"],
                "emotion": "joyful",
                "on_stage": [{"character": "alice", "position": "left"}],
                "music": None,
            }
        )
    doc = {
        "chunk_id": chunk["chunk_id"],
        "input_sha256": chunk["input_sha256"],
        "annotations": anns,
    }
    return doc


def put_cache(work, doc):
    path = annotate.cache_dir(work) / f"{doc['chunk_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def fill_all(work):
    for chunk in chunks_of(work):
        put_cache(work, good_cache(work, chunk))


# ---------------------------------------------------------------- chunking


def test_defaults_and_content(work):
    one, two = chunks_of(work)
    assert [c["chunk_id"] for c in (one, two)] == ["one_c01", "two_c01"]
    by_id = {ln["id"]: ln for ln in one["lines"]}
    assert by_id["one_0000"]["defaults"] == {
        "speaker": "narrator",
        "kind": "stage_direction",
        "location": "hall",
        "time_of_day": "day",
    }
    assert by_id["one_0099"]["defaults"]["kind"] == "stage_direction"
    assert by_id["one_0001"]["defaults"]["speaker"] == "alice"
    assert by_id["one_0001"]["defaults"]["kind"] == "dialogue"
    # Unattributed verse -> narration spoken by the lyric voice
    assert two["lines"][0]["defaults"] == {
        "speaker": "voice",
        "kind": "narration",
        "location": "yard",
        "time_of_day": "night",
    }
    allowed = one["allowed"]
    assert allowed["characters"] == ["alice", "bob", "god"]
    assert "narrator" in allowed["speakers"] and "voice" in allowed["speakers"]
    assert allowed["locations"] == ["hall", "yard"]
    assert "neutral" in allowed["emotions"] and "left" in allowed["positions"]
    assert one["context_before"] == []
    assert one["prompt_version"] == annotate.prompt_version()


def test_never_crosses_scenes_and_respects_limit(tmp_path):
    scenes = [
        {"id": "one", "heading": "ONE", "lines": _scene_lines("one", [("alice", 30)] * 5)},
        {"id": "two", "heading": "TWO", "lines": _scene_lines("two", [("bob", 7)])},
    ]
    work = make_work(tmp_path, scenes)
    chunks = chunks_of(work)
    assert [c["chunk_id"] for c in chunks] == ["one_c01", "one_c02", "one_c03", "two_c01"]
    for chunk in chunks:
        assert len(chunk["lines"]) <= 60
        assert {ln["id"].rsplit("_", 1)[0] for ln in chunk["lines"]} == {chunk["scene"]["id"]}
    assert [len(c["lines"]) for c in chunks] == [60, 60, 30, 7]


def test_splits_at_speech_boundaries(tmp_path):
    speeches = [("alice", 25), ("bob", 25), ("alice", 25), ("bob", 10)]
    work = make_work(
        tmp_path, [{"id": "one", "heading": "O", "lines": _scene_lines("one", speeches)}]
    )
    chunks = chunks_of(work)
    assert [len(c["lines"]) for c in chunks] == [50, 35]
    assert chunks[1]["lines"][0]["id"] == "one_0051"
    assert len(chunks[1]["context_before"]) == 6
    assert chunks[1]["context_before"][-1]["id"] == "one_0050"


def test_oversized_speech_splits_at_stanza_then_hard(tmp_path):
    lines = [_line("one", i + 1, 0, stanza=i // 50) for i in range(100)]  # two stanzas of 50
    work = make_work(tmp_path, [{"id": "one", "heading": "O", "lines": lines}])
    assert [len(c["lines"]) for c in chunks_of(work)] == [50, 50]
    lines = [_line("one", i + 1, 0, stanza=0) for i in range(130)]
    work = make_work(tmp_path, [{"id": "one", "heading": "O", "lines": lines}])
    assert [len(c["lines"]) for c in chunks_of(work)] == [60, 60, 10]


def test_hash_deterministic_and_content_sensitive(work, tmp_path):
    first = [c["input_sha256"] for c in chunks_of(work)]
    assert first == [c["input_sha256"] for c in chunks_of(work)]
    parsed, _ = annotate.load_parsed(work)
    parsed["scenes"][0]["lines"][1]["text"] = "changed"
    changed = annotate.build_chunks(work, parsed)
    assert changed[0]["input_sha256"] != first[0]
    assert changed[1]["input_sha256"] == first[1]


def test_prompt_change_invalidates_hash(work, monkeypatch, tmp_path):
    before = chunks_of(work)[0]["input_sha256"]
    fake = tmp_path / "prompt.md"
    fake.write_text("different prompt", encoding="utf-8")
    monkeypatch.setattr(annotate, "PROMPT_PATH", fake)
    assert chunks_of(work)[0]["input_sha256"] != before


def test_chunk_command_writes_and_removes_stale(work, capsys):
    assert main(["--work", str(work.root), "annotate", "chunk"]) == 0
    out = annotate.chunks_dir(work)
    assert sorted(p.name for p in out.glob("*.json")) == ["one_c01.json", "two_c01.json"]
    (out / "gone_c09.json").write_text("{}", encoding="utf-8")
    snapshot = (out / "one_c01.json").read_bytes()
    main(["--work", str(work.root), "annotate", "chunk"])
    assert not (out / "gone_c09.json").exists()
    assert (out / "one_c01.json").read_bytes() == snapshot
    assert json.loads(snapshot)["chunk_id"] == "one_c01"
    assert "removed stale gone_c09.json" in capsys.readouterr().out


def test_unconfigured_scene_or_speaker_fails(tmp_path):
    scenes = [{"id": "zzz", "heading": "Z", "lines": [_line("zzz", 1, 0)]}]
    with pytest.raises(annotate.AnnotateError):
        chunks_of(make_work(tmp_path, scenes))
    scenes = [{"id": "one", "heading": "O", "lines": [_line("one", 1, 0, speaker="ghost")]}]
    with pytest.raises(annotate.AnnotateError):
        chunks_of(make_work(tmp_path / "b", scenes))


# ---------------------------------------------------------------- status / merge


def test_status_states(work, capsys):
    one, two = chunks_of(work)
    assert annotate.check_cache(work, one)[0] == "missing"
    put_cache(work, good_cache(work, one))
    assert annotate.check_cache(work, one) == ("ok", [])
    bad = good_cache(work, two)
    bad["input_sha256"] = "c" * 64
    put_cache(work, bad)
    assert annotate.check_cache(work, two)[0] == "stale"
    (annotate.cache_dir(work) / "two_c01.json").write_text("{nope", encoding="utf-8")
    assert annotate.check_cache(work, two)[0] == "invalid"
    assert main(["--work", str(work.root), "annotate", "status"]) == 0
    out = capsys.readouterr().out
    assert "one_c01: ok" in out and "two_c01: invalid" in out
    assert "summary: 2 chunks: 1 ok, 0 missing, 0 stale, 1 invalid" in out


def merge_main(work, *extra):
    return main(["--work", str(work.root), "annotate", "merge", *extra])


def test_merge_ok_and_idempotent(work, capsys):
    fill_all(work)
    assert merge_main(work) == 0
    out = work.path("data/annotated.json")
    first = out.read_bytes()
    assert merge_main(work) == 0
    assert out.read_bytes() == first
    doc = json.loads(first)
    assert doc["fallback_chunks"] == []
    assert doc["parsed_sha256"] and doc["work"] == "t"
    line = doc["scenes"][0]["lines"][0]
    assert line["id"] == "one_0000" and line["text"] and line["ann"]["emotion"] == "joyful"
    assert "id" not in line["ann"] and line["ann"]["music"] is None
    assert first.endswith(b"\n")


def break_cache(work, mutate):
    chunk = chunks_of(work)[0]
    doc = good_cache(work, chunk)
    mutate(doc)
    path = annotate.cache_dir(work) / "one_c01.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda d: d.update(input_sha256="d" * 64), "stale"),
        (lambda d: d["annotations"].pop(), "missing lines"),
        (lambda d: d["annotations"].reverse(), "not in chunk order"),
        (lambda d: d["annotations"][1].update(kind="narration"), "kind is 'narration'"),
        (lambda d: d["annotations"][1].update(speaker="bob"), "speaker is 'bob'"),
        (lambda d: d["annotations"][1].update(location="yard"), "location is 'yard'"),
        (lambda d: d["annotations"][1].update(time_of_day="dawn"), "time_of_day is 'dawn'"),
        (
            lambda d: d["annotations"][1].update(
                on_stage=[{"character": "zed", "position": "left"}]
            ),
            "unknown character 'zed'",
        ),
        (
            lambda d: d["annotations"][1].update(
                on_stage=[
                    {"character": "bob", "position": "left"},
                    {"character": "bob", "position": "right"},
                ]
            ),
            "more than once",
        ),
        (
            lambda d: d["annotations"][1].update(
                on_stage=[{"character": "narrator", "position": "left"}]
            ),
            "unknown character 'narrator'",
        ),
        (lambda d: d["annotations"][1].update(emotion="happy"), "schema"),
        (lambda d: d.update(chunk_id="two_c01"), "chunk_id"),
    ],
)
def test_merge_rejects_bad_cache(work, capsys, mutate, expect):
    two = chunks_of(work)[1]
    put_cache(work, good_cache(work, two))
    break_cache(work, mutate)
    assert merge_main(work) == 1
    err = capsys.readouterr().err
    assert "one_c01" in err and expect in err
    assert not work.path("data/annotated.json").exists()


def test_merge_without_cache_lists_all_problems(work, capsys):
    assert merge_main(work) == 1
    err = capsys.readouterr().err
    assert "one_c01: missing" in err and "two_c01: missing" in err


def test_merge_fallback(work, capsys):
    put_cache(work, good_cache(work, chunks_of(work)[1]))
    assert merge_main(work, "--fallback") == 0
    captured = capsys.readouterr()
    assert "warning: one_c01: missing" in captured.err
    doc = json.loads(work.path("data/annotated.json").read_text(encoding="utf-8"))
    assert doc["fallback_chunks"] == ["one_c01"]
    lines = {ln["id"]: ln["ann"] for s in doc["scenes"] for ln in s["lines"]}
    # cast/direction line before anyone spoke: nobody on stage
    assert lines["one_0000"]["on_stage"] == []
    assert lines["one_0000"]["emotion"] == "neutral" and lines["one_0000"]["music"] is None
    # alice speaks first -> left; bob joins -> right, alice stays
    assert lines["one_0001"]["on_stage"] == [{"character": "alice", "position": "left"}]
    assert lines["one_0005"]["on_stage"] == [
        {"character": "alice", "position": "left"},
        {"character": "bob", "position": "right"},
    ]
    # the ok chunk is kept as annotated by the LLM
    assert lines["two_0001"]["emotion"] == "joyful"
    first = work.path("data/annotated.json").read_bytes()
    merge_main(work, "--fallback")
    assert work.path("data/annotated.json").read_bytes() == first


def test_fallback_skips_characters_without_sprite(tmp_path):
    lines = [_line("one", 1, 0, speaker="god"), _line("one", 2, 1, speaker="alice")]
    work = make_work(tmp_path, [{"id": "one", "heading": "O", "lines": lines}])
    result = annotate.merge(work, fallback=True)
    anns = [ln["ann"] for ln in result.document["scenes"][0]["lines"]]
    assert anns[0]["on_stage"] == []
    assert anns[1]["on_stage"] == [{"character": "alice", "position": "left"}]


# ---------------------------------------------------------------- prompt


def test_prompt_fills_placeholders(work):
    text = annotate.render_prompt(work, "one_c01")
    assert "{{" not in text
    root = work.root.resolve()
    assert str(root / "annotations/chunks/one_c01.json") in text
    assert str(root / "annotations/cache/one_c01.json") in text
    assert "annotate status one_c01" in text
    with pytest.raises(SystemExit):
        annotate.render_prompt(work, "nope_c01")
