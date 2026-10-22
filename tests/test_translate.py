import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vnpipe import renpy_gen, renpy_tl, translate
from vnpipe.config import load_work
from vnpipe.renpy_gen import BoxRecord
from vnpipe.translate_align import (
    SETTING,
    UnknownLabel,
    find_section_lines,
    group_units,
    parse_units,
    strip_gutenberg,
)

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent
SDK = Path(os.environ.get("RENPY_SDK") or ROOT / "tools/renpy-sdk") / "renpy.sh"
SPEAKERS = {"MANAGER": "director", "POET": "poet", "THE LORD": "lord", "VOICE": "poet_voice"}


@pytest.fixture
def tl_work(tmp_path):
    """mini_work plus an English translation (fixture ebook) and its annotated script."""
    root = tmp_path / "w"
    shutil.copytree(FIXTURES / "mini_work", root)
    with (root / "work.toml").open("a", encoding="utf-8") as fh:
        fh.write((FIXTURES / "mini_tl.toml").read_text(encoding="utf-8"))
    (root / "data/raw").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "annotated_mini.json", root / "data/annotated.json")
    shutil.copyfile(FIXTURES / "mini_tl_en.txt", root / "data/raw/en.txt")
    return load_work(root)


def section(name: str) -> list[tuple[int, str]]:
    text = strip_gutenberg((FIXTURES / "mini_tl_en.txt").read_text(encoding="utf-8"))
    lyric, talk = find_section_lines(text, ["LYRIC", "TALK"])
    return {"lyric": lyric, "talk": talk}[name]


def test_strip_gutenberg_keeps_only_the_ebook_text():
    text = strip_gutenberg((FIXTURES / "mini_tl_en.txt").read_text(encoding="utf-8"))
    assert "Project Gutenberg" not in text and "Licence" not in text
    assert text.lstrip().startswith("CONTENTS")


def test_find_section_lines_skips_the_contents():
    text = strip_gutenberg((FIXTURES / "mini_tl_en.txt").read_text(encoding="utf-8"))
    lyric, _ = find_section_lines(text, ["LYRIC", "TALK"])
    assert lyric[1][1].startswith("Again ye come")  # not the indented contents entry


def test_parse_units_speeches_and_directions():
    units = parse_units(section("talk"), SPEAKERS)
    kinds = [(u.kind, u.speaker, u.label) for u in units]
    assert kinds == [
        ("dir", None, SETTING),  # place line and cast list, kept apart
        ("say", "director", "MANAGER"),
        ("say", "poet", "POET"),
        ("say", "director", "MANAGER"),
        ("dir", None, ""),  # "[_Exit_." at the end of a verse line
        ("say", "poet", "POET"),
        ("say", "lord", "THE LORD"),
        ("say", "director", "MANAGER"),
        ("say", "poet_voice", "VOICE"),
        ("say", "poet", "POET"),
    ]
    assert units[0].stanzas == [["A STAGE"], ["MANAGER. POET."]]
    assert units[3].lines == ["Hush, he hears."]
    assert units[4].text == "Exit."
    # footnote number dropped; a parenthesis inside a verse stays verse
    assert units[5].lines == [
        "My breast is pressed",
        "Beneath such weight.",
        "(The evening comes),—",
    ]


def test_a_speaker_list_maps_each_occurrence_of_a_label():
    speakers = {**SPEAKERS, "MANAGER": ["director", "lord", "poet"]}
    units = parse_units(section("talk"), speakers)
    assert [u.speaker for u in units if u.label == "MANAGER"] == ["director", "lord", "poet"]
    with pytest.raises(UnknownLabel, match="more than 2x"):
        parse_units(section("talk"), {**SPEAKERS, "MANAGER": ["director", "lord"]})


def test_parse_units_without_labels_is_one_speech():
    units = parse_units(section("lyric"), SPEAKERS, default_speaker="poet_voice")
    assert len(units) == 1 and units[0].speaker == "poet_voice"
    assert [len(st) for st in units[0].stanzas] == [3, 2]  # [Illustration] dropped


def test_group_units_shows_every_source_unit_once_in_order():
    # game 0..4, source 0..5; pairs 0-0, 2-3, 4-5: loose source units go to loose game units
    carried = group_units(5, 6, {0: 0, 2: 3, 4: 5})
    assert carried == {0: [0], 1: [1, 2], 2: [3], 3: [4], 4: [5]}
    assert sorted(s for v in carried.values() for s in v) == list(range(6))


