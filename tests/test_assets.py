import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import test_renpy_gen
from PIL import Image

from vnpipe import assets, cli, renpy_gen
from vnpipe.config import load_work
from vnpipe.schema import SchemaError

FIXTURES = Path(__file__).parent / "fixtures"
STYLES = {
    "paint": {
        "label": "Paint",
        "base_prompt": "PAINT base.",
        "templates": {
            "background": "PAINT bg.",
            "sprite": "PAINT sprite.",
            "variant": "PAINT var.",
        },
    },
    "ink": {
        "label": "Ink",
        "base_prompt": "INK base.",
        "templates": {"background": "INK bg.", "sprite": "INK sprite.", "variant": "INK var."},
    },
}


def v2_manifest() -> dict:
    return {
        "default_style": "paint",
        "styles": json.loads(json.dumps(STYLES)),
        "assets": [
            {
                "id": "bg_theatre",
                "kind": "background",
                "prompt": "A stage.",
                "reference": None,
                "renditions": {},
            },
            {
                "id": "sprite_poet_neutral",
                "kind": "sprite",
                "character": "poet",
                "expression": "neutral",
                "prompt": "Neutral.",
                "reference": None,
                "renditions": {},
            },
            {
                "id": "sprite_poet_indignant",
                "kind": "sprite",
                "character": "poet",
                "expression": "indignant",
                "prompt": "Angry.",
                "reference": "sprite_poet_neutral",
                "renditions": {},
            },
        ],
    }


@pytest.fixture
def work_root(tmp_path):
    root = tmp_path / "w"
    shutil.copytree(FIXTURES / "mini_work", root)
    (root / "assets").mkdir()
    assets.save_manifest(root, v2_manifest())
    return root


def green_sprite(path: Path, fill: float = 0.4, top: int = 100) -> None:
    """1000x1500 chroma-green field with an opaque block of roughly ``fill`` of the area."""
    img = Image.new("RGB", (1000, 1500), (0, 177, 64))
    h = round(1500 * fill)
    img.paste((120, 60, 50), (250, top, 750, min(1500, top + h * 2)))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def set_rendition(root, asset_id, style, **fields):
    manifest = assets.load_manifest(root)
    asset = assets.find_asset(manifest, asset_id)
    rendition = asset["renditions"].setdefault(style, {"file": assets.style_file(asset, style)})
    rendition.update(fields)
    assets.save_manifest(root, manifest)


def test_schema_rejects_v1_shape():
    old = {"base_prompt": "x", "templates": {}, "assets": []}
    with pytest.raises(SchemaError):
        assets.validate(old, "manifest")


def test_unknown_style_and_default(work_root):
    manifest = assets.load_manifest(work_root)
    assert assets.resolve_style(manifest, None) == "paint"
    with pytest.raises(ValueError, match="unknown style"):
        assets.resolve_style(manifest, "nope")
    manifest["default_style"] = "nope"
    (work_root / assets.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="default_style"):
        assets.load_manifest(work_root)


def test_full_prompt_per_style(work_root):
    manifest = assets.load_manifest(work_root)
    bg, base, var = manifest["assets"]
    assert assets.full_prompt(manifest, bg) == "PAINT base.\n\nPAINT bg.\n\nA stage."
    assert assets.full_prompt(manifest, bg, "ink").startswith("INK base.\n\nINK bg.")
    assert "INK sprite." in assets.full_prompt(manifest, base, "ink")
    assert "INK var." in assets.full_prompt(manifest, var, "ink")


def test_style_file_layout():
    bg, sprite = v2_manifest()["assets"][:2]
    assert assets.style_file(bg, "ink") == "bg/ink/theatre.webp"
    assert assets.style_file(sprite, "ink") == "sprites/ink/poet_neutral.webp"
    assert assets.raw_path_rel(sprite, "ink") == "raw/ink/sprite_poet_neutral.png"


