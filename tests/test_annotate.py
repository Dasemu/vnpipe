import json
import sys
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

[staging.one]
cast = { bob = "right" }

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
[cast.carl]
name = "Carl"
sprite = true
[cast.dora]
name = "Dora"
sprite = true
[cast.god]
name = "God"
sprite = false
"""

# Fake runner: counts calls, then answers from $FAKE_ANSWERS (a JSON file: chunk id ->
# list of answers, one per call) as the claude envelope (or plain text if FAKE_PLAIN is set).
FAKE_RUNNER = r"""
import json, os, re, sys
text = sys.stdin.read()
chunk = re.search(r'^SCENE (\S+)', text).group(1)
part = re.search(r'part (\d+)/', text)
key = chunk + ("_c%02d" % int(part.group(1)) if part else "_c01")
state = os.environ["FAKE_STATE"]
answers = json.load(open(os.environ["FAKE_ANSWERS"]))[key]
open(state, "a").write(key + "\n")
calls = sum(1 for l in open(state) if l.strip() == key)
answer = answers[min(calls - 1, len(answers) - 1)]
if answer is None:
    sys.exit(3)
if os.environ.get("FAKE_PLAIN"):
    print(answer)
else:
    print(json.dumps({"result": answer, "is_error": False, "total_cost_usd": 0.001,
        "usage": {"input_tokens": 10, "output_tokens": 5,
                  "cache_creation_input_tokens": 100, "cache_read_input_tokens": 7}}))
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


def make_work(tmp_path: Path, scenes) -> Work:
    root = tmp_path / "work"
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "work.toml").write_text(WORK_TOML, encoding="utf-8")
    parsed = {"work": "t", "source_sha256": "b" * 64, "scenes": scenes}
    (root / "data/parsed.json").write_text(json.dumps(parsed), encoding="utf-8")
    return load_work(root)


@pytest.fixture
def work(tmp_path):
    # scene one: 0 cast, 1-3 alice (speech 0), 4 direction, 5-6 bob (speech 1),
    # 7-8 carl (speech 2), 9 alice (speech 3)
    one = [
        _line("one", 1, 0, kind="cast"),
        *[_line("one", n, 0) for n in (2, 3, 4)],
        _line("one", 5, 0, kind="direction"),
        *[_line("one", n, 1, speaker="bob") for n in (6, 7)],
        *[_line("one", n, 2, speaker="carl") for n in (8, 9)],
        _line("one", 10, 3),
    ]
    two = [_line("two", i + 1, 0, speaker=None) for i in range(2)]
    scenes = [
        {"id": "one", "heading": "ONE", "lines": one},
        {"id": "two", "heading": "TWO", "lines": two},
    ]
    return make_work(tmp_path, scenes)


def chunks_of(work):
    parsed, _ = annotate.load_parsed(work)
    return annotate.build_chunks(work, parsed)


ONE_OK = "M 1 sacred\nS 0 solemn\nL 4 joyful\nS 1 neutral\nS 2 angry\nS 3 ironic\n"
TWO_OK = "M 1 none\nS 0 neutral\n"


def runner_env(tmp_path, monkeypatch, answers, plain=False):
    script = tmp_path / "fake_runner.py"
    script.write_text(FAKE_RUNNER, encoding="utf-8")
    ans = tmp_path / "answers.json"
    ans.write_text(json.dumps(answers), encoding="utf-8")
    state = tmp_path / "calls.txt"
    monkeypatch.setenv("FAKE_ANSWERS", str(ans))
    monkeypatch.setenv("FAKE_STATE", str(state))
    if plain:
        monkeypatch.setenv("FAKE_PLAIN", "1")
    else:
        monkeypatch.delenv("FAKE_PLAIN", raising=False)
    return f"{sys.executable} {script}", state


def run_cli(work, *argv):
    return main(["--work", str(work.root), "annotate", *argv])