def test_build_maps_every_scene(tl_work):
    data = translate.build(tl_work, "en")
    talk = data["scenes"]["talk"]
    assert talk["title"] == "CONVERSATION"
    assert talk["stats"]["complete"] and talk["stats"]["forced"] == 1
    units = talk["units"]
    assert units["talk_0002"][0]["stanzas"][0][0] == "Ye two, who oft my help have been,"
    assert units["talk_0010"] == [
        {
            "kind": "dir",
            "speaker": "narrator",
            "src_line": units["talk_0010"][0]["src_line"],
            "stanzas": [["Exit."]],
        }
    ]
    assert units["talk_0017"][0]["speaker"] == "poet_voice"
    assert "SETTING" not in json.dumps(data)  # the unused setting is not shown
    lyric = data["scenes"]["lyric"]
    assert list(lyric["units"]) == ["lyric_0001"]
    assert lyric["stats"]["coverage"] == 1.0
    # deterministic
    assert json.dumps(translate.build(tl_work, "en")) == json.dumps(data)


def test_override_must_quote_exactly_one_unit(tl_work):
    data = json.loads(json.dumps(tl_work.data))
    data["translations"]["en"]["overrides"][0]["source"] = "Nothing like this"
    with pytest.raises(SystemExit, match="0 source units"):
        translate.build(type(tl_work)(tl_work.root, data), "en")


def test_split_long_lines_keeps_the_text_verbatim():
    line = ("One two three four five six seven eight nine ten. " * 6).strip() + " End!—Next part"
    chunks = renpy_tl._split_long(line)
    assert len(chunks) > 1
    assert all(len(c.split()) <= renpy_tl.MAX_LINE_WORDS for c in chunks)
    assert re.sub(r"\s+", "", "".join(chunks)) == re.sub(r"\s+", "", line)


def test_voice_follows_the_first_box_of_the_same_speaker():
    tl = renpy_tl.Translation(
        "en",
        "english",
        "English",
        {},
        {
            "scenes": {
                "s": {
                    "units": {
                        "s_0001": [
                            {"kind": "say", "speaker": "poet", "src_line": 1, "stanzas": [["A"]]},
                            {
                                "kind": "say",
                                "speaker": "lord",
                                "src_line": 2,
                                "stanzas": [["B"], ["C"]],
                            },
                            {
                                "kind": "dir",
                                "speaker": "narrator",
                                "src_line": 3,
                                "stanzas": [["D"]],
                            },
                        ],
                    }
                }
            },
        },
    )
    scene = {
        "id": "s",
        "lines": [
            {
                "id": f"s_000{i}",
                "kind": "verse",
                "speech": i,
                "text": "x",
                "ann": {"speaker": who, "kind": "speech"},
            }
            for i, who in ((1, "lord"), (2, "lord"))
        ],
    }
    records = [
        BoxRecord("s_0001", "lord", False, voice="voice/a.ogg"),
        BoxRecord("s_0002", "lord", False),
    ]
    cast = {"poet": {}, "lord": {}}
    out = renpy_tl.render_scene_blocks(tl, scene, records, "poet_voice", cast)
    assert out == [
        "translate english s_0001:",
        '    c_poet "A"',
        '    voice "voice/a.ogg"',
        '    c_lord "B"',
        "    voice sustain",
        '    c_lord "C"',
        "    voice sustain",
        '    c_narrator "D"',
        "",
        "translate english s_0002:",
        "    pass",
        "",
    ]


def test_clips_follow_the_box_position_when_the_speaker_differs():
    # an ensemble line ("chorus") shown by a named source speaker, and a speaker mismatch
    says = ["girl", "student", "girl"]
    clips = [("chorus", "a.ogg"), ("student", "b.ogg"), ("burgher", "c.ogg")]
    assert renpy_tl.assign_clips(clips, says) == {0: "a.ogg", 1: "b.ogg", 2: "c.ogg"}
    # a later speaker match is skipped if it leaves no box for the clips after it
    assert renpy_tl.assign_clips([("x", "a"), ("y", "b")], ["y", "x"]) == {0: "a", 1: "b"}
    assert renpy_tl.assign_clips([("y", "a")], ["x", "y"]) == {1: "a"}
    with pytest.raises(ValueError, match="2 voice clips"):
        renpy_tl.assign_clips([("x", "a"), ("x", "b")], ["x"])