def test_import_and_process_per_style(work_root):
    green_sprite(work_root / "assets/raw/ink/sprite_poet_neutral.png")
    raw = (work_root / "assets/raw/ink/sprite_poet_neutral.png").read_bytes()
    assets.import_image(work_root, "sprite_poet_neutral", raw, "gid-1", "ink")
    manifest = assets.load_manifest(work_root)
    rendition = manifest["assets"][1]["renditions"]["ink"]
    assert (
        rendition["status"] == "generated" and rendition["file"] == "sprites/ink/poet_neutral.webp"
    )
    assert "paint" not in manifest["assets"][1]["renditions"]
    assert cli.main(["--work", str(work_root), "assets", "process", "--style", "ink"]) == 0
    manifest = assets.load_manifest(work_root)
    rendition = manifest["assets"][1]["renditions"]["ink"]
    assert rendition["status"] == "processed" and rendition["sha256"]
    assert (work_root / "assets/sprites/ink/poet_neutral.webp").is_file()


def test_status_matrix(work_root, capsys):
    set_rendition(work_root, "bg_theatre", "paint", status="approved")
    set_rendition(work_root, "bg_theatre", "ink", status="generated")
    assert cli.main(["--work", str(work_root), "assets", "status"]) == 0
    out = capsys.readouterr().out
    theatre = next(ln for ln in out.splitlines() if ln.startswith("bg_theatre"))
    assert theatre.split() == ["bg_theatre", "approved", "generated"]
    assert "paint: 1 approved, 0 pending, 2 missing" in out
    assert "ink: 0 approved, 1 pending, 2 missing" in out


def test_placeholders_only_for_chosen_style(work_root):
    cli.main(["--work", str(work_root), "assets", "placeholders", "--style", "ink"])
    manifest = assets.load_manifest(work_root)
    assert all(set(a["renditions"]) == {"ink"} for a in manifest["assets"])
    assert (work_root / "assets/bg/ink/theatre.webp").is_file()
    assert not (work_root / "assets/bg/paint").exists()


def test_check_background_size(work_root):
    path = work_root / "assets/bg/paint/theatre.webp"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (1280, 720)).save(path)
    set_rendition(work_root, "bg_theatre", "paint", status="approved")
    manifest = assets.load_manifest(work_root)
    (msg,) = assets.check_style(work_root, manifest, "paint")
    assert "1280x720" in msg
    Image.new("RGB", assets.BG_SIZE).save(path)
    assert assets.check_style(work_root, manifest, "paint") == []


def test_check_sprite_coverage_and_top_edge(work_root):
    raw = work_root / "assets/raw/paint/sprite_poet_neutral.png"
    final = work_root / "assets/sprites/paint/poet_neutral.webp"
    final.parent.mkdir(parents=True)
    Image.new("RGBA", (10, 10)).save(final)
    set_rendition(
        work_root,
        "sprite_poet_neutral",
        "paint",
        status="approved",
        raw_file="raw/paint/sprite_poet_neutral.png",
    )
    manifest = assets.load_manifest(work_root)
    green_sprite(raw, fill=0.4)  # 500x1200 block of 1000x1500 = 40%
    assert assets.check_style(work_root, manifest, "paint") == []
    green_sprite(raw, fill=0.4, top=0)
    (msg,) = assets.check_style(work_root, manifest, "paint")
    assert "top edge" in msg
    green_sprite(raw, fill=0.05)  # 500x150 = 10%
    (msg,) = assets.check_style(work_root, manifest, "paint")
    assert "coverage" in msg


def test_check_variant_identical_to_reference(work_root):
    for name in ("poet_neutral", "poet_indignant"):
        path = work_root / f"assets/sprites/paint/{name}.webp"
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        img.paste((10, 10, 10, 255), (20, 20, 80, 80))
        img.save(path)  # byte-identical files
    for asset_id in ("sprite_poet_neutral", "sprite_poet_indignant"):
        set_rendition(work_root, asset_id, "paint", status="approved")
    manifest = assets.load_manifest(work_root)
    (msg,) = assets.check_style(work_root, manifest, "paint")
    assert "sprite_poet_indignant" in msg and "identical to its reference" in msg
    # same asset under another style is not compared with the paint reference
    assert assets.check_style(work_root, manifest, "ink") == []
    assert cli.main(["--work", str(work_root), "assets", "check"]) == 1