# ---------------------------------------------------------------- chunking and rendering


def test_chunks_are_whole_speeches_and_never_cross_scenes(work):
    one, two = chunks_of(work)
    assert (one.id, two.id) == ("one_c01", "two_c01")
    assert one.speeches == [0, 1, 2, 3] and two.speeches == [0]
    assert (one.first, one.last) == (1, 10)


def test_long_scene_splits_on_speech_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(annotate, "MAX_CHUNK_LINES", 4)
    lines = [
        *[_line("one", n, 0) for n in (1, 2, 3)],
        *[_line("one", n, 1, speaker="bob") for n in (4, 5, 6)],
        *[_line("one", n, 2) for n in (7, 8)],
    ]
    work = make_work(tmp_path, [{"id": "one", "heading": "ONE", "lines": lines}])
    chunks = chunks_of(work)
    assert [[annotate.line_no(ln["id"]) for ln in c.lines] for c in chunks] == [
        [1, 2, 3],
        [4, 5, 6],
        [7, 8],
    ]
    assert [c.id for c in chunks] == ["one_c01", "one_c02", "one_c03"]
    assert "part 2/3" in chunks[1].text
    # later chunks list the sprite speakers seen so far plus the pinned cast
    assert "on stage at start: bob*, alice" in chunks[2].text


def test_render_format(work):
    one, two = chunks_of(work)
    lines = one.text.splitlines()
    assert lines[0] == (
        'SCENE one "ONE" | hall | day | sprite cast: bob (Bob), alice (Alice), carl (Carl)'
        " | on stage at start: bob*"
    )
    assert "(D0001 text one 1)" in lines  # cast list is shown as a direction
    assert lines[2:5] == ["[S0 ALICE]", "0002 text one 2", "0003 text one 3"]
    assert "(D0005 text one 5)" in lines
    assert "[S1 BOB]" in lines
    # unattributed verse: header without a name; no sprite cast
    assert two.text.splitlines()[0].endswith("sprite cast: - | on stage at start: -")
    assert "[S0]" in two.text


def test_input_hash_covers_text_and_prompt(work, monkeypatch):
    before = chunks_of(work)[0].input_sha
    assert chunks_of(work)[0].input_sha == before
    monkeypatch.setattr(annotate, "prompt_version", lambda: "other")
    assert chunks_of(work)[0].input_sha != before


def test_prompt_lists_the_vocabularies():
    text = annotate.PROMPT_PATH.read_text(encoding="utf-8")
    for word in (*annotate.EMOTIONS, *annotate.MUSIC):
        assert f"- {word}:" in text


def test_prompt_command_prints_instructions_and_input(work, capsys):
    assert run_cli(work, "prompt", "one_c01") == 0
    out = capsys.readouterr().out
    assert "Output records" in out and "[S0 ALICE]" in out
    with pytest.raises(SystemExit):
        run_cli(work, "prompt", "nope")


# ---------------------------------------------------------------- parsing and validation


def parse(work, text, idx=0):
    return annotate.parse_records(text, chunks_of(work)[idx])