def test_changed_mapping_config_is_an_error(tl_work, tmp_path):
    translate.write_json(translate.output_path(tl_work, "en"), translate.build(tl_work, "en"))
    annotated = json.loads(tl_work.path("data/annotated.json").read_text(encoding="utf-8"))
    data = json.loads(json.dumps(tl_work.data))
    data["translations"]["en"]["names"]["lord"] = "God"  # names need no rebuild
    renpy_gen.generate(type(tl_work)(tl_work.root, data), annotated, tmp_path / "ok")
    data["translations"]["en"]["speakers"]["POET"] = "director"
    with pytest.raises(ValueError, match="mapping configuration"):
        renpy_gen.generate(type(tl_work)(tl_work.root, data), annotated, tmp_path / "p")


@pytest.fixture
def tl_project(tl_work, tmp_path):
    translate.write_json(translate.output_path(tl_work, "en"), translate.build(tl_work, "en"))
    annotated = json.loads(tl_work.path("data/annotated.json").read_text(encoding="utf-8"))
    out = tmp_path / "proj"
    renpy_gen.generate(tl_work, annotated, out)
    return out


def test_generate_writes_a_block_for_every_say(tl_project):
    game = tl_project / "game"
    script = (game / "script.rpy").read_text(encoding="utf-8")
    tl = (game / "tl/english/script.rpy").read_text(encoding="utf-8")
    ids = re.findall(r" id (\w+)$", script, re.M)
    assert ids and ids == re.findall(r"^translate english (\w+):$", tl, re.M)
    # the lyric scene is NVL: English pages cleared like the Spanish ones
    lyric = tl.split("translate english lyric_0001:\n")[1].split("\n\n")[0].splitlines()
    assert lyric == [
        "    nvl clear",
        '    c_poet_voice_nvl "Again ye come, ye hovering Forms! I find ye,\\n'
        "As early to my clouded sight ye shone!\\n"
        'Shall I attempt, this once, to seize and bind ye?"',
        "    nvl clear",
        '    c_poet_voice_nvl "And dear ones rise,\\nWhom fortune stole."',
    ]
    # director's 6 lines: 4 + 2 in ADV; the rest of his Spanish unit shows nothing
    talk = tl.split("translate english talk_0002:\n")[1].split("\n\n")[0].splitlines()
    assert [ln.count("\\n") for ln in talk] == [3, 1]
    assert "translate english talk_0006:\n    pass" in tl


def test_generate_translates_names_cards_and_menus(tl_project):
    game = tl_project / "game"
    strings = (game / "tl/english/strings.rpy").read_text(encoding="utf-8")
    for old, new in (
        ("El Director", "Manager"),
        ("CONVERSACIÓN", "CONVERSATION"),
        ("Escenas", "Scenes"),
        ("Estilo visual", "Visual style"),
        ("Continuará…", "To be continued…"),
    ):
        assert f'    old "{old}"\n    new "{new}"' in strings
    languages = (game / "languages.rpy").read_text(encoding="utf-8")
    assert "define vn_languages = [(None, 'Español'), ('english', 'English')]" in languages
    menu = (game / "menu_data.rpy").read_text(encoding="utf-8")
    credits_tl = json.loads(re.search(r"^define vn_credits_tl = (.*)$", menu, re.M).group(1))
    text = dict(credits_tl["english"])["Text"]
    assert "Translation: E. Translator" in text
    assert dict(credits_tl["english"]).get("Voices") is None  # no voice clips in the fixture


def test_missing_names_are_an_error(tl_work, tmp_path):
    translate.write_json(translate.output_path(tl_work, "en"), translate.build(tl_work, "en"))
    data = json.loads(json.dumps(tl_work.data))
    del data["translations"]["en"]["names"]["lord"]
    annotated = json.loads(tl_work.path("data/annotated.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="lord"):
        renpy_gen.generate(type(tl_work)(tl_work.root, data), annotated, tmp_path / "p")


def test_stale_translation_is_an_error(tl_work, tmp_path):
    translate.write_json(translate.output_path(tl_work, "en"), translate.build(tl_work, "en"))
    path = tl_work.path("data/annotated.json")
    annotated = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(annotated, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="translate build"):
        renpy_gen.generate(tl_work, annotated, tmp_path / "p")


@pytest.mark.skipif(not SDK.exists(), reason="Ren'Py SDK not found (set RENPY_SDK)")
def test_renpy_lint_with_translation(tl_project, tmp_path):
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    (tmp_path / "home").mkdir()
    result = subprocess.run(
        [str(SDK), str(tl_project), "lint"], capture_output=True, text=True, env=env, timeout=300
    )
    report = result.stdout + result.stderr
    assert result.returncode == 0, report
    assert "Traceback" not in report and "error" not in report.lower(), report
    assert "The english translation contains" in report