def test_process_runs_checks_and_fails(work_root):
    green_sprite(work_root / "assets/raw/paint/sprite_poet_neutral.png", top=0)
    set_rendition(
        work_root,
        "sprite_poet_neutral",
        "paint",
        status="generated",
        raw_file="raw/paint/sprite_poet_neutral.png",
    )
    assert cli.main(["--work", str(work_root), "assets", "process"]) == 1


def test_migrate_v1(tmp_path):
    root = tmp_path / "w"
    shutil.copytree(FIXTURES / "mini_work", root)
    (root / "assets/bg").mkdir(parents=True)
    (root / "assets/raw").mkdir()
    (root / "assets/bg/theatre.webp").write_bytes(b"x")
    (root / "assets/raw/bg_theatre.png").write_bytes(b"y")
    old = {
        "base_prompt": "B",
        "templates": {"background": "b", "sprite": "s", "variant": "v"},
        "assets": [
            {
                "id": "bg_theatre",
                "kind": "background",
                "prompt": "P",
                "source": "chatgpt-web",
                "generation_id": "g",
                "reference": None,
                "raw_file": "raw/bg_theatre.png",
                "file": "bg/theatre.webp",
                "sha256": "a" * 64,
                "status": "approved",
                "notes": "n",
            }
        ],
    }
    (root / assets.MANIFEST).write_text(json.dumps(old), encoding="utf-8")
    assert cli.main(["--work", str(root), "assets", "migrate", "paint", "--label", "Paint"]) == 0
    manifest = assets.load_manifest(root)
    assert manifest["default_style"] == "paint"
    assert manifest["styles"]["paint"] == {
        "label": "Paint",
        "base_prompt": "B",
        "templates": old["templates"],
    }
    rendition = manifest["assets"][0]["renditions"]["paint"]
    assert (
        rendition["file"] == "bg/paint/theatre.webp"
        and rendition["raw_file"] == "raw/paint/bg_theatre.png"
    )
    assert rendition["status"] == "approved" and rendition["notes"] == "n"
    assert (root / "assets/bg/paint/theatre.webp").read_bytes() == b"x"
    assert (root / "assets/raw/paint/bg_theatre.png").read_bytes() == b"y"
    assert not (root / "assets/bg/theatre.webp").exists()
    with pytest.raises(ValueError, match="already v2"):
        assets.migrate_v1(root, "paint")


# --- Ren'Py generation ------------------------------------------------------------------------


def gen(work_root, tmp_path, with_test=False):
    work = load_work(work_root)
    annotated = json.loads((FIXTURES / "annotated_mini.json").read_text(encoding="utf-8"))
    out = tmp_path / "proj"
    renpy_gen.generate(work, annotated, out, with_test=with_test)
    return out


def approve(root, asset_id, style, size=(100, 100)):
    manifest = assets.load_manifest(root)
    asset = assets.find_asset(manifest, asset_id)
    path = root / "assets" / assets.style_file(asset, style)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (5, 5, 5, 255)).save(path)
    set_rendition(root, asset_id, style, status="approved")