def test_parse_valid_records(work):
    rec = parse(work, ONE_OK + "E 6 -alice\n\n")
    assert rec.speech == {0: "solemn", 1: "neutral", 2: "angry", 3: "ironic"}
    assert rec.change == {4: "joyful"}
    assert rec.music == {1: "sacred"}
    assert rec.events == {6: [("-", "alice")]}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (ONE_OK.replace("S 0 solemn", "S 0 happy"), "unknown emotion 'happy'"),
        (ONE_OK.replace("sacred", "loud"), "unknown music mood 'loud'"),
        (ONE_OK + "E 6 +zed\n", "unknown sprite id '\\+zed'"),
        (ONE_OK + "E 6 -carl\n", "carl is not on stage at that line"),
        (ONE_OK + "E 3 -bob\n", "bob is fixed on stage"),
        (ONE_OK + "E 6 +god\n", "unknown sprite id '\\+god'"),  # no sprite
        (ONE_OK + "E 6 alice\n", "bad or unknown sprite id 'alice'"),
        (ONE_OK.replace("S 3 ironic\n", ""), "missing S record for speech 3"),
        (ONE_OK + "S 9 neutral\n", "unknown speech 9"),
        (ONE_OK + "S 0 neutral\n", "duplicate S record for speech 0"),
        (ONE_OK + "L 99 joyful\n", "line outside the chunk"),
        (ONE_OK + "L 5 joyful\n", "L needs a spoken line"),  # direction line
        (ONE_OK + "M 99 comic\n", "line outside the chunk"),
        (ONE_OK + "hello\n", "unrecognised record"),
        (ONE_OK + "S 1\n", "unrecognised record"),
        ("S 0 solemn\n" + ONE_OK, "first record must be an M"),
        (ONE_OK.replace("M 1 sacred", "M 6 sacred"), "first record must be an M"),
        ("", "empty output"),
    ],
)
def test_parse_errors(work, text, message):
    with pytest.raises(annotate.ParseError, match=message):
        parse(work, text)


def test_redundant_entrances_are_dropped_and_event_lines_merge(work):
    rec = parse(work, ONE_OK + "E 6 +alice\nE 6 -alice\n")
    assert rec.events == {6: [("-", "alice")]}  # +alice is redundant: she speaks again at 10


def test_first_music_may_sit_on_the_first_spoken_line(work):
    assert parse(work, ONE_OK.replace("M 1 sacred", "M 2 sacred")).music == {2: "sacred"}


def test_parse_reports_every_problem(work):
    with pytest.raises(annotate.ParseError) as err:
        parse(work, ONE_OK.replace("solemn", "x").replace("neutral", "y"))
    assert str(err.value).count("unknown emotion") == 2


# ---------------------------------------------------------------- expansion


def expand(work, rec_text_by_chunk):
    """Expand scene one/two like merge does; returns per-line annotations by id."""
    out, music, stages = {}, None, {}
    for chunk in chunks_of(work):
        text = rec_text_by_chunk.get(chunk.id)
        rec = annotate.parse_records(text, chunk) if text else None
        stage = stages.setdefault(
            chunk.scene_id, annotate.Stage(annotate.pinned_cast(work, chunk.scene_id))
        )
        items, music = annotate.expand_chunk(work, chunk, rec, stage, music)
        out.update({ln["id"]: it for ln, it in zip(chunk.lines, items, strict=True)})
    return out


def test_expansion_emotions_and_defaults(work):
    ann = expand(work, {"one_c01": ONE_OK})
    assert ann["one_0001"]["kind"] == "stage_direction" and ann["one_0001"]["speaker"] == "narrator"
    assert ann["one_0002"]["emotion"] == "solemn" and ann["one_0003"]["emotion"] == "solemn"
    assert ann["one_0004"]["emotion"] == "joyful"  # L applies from that line on
    assert ann["one_0005"]["emotion"] == "neutral"  # directions are neutral
    assert ann["one_0006"]["emotion"] == "neutral"
    assert ann["one_0008"]["emotion"] == "angry"
    assert ann["one_0010"]["emotion"] == "ironic"  # new speech restarts at its S emotion
    assert ann["one_0002"]["location"] == "hall" and ann["one_0002"]["time_of_day"] == "day"
    assert ann["one_0002"]["kind"] == "dialogue" and ann["one_0002"]["speaker"] == "alice"


def test_first_music_covers_the_lead_in(work):
    ann = expand(work, {"one_c01": ONE_OK.replace("M 1 sacred", "M 2 sacred")})
    assert ann["one_0001"]["music"] == "sacred"
    ann = expand(work, {"one_c01": ONE_OK.replace("M 1 sacred", "M 2 comic")})
    assert ann["one_0001"]["music"] == "comic"  # not a mood carried over from elsewhere


