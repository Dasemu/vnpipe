import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from vnpipe import cli, renpy_gen
from vnpipe.config import load_work
from vnpipe.schema import validate

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
SDK = Path(os.environ.get("RENPY_SDK") or ROOT / "tools/renpy-sdk") / "renpy.sh"


@pytest.fixture(scope="module")
def work():
    return load_work(FIXTURES / "mini_work")


@pytest.fixture(scope="module")
def annotated():
    data = json.loads((FIXTURES / "annotated_mini.json").read_text(encoding="utf-8"))
    validate(data, "annotated")
    return data


@pytest.fixture(scope="module")
def project(work, annotated, tmp_path_factory):
    out = tmp_path_factory.mktemp("proj")
    renpy_gen.generate(work, annotated, out)
    return out


@pytest.fixture(scope="module")
def script(project) -> str:
    return (project / "game/script.rpy").read_text(encoding="utf-8")


def scene_block(script: str, scene_id: str) -> list[str]:
    lines = script.split("\n")
    start = lines.index(f"label scene_{scene_id}:")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("label ")), None)
    if end is None:
        end = len(lines)
    return [ln.strip() for ln in lines[start:end]]


def say_lines(block: list[str]) -> list[str]:
    return [ln for ln in block if ln.startswith("c_")]


def test_escape_text():
    assert renpy_gen.escape_text("a [b] {c}") == "a [[b] {{c}"
    assert renpy_gen.escape_text('say "hi"\\') == 'say \\"hi\\"\\\\'
    assert renpy_gen.escape_text("a\nb") == "a\\nb"


def test_escaping_in_script(script):
    assert "¿qué pensáis [[ahora]?" in script
    assert "Es mi {{anhelo} preferente" in script
    assert 'lo \\"mejor\\" que pueda.' in script


def test_title_card_and_end(script):
    block = scene_block(script, "talk")
    assert "show card_talk at truecenter" in block
    assert "hide card_talk" in block
    assert "with fade" in block
    assert "scene bg theatre at tint_none" in block
    assert script.rstrip().endswith("return")
    assert "show card_end at truecenter" in script and "label the_end:" in script


def test_nvl_pages_per_stanza(script):
    block = scene_block(script, "lyric")
    says = say_lines(block)
    # 9-line stanza -> pages of 8 and 1, then the 2-line second stanza
    assert [s.count("\\n") + 1 for s in says] == [8, 1, 2]
    assert all(s.startswith("c_poet_voice_nvl ") for s in says)
    assert block.count("nvl clear") == 3
    assert block[-2:] == ["nvl hide dissolve", ""]
    assert not any(ln.startswith("show ") and "vn_" in ln for ln in block)


def test_adv_grouping(script):
    says = say_lines(scene_block(script, "talk"))
    # director's 6 verse lines -> 4 + 2
    assert says[0].count("\\n") == 3
    assert says[1].count("\\n") == 1
    assert says[0].startswith("c_director ")
    # split lines (indent >= 6) get their own boxes
    assert says[2] == 'c_poet "¡Vete, profano!"'
    assert says[3] == 'c_director "Calla, que oye."'
    # stage direction: narrator, own box
    assert says[4] == 'c_narrator "Se aleja."'
    # poet's 3 lines in one box
    assert says[5].count("\\n") == 2


def test_cue_forces_box_break_and_emits_before_line(script):
    block = scene_block(script, "talk")
    cue_start = block.index("# talk_0014 .. talk_0015")
    prefix = block[:cue_start]
    # cue 1: slow bg change with dark tint and sprites hidden, before the Lord's lines
    assert "show bg heaven at tint_dark" in prefix
    assert prefix[prefix.index("show bg heaven at tint_dark") + 1] == "with vn_slow"
    assert "hide poet" in prefix[-6:]
    # Poet's box ended before the cue (box break at cue line)
    assert any("y el ocaso llega." in ln for ln in prefix)