def test_renpy_condition_switch_and_fallback(work_root, tmp_path):
    approve(work_root, "bg_theatre", "paint")
    approve(work_root, "bg_theatre", "ink")
    approve(work_root, "sprite_poet_neutral", "paint")  # only the default style
    approve(work_root, "sprite_poet_indignant", "ink")  # only a non-default style
    out = gen(work_root, tmp_path)
    images = (out / "game/images.rpy").read_text(encoding="utf-8")
    assert (
        "image bg theatre = ConditionSwitch(\"persistent.art_style == 'ink'\", "
        '"images/bg/ink/theatre.webp", "True", "images/bg/paint/theatre.webp")'
    ) in images
    assert 'image poet neutral = "images/sprites/paint/poet_neutral.webp"' in images
    # ink-only rendition: the default style still needs a (placeholder) fallback file
    assert "images/sprites/ink/poet_indignant.webp" in images
    assert (
        'ConditionSwitch("persistent.art_style == \'ink\'", "images/sprites/ink/poet_indignant'
        in images
    )
    assert (out / "game/images/sprites/paint/poet_indignant.webp").is_file()
    assert (out / "game/images/bg/ink/theatre.webp").is_file()
    styles = (out / "game/styles.rpy").read_text(encoding="utf-8")
    assert 'default persistent.art_style = "paint"' in styles
    assert "define vn_art_styles = [('paint', 'Paint'), ('ink', 'Ink')]" in styles
    assert "@@" not in styles
    assert "$ quick_menu = False" in styles  # the picker hides the in-game quick menu
    assert "call vn_pick_art_style" in (out / "game/script.rpy").read_text(encoding="utf-8")
    screens = (out / "game/screens.rpy").read_text(encoding="utf-8")
    assert "Estilo visual" in screens and 'SetField(persistent, "art_style", style_id)' in screens


def test_renpy_no_anime_renditions_is_plain_default(work_root, tmp_path):
    approve(work_root, "bg_theatre", "paint")
    set_rendition(work_root, "bg_theatre", "ink", status="generated")  # not approved: ignored
    out = gen(work_root, tmp_path)
    images = (out / "game/images.rpy").read_text(encoding="utf-8")
    assert 'image bg theatre = "images/bg/paint/theatre.webp"' in images
    assert "ConditionSwitch" not in images
    assert not (out / "game/images/bg/ink").exists()


def test_e2e_picks_last_style(work_root, tmp_path):
    out = gen(work_root, tmp_path, with_test=True)
    text = (out / "game/e2e_test.rpy").read_text(encoding="utf-8")
    assert 'click "Ink"' in text and 'assert screen "vn_art_style_picker"' in text
    assert "@@" not in text


@pytest.mark.skipif(not test_renpy_gen.SDK.exists(), reason="Ren'Py SDK not found (set RENPY_SDK)")
def test_two_style_project_lints_and_plays(work_root, tmp_path):
    """Picker, preferences and ConditionSwitch images in a real Ren'Py run."""
    approve(work_root, "bg_theatre", "paint")
    approve(work_root, "bg_theatre", "ink")
    out = gen(work_root, tmp_path, with_test=True)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    lint = subprocess.run(
        [str(test_renpy_gen.SDK), str(out), "lint"], capture_output=True, text=True, env=env
    )
    report = lint.stdout + lint.stderr
    assert lint.returncode == 0 and "error" not in report.lower(), report
    test_renpy_gen.run_e2e(out, home)


def test_placeholder_label_only_when_asked():
    from PIL import ImageChops

    from vnpipe.assets import placeholder

    asset = {"id": "bg_x", "kind": "background"}
    labelled, plain = placeholder(asset), placeholder(asset, label=False)
    assert ImageChops.difference(labelled, plain).getbbox() is not None
    # the shipped placeholder is the plain gradient: no pixel brighter than its last row
    assert plain.getpixel((0, 0)) != plain.getpixel((0, plain.height - 1))  # a bare gradient


def test_renpy_regenerates_placeholders_and_adds_slot_frames(work_root, tmp_path):
    assets.ensure_placeholder(
        work_root, assets.find_asset(assets.load_manifest(work_root), "bg_theatre"), "paint"
    )
    out = gen(work_root, tmp_path)
    shipped = Image.open(out / "game/images/bg/paint/theatre.webp").convert("RGB")
    assert shipped.getpixel((0, 40)) == shipped.getpixel((300, 40))  # no label on the row
    assert (out / "game/gui/button/slot_idle_background.png").is_file()
    assert (out / "game/gui/button/slot_hover_background.png").is_file()