def test_expansion_unattributed_verse_uses_lyric_voice(work):
    ann = expand(work, {})
    assert ann["two_0001"]["speaker"] == "voice" and ann["two_0001"]["kind"] == "narration"
    assert ann["two_0001"]["on_stage"] == []  # voice has no sprite


def test_expansion_auto_enter_and_positions(work):
    ann = expand(work, {"one_c01": ONE_OK})
    # bob is pinned right from the start; alice speaks first and takes the first free slot
    assert ann["one_0001"]["on_stage"] == [{"character": "bob", "position": "right"}]
    assert ann["one_0002"]["on_stage"] == [
        {"character": "bob", "position": "right"},
        {"character": "alice", "position": "left"},
    ]
    assert [s["character"] for s in ann["one_0008"]["on_stage"]] == ["bob", "alice", "carl"]
    assert ann["one_0008"]["on_stage"][2]["position"] == "center"


def test_expansion_exit_and_stable_position(work):
    text = ONE_OK + "E 6 -alice\n"
    ann = expand(work, {"one_c01": text})
    assert [s["character"] for s in ann["one_0005"]["on_stage"]] == ["bob", "alice"]
    assert [s["character"] for s in ann["one_0006"]["on_stage"]] == ["bob"]
    # alice speaks again at line 10 and re-enters at her original position
    last = {s["character"]: s["position"] for s in ann["one_0010"]["on_stage"]}
    assert last["alice"] == "left" and last["bob"] == "right"
    assert last["carl"] == "center"


def test_pinned_character_cannot_be_removed(work):
    chunk = chunks_of(work)[0]
    rec = annotate.parse_records(ONE_OK, chunk)
    rec.events[3] = [("-", "bob")]
    items, _ = annotate.expand_chunk(work, chunk, rec, annotate.Stage({"bob": "right"}), None)
    assert "bob" in [s["character"] for s in items[2]["on_stage"]]


def test_expansion_music_carries_forward_across_chunks_and_scenes(work):
    ann = expand(work, {"one_c01": ONE_OK + "M 8 mystical\n"})
    assert ann["one_0001"]["music"] == "sacred"
    assert ann["one_0007"]["music"] == "sacred" and ann["one_0008"]["music"] == "mystical"
    assert ann["two_0001"]["music"] == "mystical"  # no records for scene two: carried over
    ann = expand(work, {"one_c01": ONE_OK, "two_c01": TWO_OK})
    assert ann["two_0001"]["music"] == "none"


def test_stage_carries_across_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(annotate, "MAX_CHUNK_LINES", 3)
    lines = [
        *[_line("one", n, 0) for n in (1, 2, 3)],
        *[_line("one", n, 1, speaker="carl") for n in (4, 5, 6)],
        *[_line("one", n, 2, speaker="bob") for n in (7, 8, 9)],
    ]
    work = make_work(tmp_path, [{"id": "one", "heading": "ONE", "lines": lines}])
    ann = expand(
        work,
        {
            "one_c01": "M 1 comic\nS 0 neutral\n",
            "one_c02": "M 4 comic\nS 1 neutral\nE 5 -alice\n",
            "one_c03": "M 7 comic\nS 2 neutral\n",
        },
    )
    assert [s["character"] for s in ann["one_0009"]["on_stage"]] == ["bob", "carl"]


# ---------------------------------------------------------------- runner, retry, cache


