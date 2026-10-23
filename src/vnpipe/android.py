"""Android packaging inputs for RAPT (Ren'Py's Android tool): ``android.json``, the adaptive
icon and the presplash. Written into the Ren'Py project only when the work has an
``[android]`` table; building the APK itself is a launcher/RAPT step (``docs/running.md``)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from vnpipe.config import Work

ICON_SIZE = 432  # adaptive icon layer, in px; the safe zone is the central 2/3
ICON_ART = 288
PRESPLASH_SIZE = (1280, 720)


def android_config(work: Work) -> dict | None:
    cfg = work.data.get("android")
    if not cfg:
        return None
    if not cfg.get("package"):
        raise ValueError("[android].package is required (e.g. 'io.github.you.work')")
    return cfg


def render_android_json(work: Work, cfg: dict) -> str:
    data = {
        "package": cfg["package"],
        "name": cfg.get("name", work.data["work"]["title"]),
        "icon_name": cfg.get("name", work.data["work"]["title"]),
        "orientation": cfg.get("orientation", "sensorLandscape"),
        "permissions": cfg.get("permissions", []),
        "store": "none",
        "update_icons": True,
        "update_always": True,
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def foreground(source: Path) -> Image.Image:
    """The subject of ``source`` (its top square: head and shoulders for a sprite), centred
    in the transparent adaptive-icon layer."""
    img = Image.open(source).convert("RGBA")
    box = img.getchannel("A").getbbox() or (0, 0, *img.size)
    left, top, right, _ = box
    side = min(right - left, img.height - top)
    square = img.crop((left, top, left + side, top + side)).resize((ICON_ART, ICON_ART))
    layer = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    offset = (ICON_SIZE - ICON_ART) // 2
    layer.alpha_composite(square, (offset, offset))
    return layer


def write_android(work: Work, out: Path) -> list[Path]:
    """Write the RAPT inputs into the Ren'Py project ``out``; returns the files written."""
    cfg = android_config(work)
    if cfg is None:
        return []
    written = []

    def save(name: str, content: str | Image.Image, **kwargs) -> None:
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            dest.write_text(content, encoding="utf-8", newline="\n")
        else:
            content.save(dest, **kwargs)
        written.append(dest)

    save("android.json", render_android_json(work, cfg))
    if cfg.get("icon"):
        save("android-icon_foreground.png", foreground(work.path(cfg["icon"])), format="PNG")
        background = Image.new("RGB", (ICON_SIZE, ICON_SIZE), cfg.get("icon_background", "#000000"))
        save("android-icon_background.png", background, format="PNG")
    if cfg.get("presplash"):
        splash = Image.open(work.path(cfg["presplash"])).convert("RGB").resize(PRESPLASH_SIZE)
        save("android-presplash.jpg", splash, format="JPEG", quality=88)
    return written