def test_mode_switch_and_flash_shake(script):
    block = scene_block(script, "talk")
    i_nvl = block.index("# talk_0016")
    # nvl cue at 0016: sprites hidden (nothing changes the bg), director spoke in nvl mode
    assert block[i_nvl + 1].startswith("c_director_nvl ")
    assert "nvl clear" in block[i_nvl:]
    # lyric voice lines in nvl are grouped into one page
    pages = [ln for ln in block if ln.startswith("c_poet_voice_nvl")]
    assert pages == ['c_poet_voice_nvl "Callan los pájaros;\\nla noche cae."']
    # cue 3: leave nvl, flash + shake, tint reset
    i_cue = block.index("# talk_0019")
    assert block[i_cue - 4 : i_cue] == [
        "nvl hide dissolve",
        "show bg theatre at tint_none",
        "with vn_flash",
        "show layer master at vn_shake",
    ]


def test_expression_mapping(script):
    block = scene_block(script, "talk")
    # first line of the first box is joyful -> director "pleased"
    assert "show director pleased at vn_left" in block
    # poet angry -> indignant (split box); director stays pleased/neutral as per his own lines
    assert "show poet indignant at vn_right" in block
    # poet melancholic -> neutral
    assert "show poet neutral at vn_right" in block


def test_stable_positions(script):
    block = scene_block(script, "talk")
    shows = [ln for ln in block if ln.startswith("show poet ")]
    assert shows and all(ln.endswith("at vn_right") for ln in shows)
    assert not any("vn_center" in ln for ln in block)  # annotated centre was never used


def test_sprite_false_never_shown(script):
    assert "show lord" not in script
    assert "show poet_voice" not in script
    assert "c_lord " in script  # still speaks, with name and colour


def test_characters_and_images(project):
    chars = (project / "game/characters.rpy").read_text(encoding="utf-8")
    assert (
        'define c_director = Character("El Director", who_color="#c9a25b", '
        'ctc="vn_ctc", ctc_position="nestled")' in chars
    )
    assert 'define c_director_nvl = Character("El Director", kind=nvl' in chars
    assert chars.count('ctc="vn_ctc"') == chars.count("Character(")
    assert "define c_narrator = Character(None, what_color=" in chars
    assert "what_italic=True" in chars
    nameless_nvl = next(ln for ln in chars.split("\n") if ln.startswith("define c_poet_voice_nvl"))
    assert "None" in nameless_nvl and "nvl_thought" in nameless_nvl
    images = (project / "game/images.rpy").read_text(encoding="utf-8")
    assert 'image bg dedication = "images/bg/default/dedication.webp"' in images
    assert 'image director pleased = "images/sprites/default/director_pleased.webp"' in images
    assert (project / "game/images/sprites/default/poet_indignant.webp").is_file()
    assert (project / "game/images/bg/default/theatre.webp").is_file()
    assert not (project / "game/images/sprites/default/lord_neutral.webp").exists()


def test_title_card_images(project):
    images = (project / "game/images.rpy").read_text(encoding="utf-8")
    assert (
        'image card_talk = Text("CONVERSACIÓN", size=76, kerning=3, text_align=0.5, xmaximum=1600)'
        in images
    )
    assert 'image card_end = Text("Continuará…"' in images


def test_options_substitution(project):
    options = (project / "game/options.rpy").read_text(encoding="utf-8")
    assert 'config.name = _("Mini")' in options
    assert 'build.name = "mini-vn"' in options
    assert "@@" not in options
    gui = (project / "game/gui.rpy").read_text(encoding="utf-8")
    assert 'gui.main_menu_background = "bg dedication"' in gui
    assert (project / "game/gui/textbox.png").is_file()
    assert (project / "game/fonts/RobotoSlab-Regular.ttf").is_file()


def test_music_only_when_audio_exists(work, annotated, tmp_path):
    out = tmp_path / "p"
    renpy_gen.generate(work, annotated, out)
    assert "play music" not in (out / "game/script.rpy").read_text(encoding="utf-8")
    assert "label the_end:\n    scene black" in (out / "game/script.rpy").read_text(
        encoding="utf-8"
    )
    (out / "game/audio").mkdir()
    (out / "game/audio/sacred.ogg").write_bytes(b"OggS")
    renpy_gen.generate(work, annotated, out)
    text = (out / "game/script.rpy").read_text(encoding="utf-8")
    assert 'play music "audio/sacred.ogg" fadeout 1.0 fadein 1.0 if_changed' in text
    assert "stop music fadeout 1.0" in text  # mood "none" on the Lord's first line
    assert "label the_end:\n    stop music fadeout 3.0" in text  # the music fades with the end card
    assert (out / "game/audio/sacred.ogg").is_file()  # regeneration keeps user audio