def test_run_caches_logs_usage_and_skips_cached(work, tmp_path, monkeypatch, capsys):
    runner, state = runner_env(tmp_path, monkeypatch, {"one_c01": [ONE_OK], "two_c01": [TWO_OK]})
    assert run_cli(work, "run", "--runner", runner, "--jobs", "2") == 0
    cache = annotate.cache_dir(work)
    body = (cache / "one_c01.txt").read_text(encoding="utf-8")
    assert body.startswith("# input=") and "model=haiku" in body and "S 0 solemn" in body
    rows = annotate.usage_rows(work)
    assert sorted(r["chunk"] for r in rows) == ["one_c01", "two_c01"]
    assert rows[0]["input_tokens"] == 10 and rows[0]["cache_creation_tokens"] == 100
    assert rows[0]["cost_usd"] == 0.001 and rows[0]["model"] == "haiku" and "seconds" in rows[0]
    assert len(state.read_text().split()) == 2
    # second run: everything cached, runner not called again
    capsys.readouterr()
    assert run_cli(work, "run", "--runner", runner) == 0
    assert "2 cached" in capsys.readouterr().out
    assert len(state.read_text().split()) == 2
    assert run_cli(work, "status") == 0
    assert "2 done, 0 pending, 0 failed" in capsys.readouterr().out


def test_run_accepts_plain_text_and_filters_with_only(work, tmp_path, monkeypatch, capsys):
    runner, state = runner_env(tmp_path, monkeypatch, {"one_c01": [ONE_OK]}, plain=True)
    assert run_cli(work, "run", "--runner", runner, "--only", "one_*") == 0
    assert state.read_text().split() == ["one_c01"]
    assert annotate.usage_rows(work)[0]["input_tokens"] == 0
    capsys.readouterr()
    run_cli(work, "status")
    assert "1 done, 1 pending, 0 failed" in capsys.readouterr().out


def test_other_model_reruns_and_prompt_change_makes_stale(work, tmp_path, monkeypatch, capsys):
    runner, state = runner_env(tmp_path, monkeypatch, {"one_c01": [ONE_OK, ONE_OK]})
    run_cli(work, "run", "--runner", runner, "--only", "one_c01")
    run_cli(work, "run", "--runner", runner, "--only", "one_c01", "--model", "sonnet")
    assert len(state.read_text().split()) == 2
    assert "model=sonnet" in (annotate.cache_dir(work) / "one_c01.txt").read_text()
    monkeypatch.setattr(annotate, "prompt_version", lambda: "changed")
    capsys.readouterr()
    run_cli(work, "status")
    out = capsys.readouterr().out
    assert "one_c01: stale" in out and "0 done, 2 pending" in out


def test_retry_appends_error_then_succeeds(work, tmp_path, monkeypatch):
    bad = ONE_OK.replace("S 3 ironic\n", "")
    runner, state = runner_env(tmp_path, monkeypatch, {"one_c01": [bad, ONE_OK]})
    ok, _ = annotate.annotate_chunk(work, chunks_of(work)[0], runner, "haiku")
    assert ok and len(state.read_text().split()) == 2
    assert [r["attempt"] for r in annotate.usage_rows(work)] == [1, 2]
    assert "S 3 ironic" in (annotate.cache_dir(work) / "one_c01.txt").read_text()


def test_retry_prompt_contains_the_error(work, monkeypatch):
    seen = []

    def fake(template, model, text):
        seen.append(text)
        return annotate.Reply("M 1 comic" if len(seen) == 1 else ONE_OK, {}, 0.0, 0.1)

    monkeypatch.setattr(annotate, "run_command", fake)
    ok, _ = annotate.annotate_chunk(work, chunks_of(work)[0], "x", "haiku")
    assert ok
    assert seen[1].startswith(chunks_of(work)[0].text)
    assert "missing S record for speech 0" in seen[1] and "M 1 comic" in seen[1]


