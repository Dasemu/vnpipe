import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from vnpipe import android
from vnpipe.config import load_work

FIXTURES = Path(__file__).parent / "fixtures"


def make_work(tmp_path, android_cfg):
    root = tmp_path / "w"
    shutil.copytree(FIXTURES / "mini_work", root)
    sprite = Image.new("RGBA", (200, 500), (0, 0, 0, 0))
    sprite.paste((200, 50, 50, 255), (50, 20, 150, 480))
    sprite.save(root / "sprite.png")
    Image.new("RGB", (640, 360), "#123456").save(root / "bg.png")
    work = load_work(root)
    work.data["android"] = android_cfg
    return work


def test_no_android_table_writes_nothing(tmp_path):
    work = make_work(tmp_path, {})
    assert android.write_android(work, tmp_path / "out") == []


def test_package_is_required(tmp_path):
    work = make_work(tmp_path, {"name": "X"})
    with pytest.raises(ValueError, match="package"):
        android.write_android(work, tmp_path / "out")


def test_writes_json_icon_and_presplash(tmp_path):
    cfg = {
        "package": "io.example.work",
        "icon": "sprite.png",
        "icon_background": "#102030",
        "presplash": "bg.png",
    }
    work = make_work(tmp_path, cfg)
    out = tmp_path / "out"
    written = android.write_android(work, out)
    assert sorted(p.name for p in written) == [
        "android-icon_background.png",
        "android-icon_foreground.png",
        "android-presplash.jpg",
        "android.json",
    ]
    data = json.loads((out / "android.json").read_text())
    assert data["package"] == "io.example.work"
    assert data["name"] == work.data["work"]["title"]
    assert data["orientation"] == "sensorLandscape"
    fg = Image.open(out / "android-icon_foreground.png")
    assert fg.size == (432, 432) and fg.getpixel((0, 0))[3] == 0  # transparent margin
    assert fg.getpixel((216, 216))[3] == 255  # subject in the middle
    assert Image.open(out / "android-icon_background.png").getpixel((5, 5)) == (16, 32, 48)
    assert Image.open(out / "android-presplash.jpg").size == (1280, 720)
    again = android.write_android(work, out)  # idempotent
    assert [p.read_bytes() for p in again] == [p.read_bytes() for p in written]