def test_unknown_cue_target_fails(work, annotated):
    broken = json.loads(json.dumps(annotated))
    broken["scenes"][1]["lines"] = broken["scenes"][1]["lines"][:5]
    with pytest.raises(ValueError, match="unknown line ids"):
        renpy_gen.render_script(work, broken)


def test_unknown_speaker_fails(work, annotated):
    broken = json.loads(json.dumps(annotated))
    broken["scenes"][1]["lines"][1]["ann"]["speaker"] = "ghost"
    with pytest.raises(ValueError, match="ghost"):
        renpy_gen.render_script(work, broken)


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_deterministic(work, annotated, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    renpy_gen.generate(work, annotated, a)
    renpy_gen.generate(work, annotated, b)
    renpy_gen.generate(work, annotated, b)  # regenerating over an existing tree
    assert tree(a) == tree(b)


@pytest.mark.skipif(not SDK.exists(), reason="Ren'Py SDK not found (set RENPY_SDK)")
def test_renpy_lint(work, annotated, tmp_path):
    out = tmp_path / "mini"
    renpy_gen.generate(work, annotated, out)
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    (tmp_path / "home").mkdir()
    result = subprocess.run(
        [str(SDK), str(out), "lint"], capture_output=True, text=True, env=env, timeout=300
    )
    report = result.stdout + result.stderr
    assert result.returncode == 0, report
    assert "Traceback" not in report and "error" not in report.lower(), report


def _with_cfg(work, **changes):
    data = json.loads(json.dumps(work.data))
    for key, value in changes.items():
        data[key] = value
    return type(work)(root=work.root, data=data)


def test_pinned_cast(work, annotated):
    data = json.loads(json.dumps(work.data))
    data["staging"]["talk"]["cast"] = {"poet": "left", "director": "center"}
    pinned = type(work)(root=work.root, data=data)
    block = scene_block(renpy_gen.render_script(pinned, annotated), "talk")
    first_box = block.index("# talk_0002 .. talk_0005")
    assert "show poet neutral at vn_left" in block[first_box:]
    assert any(ln.startswith("show director") and ln.endswith("vn_center") for ln in block)
    assert not any("vn_right" in ln for ln in block)  # annotation position overridden


def test_stage_direction_keeps_on_stage(work, annotated):
    ann = json.loads(json.dumps(annotated))
    direction = next(ln for ln in ann["scenes"][1]["lines"] if ln["id"] == "talk_0010")
    direction["ann"]["on_stage"] = []
    block = scene_block(renpy_gen.render_script(work, ann), "talk")
    i = block.index('c_narrator "Se aleja."')
    assert not any(ln.startswith("hide ") for ln in block[i - 3 : i])


def test_cue_move(work, annotated):
    data = json.loads(json.dumps(work.data))
    data["cues"][0]["move"] = {"poet": "left"}
    moved = type(work)(root=work.root, data=data)
    block = scene_block(renpy_gen.render_script(moved, annotated), "talk")
    # sprites are hidden by that cue's hide_all, so nothing to slide; position is updated
    assert "with vn_move" not in block
    data["cues"][0].pop("hide_all")
    data["cues"][0]["move"] = {"poet": "left"}
    moved = type(work)(root=work.root, data=data)
    block = scene_block(renpy_gen.render_script(moved, annotated), "talk")
    i = block.index("with vn_move")
    assert block[i - 1].startswith("show poet ") and block[i - 1].endswith("vn_left")


def test_overrides(work, annotated):
    ov = [{"from": "talk_0011", "to": "talk_0013", "emotion": "angry"}]
    block = scene_block(renpy_gen.render_script(_with_cfg(work, overrides=ov), annotated), "talk")
    assert "show poet indignant at vn_right" in block[block.index("# talk_0011 .. talk_0013") :]


@pytest.mark.parametrize(
    "change, match",
    [
        ({"overrides": [{"from": "talk_0011", "to": "nope", "emotion": "angry"}]}, "unknown line"),
        ({"overrides": [{"from": "talk_0011", "to": "talk_0012", "emotion": "mad"}]}, "emotion"),
        ({"overrides": [{"from": "talk_0011", "to": "lyric_0001", "emotion": "angry"}]}, "range"),
        ({"overrides": [{"from": "talk_0012", "to": "talk_0011", "emotion": "angry"}]}, "range"),
    ],
)
def test_invalid_overrides(work, annotated, change, match):
    with pytest.raises(ValueError, match=match):
        renpy_gen.render_script(_with_cfg(work, **change), annotated)


def test_invalid_staging_cast_and_move(work, annotated):
    data = json.loads(json.dumps(work.data))
    data["staging"]["talk"]["cast"] = {"ghost": "left"}
    with pytest.raises(ValueError, match="ghost"):
        renpy_gen.render_script(type(work)(root=work.root, data=data), annotated)
    data = json.loads(json.dumps(work.data))
    data["staging"]["talk"]["cast"] = {"poet": "up"}
    with pytest.raises(ValueError, match="position"):
        renpy_gen.render_script(type(work)(root=work.root, data=data), annotated)
    data = json.loads(json.dumps(work.data))
    data["cues"][0]["move"] = {"poet": "nowhere"}
    with pytest.raises(ValueError, match="position"):
        renpy_gen.render_script(type(work)(root=work.root, data=data), annotated)


def test_every_gui_image_exists(project):
    """Every gui/... path the template screens reference resolves to a shipped file."""
    import re

    game = project / "game"
    text = "".join((game / n).read_text(encoding="utf-8") for n in ("gui.rpy", "screens.rpy"))
    text = "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))
    refs = set(re.findall(r'"(gui/[^"]+\.png)"', text))
    assert len(refs) > 20
    missing = []
    for ref in sorted(refs):
        # Ren'Py's prefix search ends with the un-prefixed name
        candidates = [ref.replace("[prefix_]", "")]
        if ref.endswith("confirm_frame.png"):
            candidates.append("gui/frame.png")  # listed fallback in screens.rpy
        if not any((game / c).is_file() for c in candidates):
            missing.append(ref)
    assert not missing
    # button_properties() backgrounds fall back to gui/button/background.png
    assert (game / "gui/button/background.png").is_file()
    assert (game / "gui/phone/button/background.png").is_file()


def test_with_test_flag(work, annotated, tmp_path):
    plain, tested = tmp_path / "a", tmp_path / "b"
    renpy_gen.generate(work, annotated, plain)
    renpy_gen.generate(work, annotated, tested, with_test=True)
    assert not (plain / "game/e2e_test.rpy").exists()
    assert "testcase default" in (tested / "game/e2e_test.rpy").read_text(encoding="utf-8")
    renpy_gen.generate(work, annotated, tested)  # flag off removes it again
    assert not (tested / "game/e2e_test.rpy").exists()


def run_e2e(project: Path, home: Path) -> str:
    home.mkdir(exist_ok=True)
    for name in ("traceback.txt", "errors.txt"):
        (project / name).unlink(missing_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        "RENPY_RENDERER": "sw",
    }
    result = subprocess.run(
        [str(SDK), str(project), "test"], capture_output=True, text=True, env=env, timeout=900
    )
    report = result.stdout + result.stderr
    for name in ("traceback.txt", "errors.txt"):
        assert not (project / name).exists(), (project / name).read_text(encoding="utf-8")
    assert result.returncode == 0, report
    assert "Traceback" not in report and "FAIL" not in report, report
    return report


@pytest.mark.skipif(not SDK.exists(), reason="Ren'Py SDK not found (set RENPY_SDK)")
def test_e2e_playthrough_mini(work, annotated, tmp_path):
    out = tmp_path / "mini"
    renpy_gen.generate(work, annotated, out, with_test=True)
    run_e2e(out, tmp_path / "home")


def test_cli_defaults_to_current_directory(work, tmp_path, monkeypatch):
    import shutil

    shutil.copytree(FIXTURES / "mini_work", tmp_path / "w")
    (tmp_path / "w/data").mkdir()
    shutil.copy(FIXTURES / "annotated_mini.json", tmp_path / "w/data/annotated.json")
    monkeypatch.chdir(tmp_path / "w")
    assert cli.main(["renpy"]) == 0
    assert (tmp_path / "w/vn/game/script.rpy").is_file()


def _audio():
    files = [
        "audio/sacred/a.ogg",
        "audio/sacred/b.ogg",
        "audio/comic.ogg",
        "audio/scenes/x.ogg",
    ]
    return renpy_gen.Audio(
        {"sacred": files[:2], "comic": [files[2]], "scenes": [files[3]]}, frozenset(files)
    )


def _music_lines(block):
    return [ln for ln in block if "music" in ln]


def test_scan_and_copy_audio(work, tmp_path):
    root = tmp_path / "w"
    (root / "audio/sacred").mkdir(parents=True)
    (root / "audio/scenes").mkdir()
    for rel in ("sacred/b.ogg", "sacred/a.ogg", "scenes/x.ogg", "comic.ogg"):
        (root / "audio" / rel).write_bytes(b"OggS")
    (root / "audio/credits.toml").write_text("", encoding="utf-8")
    out = tmp_path / "out"
    copied = renpy_gen.copy_audio(type(work)(root=root, data=work.data), out)
    assert len(copied) == 4 and not (out / "game/audio/credits.toml").exists()
    audio = renpy_gen.scan_audio(out)
    assert audio.moods["sacred"] == ["audio/sacred/a.ogg", "audio/sacred/b.ogg"]  # sorted
    assert audio.moods["comic"] == ["audio/comic.ogg"]
    assert "audio/scenes/x.ogg" in audio.files


def test_copy_translations_and_forced_language(work, tmp_path):
    root = tmp_path / "w"
    (root / "tl/spanish").mkdir(parents=True)
    work_common = "translate spanish strings:\n"
    (root / "tl/spanish/common.rpy").write_text(work_common, encoding="utf-8")
    (root / "tl/spanish/extra.rpy").write_text(work_common, encoding="utf-8")
    data = {**work.data, "ui": {**work.data.get("ui", {}), "language": "spanish"}}
    out = tmp_path / "out"
    copied = renpy_gen.copy_translations(type(work)(root=root, data=data), out)
    tl = out / "game/tl/spanish"
    # vnpipe's own translation ships; a work file with the same path replaces it
    shipped = renpy_gen.TEMPLATE_DIR / "tl/spanish/screens.rpy"
    assert (tl / "screens.rpy").read_bytes() == shipped.read_bytes()
    assert (tl / "common.rpy").read_text(encoding="utf-8") == work_common
    assert (tl / "extra.rpy").is_file()
    lang = out / "game/language.rpy"
    assert lang in copied
    # config.language beats a persistent preference left by an older build
    assert lang.read_text(encoding="utf-8") == (
        'define config.default_language = "spanish"\ndefine config.language = "spanish"\n'
    )
    # Without [ui].language the stale file is removed.
    renpy_gen.copy_translations(type(work)(root=tmp_path / "none", data=work.data), out)
    assert not lang.exists()


def _tl_strings(path: Path) -> list[str]:
    return re.findall(r'^\s*old "((?:[^"\\]|\\.)*)"$', path.read_text(encoding="utf-8"), re.M)


def test_shipped_spanish_covers_every_template_string():
    """Every _("...") string of the template screens has exactly one Spanish translation."""
    template = ""
    for path in sorted((renpy_gen.TEMPLATE_DIR / "game").glob("*.rpy")):
        template += path.read_text(encoding="utf-8")
    wanted = set(re.findall(r'(?<![\w.])_\("((?:[^"\\]|\\.)*)"\)', template))
    wanted = {s for s in wanted if "@@" not in s}  # work values (title) are already translated
    assert len(wanted) > 70
    olds = []
    for path in sorted((renpy_gen.TEMPLATE_DIR / "tl/spanish").glob("*.rpy")):
        olds += _tl_strings(path)
    assert len(olds) == len(set(olds)), "a string translated twice breaks Ren'Py"
    assert sorted(wanted - set(olds)) == []


def test_no_sync_buttons(project):
    options = (project / "game/options.rpy").read_text(encoding="utf-8")
    assert "define config.has_sync = False" in options
    screens = (project / "game/screens.rpy").read_text(encoding="utf-8")
    assert "UploadSync" not in screens and "DownloadSync" not in screens


def test_menu_ornaments_are_deterministic():
    a, b = renpy_gen.gui_extra_images(), renpy_gen.gui_extra_images()
    assert a.keys() == b.keys()
    assert all(a[k].tobytes() == b[k].tobytes() for k in a)
    for name in ("card_background", "card_hover_background", "rule", "nav_selected_background"):
        assert f"gui/vn/{name}.png" in a
    assert "gui/phone/vn/rule.png" not in a  # ornaments are not looked up per variant
    rule = a["gui/vn/rule.png"]
    mid = rule.height // 2
    # bright in the middle, faded at the ends
    assert rule.getpixel((0, mid))[3] < 10 and rule.getpixel((rule.width // 3, mid))[3] == 255


def test_save_metadata_and_slots(project):
    menus = (project / "game/menus.rpy").read_text(encoding="utf-8")
    assert "config.save_json_callbacks.append(vn_save_json)" in menus
    assert "store.vn_current_scene = label" in menus
    screens = (project / "game/screens.rpy").read_text(encoding="utf-8")
    assert 'info.get("vn_scene")' in screens and 'info["vn_line"]' in screens
    assert "FileDelete(slot)" in screens and "vn_roman(page)" in screens


def test_mood_directory_is_a_playlist(work, annotated):
    block = scene_block(renpy_gen.render_script(work, annotated, _audio()), "lyric")
    assert _music_lines(block) == [
        'play music ["audio/sacred/a.ogg", "audio/sacred/b.ogg"] fadeout 1.0 fadein 1.0 if_changed'
    ]


def test_scene_music_override_and_no_restart(work, annotated):
    # the scene override ignores annotation moods (here a stop on talk_0014)
    w = _with_cfg(
        work, staging={**work.data["staging"], "talk": {"bg": "bg_theatre", "mode": "adv"}}
    )
    w.data["staging"]["talk"]["music"] = "track:scenes/x.ogg"
    block = scene_block(renpy_gen.render_script(w, annotated, _audio()), "talk")
    assert _music_lines(block) == [
        'play music "audio/scenes/x.ogg" fadeout 1.0 fadein 1.0 if_changed'
    ]  # once, not at every box


def test_cue_music_and_auto(work, annotated):
    cues = [{"at": "talk_0004", "music": "comic"}, {"at": "talk_0014", "music": "auto"}]
    w = _with_cfg(work, cues=cues)
    block = scene_block(renpy_gen.render_script(w, annotated, _audio()), "talk")
    music = _music_lines(block)
    # comic from talk_0004 on, until `auto` hands control back to the annotation (stop at 0014)
    assert music == [
        'play music "audio/comic.ogg" fadeout 1.0 fadein 1.0 if_changed',
        "stop music fadeout 1.0",
    ]
    box = next(i for i, ln in enumerate(block) if ln.startswith("#") and "talk_0014" in ln)
    assert block.index(music[0]) < box < block.index(music[1])


def test_music_config_errors(work, annotated):
    w = _with_cfg(work, cues=[{"at": "talk_0004", "music": "track:nope.ogg"}])
    with pytest.raises(ValueError, match="not found"):
        renpy_gen.render_script(w, annotated, _audio())
    w = _with_cfg(work, staging={"talk": {"music": "auto"}})
    with pytest.raises(ValueError, match="only valid in a cue"):
        renpy_gen.render_script(w, annotated, _audio())
    w = _with_cfg(work, staging={"talk": {"music": "loud"}})
    with pytest.raises(ValueError, match="no audio for music mood"):
        renpy_gen.render_script(w, annotated, _audio())


def _project(work, annotated, tmp_path, **cfg):
    out = tmp_path / "p"
    renpy_gen.generate(_with_cfg(work, **cfg) if cfg else work, annotated, out)
    return out


def test_title_card_subtitle(work, annotated, tmp_path):
    scenes = [{"heading": "CONVERSACIÓN", "id": "talk", "subtitle": "Escenario · Día"}]
    out = _project(work, annotated, tmp_path, scenes=scenes)
    images = (out / "game/images.rpy").read_text(encoding="utf-8")
    assert 'image card_talk = VBox(Text("CONVERSACIÓN", size=76' in images
    assert 'Text("Escenario · Día", size=34, italic=True' in images
    assert (
        'image card_lyric = Text("LÍRICA", size=76, kerning=3, text_align=0.5, xmaximum=1600)'
        in images
    )  # no subtitle


def test_scene_labels_and_menus(project, script):
    assert 'label scene_talk:\n    $ vn_mark_scene("scene_talk")\n    window auto' in script
    data = (project / "game/menu_data.rpy").read_text(encoding="utf-8")
    assert 'define vn_scenes = [["scene_lyric", "LÍRICA"], ["scene_talk", "CONVERSACIÓN"]]' in data
    assert 'define vn_subtitle = "Fixture work"' in data
    menus = (project / "game/menus.rpy").read_text(encoding="utf-8")
    assert "Start(scene_label)" in menus and "@@" not in menus
    screens = (project / "game/screens.rpy").read_text(encoding="utf-8")
    assert 'textbutton "Escenas" action ShowMenu("vn_scene_select")' in screens
    assert 'textbutton "Créditos" action ShowMenu("vn_credits")' in screens
    assert "@@" not in screens


def test_repeated_headings_are_told_apart(work, annotated):
    ann = json.loads(json.dumps(annotated))
    ann["scenes"][1]["heading"] = ann["scenes"][0]["heading"]
    scenes = [{"id": "talk", "heading": "x", "subtitle": "Día"}]
    titles = renpy_gen.scene_menu_titles(_with_cfg(work, scenes=scenes), ann)
    assert [t for _, t in titles] == ["LÍRICA (1)", "LÍRICA (Día)"]


def test_credits_from_work_audio_and_manifest(work, tmp_path):
    root = tmp_path / "w"
    (root / "audio").mkdir(parents=True)
    (root / "audio/a.ogg").write_bytes(b"OggS")
    (root / "audio/b.ogg").write_bytes(b"OggS")
    (root / "audio/credits.toml").write_text(
        '[[track]]\nfile = "audio/a.ogg"\nattribution = "Bach [x]. CC BY-SA 4.0."\n'
        '[[track]]\nfile = "audio/b.ogg"\nattribution = "Liszt. Performed by X."\n'
        'attribution_es = "Liszt. Interpretación de X."\n'
        '[[track]]\nfile = "audio/missing.ogg"\nattribution = "not shipped"\n',
        encoding="utf-8",
    )
    data = json.loads(json.dumps(work.data))
    data["source"] = {"gutenberg_id": 42}
    w = type(work)(root=root, data=data)
    manifest = {
        "assets": [
            {"renditions": {"s": {"source": "chatgpt-web", "status": "approved"}}},
            {"renditions": {"s": {"source": "placeholder", "status": "placeholder"}}},
        ]
    }
    sections = dict(renpy_gen.credits_sections(w, manifest))
    assert sections["Texto"][0] == "A. Author"
    assert sections["Texto"][1] == "Traducción: T. Translator"
    assert "Proyecto Gutenberg, n.º 42" in sections["Texto"][2]
    assert sections["Ilustraciones"] == ["Ilustraciones generadas con ChatGPT (OpenAI)"]
    # verbatim (markup-escaped only); a translated `attribution_<language>` wins when present
    assert sections["Música"] == ["Bach [[x]. CC BY-SA 4.0.", "Liszt. Interpretación de X."]


def test_main_menu_background_from_ui(work, annotated, tmp_path):
    out = _project(work, annotated, tmp_path, ui={"main_menu_bg": "bg_theatre"})
    gui = (out / "game/gui.rpy").read_text(encoding="utf-8")
    assert 'gui.main_menu_background = "bg theatre"' in gui


def test_reading_comfort_defaults(project):
    options = (project / "game/options.rpy").read_text(encoding="utf-8")
    assert "default preferences.text_cps = 40" in options
    assert "default preferences.afm_time = 15" in options
    assert "define config.has_music = True" in options


def test_newcomer_never_overlaps(work, annotated):
    ann = json.loads(json.dumps(annotated))
    for line in ann["scenes"][1]["lines"]:  # everyone annotated at the same position
        for entry in line["ann"]["on_stage"]:
            entry["position"] = "left"
    block = scene_block(renpy_gen.render_script(work, ann), "talk")
    slots: dict[str, str] = {}
    for ln in block:
        if ln.startswith("show ") and " at vn_" in ln and not ln.startswith("show layer"):
            slots[ln.split()[1]] = ln.rsplit("vn_", 1)[1]
        elif ln.startswith("hide "):
            slots.pop(ln.split()[1], None)
        assert len(set(slots.values())) == len(slots), ln
    assert len(slots) >= 1