def test_failed_chunk_does_not_stop_others(work, tmp_path, monkeypatch, capsys):
    runner, _ = runner_env(
        tmp_path, monkeypatch, {"one_c01": ["garbage", "still garbage"], "two_c01": [TWO_OK]}
    )
    assert run_cli(work, "run", "--runner", runner) == 1
    cache = annotate.cache_dir(work)
    assert not (cache / "one_c01.txt").exists() and (cache / "one_c01.failed").exists()
    assert (cache / "two_c01.txt").exists()
    capsys.readouterr()
    run_cli(work, "status")
    out = capsys.readouterr().out
    assert "one_c01: failed" in out and "1 done, 0 pending, 1 failed" in out
    # a later run retries the failed chunk only
    runner, state = runner_env(tmp_path, monkeypatch, {"one_c01": [ONE_OK]})
    assert run_cli(work, "run", "--runner", runner) == 0
    assert state.read_text().split().count("two_c01") == 1  # not asked again
    assert not (cache / "one_c01.failed").exists()


def test_runner_crash_is_a_failure(work, tmp_path, monkeypatch):
    runner, _ = runner_env(tmp_path, monkeypatch, {"one_c01": [None], "two_c01": [TWO_OK]})
    assert run_cli(work, "run", "--runner", runner) == 1
    assert (annotate.cache_dir(work) / "two_c01.txt").exists()


def test_envelope_error_is_runner_error(monkeypatch):
    class Proc:
        returncode = 0
        stdout = json.dumps({"result": "boom", "is_error": True})
        stderr = ""

    monkeypatch.setattr(annotate.subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(annotate.RunnerError, match="boom"):
        annotate.run_command("cmd", "haiku", "x")


def test_default_runner_command_substitution(monkeypatch):
    seen = {}

    class Proc:
        returncode = 0
        stdout = "M 1 comic"
        stderr = ""

    def fake(args, **kwargs):
        seen["args"], seen["input"] = args, kwargs["input"]
        return Proc()

    monkeypatch.setattr(annotate.subprocess, "run", fake)
    annotate.run_command(annotate.DEFAULT_RUNNER, "haiku", "hello")
    args = seen["args"]
    assert (
        args[:3] == ["env", "MAX_THINKING_TOKENS=0", "claude"]
        and args[3] == "-p"
        and args[args.index("--model") + 1] == "haiku"
    )
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--system-prompt-file") + 1] == str(annotate.PROMPT_PATH)
    assert seen["input"] == "hello"


# ---------------------------------------------------------------- merge


def test_merge_partial_uses_fallback_and_reports(work, tmp_path, monkeypatch, capsys):
    runner, _ = runner_env(tmp_path, monkeypatch, {"one_c01": [ONE_OK]})
    run_cli(work, "run", "--runner", runner, "--only", "one_c01")
    assert run_cli(work, "merge", "--strict") == 1
    assert not work.path("data/annotated.json").exists()
    capsys.readouterr()
    assert run_cli(work, "merge") == 0
    cap = capsys.readouterr()
    assert "scene two: NOT annotated" in cap.err and "scene one" not in cap.err
    doc = json.loads(work.path("data/annotated.json").read_text(encoding="utf-8"))
    assert doc["fallback_chunks"] == ["two_c01"]
    first = doc["scenes"][0]["lines"][1]
    assert first["ann"]["emotion"] == "solemn" and first["id"] == "one_0002"
    assert list(first["ann"]) == list(annotate.ANN_ORDER)
    assert doc["scenes"][1]["lines"][0]["ann"]["emotion"] == "neutral"


def test_merge_is_deterministic(work, tmp_path, monkeypatch):
    runner, _ = runner_env(tmp_path, monkeypatch, {"one_c01": [ONE_OK], "two_c01": [TWO_OK]})
    run_cli(work, "run", "--runner", runner)
    assert run_cli(work, "merge", "--strict") == 0
    first = work.path("data/annotated.json").read_bytes()
    run_cli(work, "merge", "--strict")
    assert work.path("data/annotated.json").read_bytes() == first
    assert json.loads(first)["fallback_chunks"] == []


def test_unconfigured_scene_is_an_error(work):
    parsed, _ = annotate.load_parsed(work)
    parsed["scenes"][0]["id"] = "zzz"
    with pytest.raises(annotate.AnnotateError):
        annotate.build_chunks(work, parsed)
