"""Asset manifest management: import generated images, key out sprite backgrounds,
fall back to placeholders, and build contact sheets for visual review.

The manifest (``<work>/assets/assets.json``) is the single source of truth for every
image: prompt, origin, hash and review status. Image generation itself happens
outside this module (an MCP tool driven by an agent); ``import`` ingests its result.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from vnpipe.config import load_work
from vnpipe.schema import validate

MANIFEST = "assets/assets.json"
BG_SIZE = (1920, 1080)
SPRITE_HEIGHT = 1000
USABLE = {"processed", "approved"}

# Chroma key thresholds on "greenness" = g - max(r, b).
KEY_FULL = 60  # at or above: fully transparent
KEY_NONE = 20  # at or below: fully opaque


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(work_root: Path) -> dict[str, Any]:
    data = json.loads((work_root / MANIFEST).read_text(encoding="utf-8"))
    validate(data, "manifest")
    return data


def save_manifest(work_root: Path, data: dict[str, Any]) -> None:
    validate(data, "manifest")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    (work_root / MANIFEST).write_text(text, encoding="utf-8")


def find_asset(manifest: dict[str, Any], asset_id: str) -> dict[str, Any]:
    for asset in manifest["assets"]:
        if asset["id"] == asset_id:
            return asset
    raise KeyError(f"unknown asset id: {asset_id}")


def full_prompt(manifest: dict[str, Any], asset: dict[str, Any]) -> str:
    """Prompt actually sent to the generator: base + kind template + asset prompt."""
    kind = "variant" if asset.get("reference") and asset["kind"] == "sprite" else asset["kind"]
    template = manifest["templates"][kind]
    return "\n\n".join(p for p in (manifest["base_prompt"], template, asset["prompt"]) if p)


# --- import -----------------------------------------------------------------


def load_tool_result(path: Path) -> dict[str, Any]:
    """Read a stored query_tasks result (raw object or [{"type": "text", "text": ...}])."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list) and data and isinstance(data[0], dict) and "text" in data[0]:
        data = json.loads(data[0]["text"])
    if isinstance(data, str):
        data = json.loads(data)
    return data


def decode_output(result: dict[str, Any], index: int = 0) -> bytes:
    if result.get("status") != "COMPLETED":
        raise ValueError(f"generation not completed: status={result.get('status')}")
    outputs = [o for o in result.get("outputs", []) if o.get("base64")]
    if len(outputs) <= index:
        raise ValueError("no base64 output; query again with include_base64=true")
    out = outputs[index]
    raw = base64.b64decode(out["base64"])
    expected = out.get("sha256")
    if expected and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("sha256 mismatch between decoded image and the MCP report")
    return raw


def import_image(work_root: Path, asset_id: str, raw: bytes, generation_id: str | None) -> Path:
    manifest = load_manifest(work_root)
    asset = find_asset(manifest, asset_id)
    raw_rel = f"raw/{asset_id}.png"
    raw_path = work_root / "assets" / raw_rel
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(_bytes_io(raw)) as img:
        img.convert("RGB").save(raw_path, "PNG")
    asset.update(raw_file=raw_rel, generation_id=generation_id, status="generated")
    asset.setdefault("source", "chatgpt-web")
    save_manifest(work_root, manifest)
    return raw_path


def _bytes_io(raw: bytes):
    import io

    return io.BytesIO(raw)


# --- processing -------------------------------------------------------------


def chroma_key(img: Image.Image) -> Image.Image:
    """Make the flat green field transparent with a soft edge and green despill."""
    rgb = img.convert("RGB")
    pixels = []
    for r, g, b in rgb.getdata():
        greenness = g - max(r, b)
        if greenness >= KEY_FULL:
            pixels.append((0, 0, 0, 0))
            continue
        if greenness <= KEY_NONE:
            alpha = 255
        else:
            alpha = round(255 * (KEY_FULL - greenness) / (KEY_FULL - KEY_NONE))
        if greenness > 0:
            g = max(r, b)  # despill: clamp green to the other channels
        pixels.append((r, g, b, alpha))
    out = Image.new("RGBA", rgb.size)
    out.putdata(pixels)
    return out


def trim_and_scale_sprite(img: Image.Image, height: int = SPRITE_HEIGHT) -> Image.Image:
    bbox = img.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    scale = height / img.height
    return img.resize((max(1, round(img.width * scale)), height), Image.Resampling.LANCZOS)


def process_asset(work_root: Path, asset: dict[str, Any]) -> None:
    raw_path = work_root / "assets" / asset["raw_file"]
    final = work_root / "assets" / asset["file"]
    final.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(raw_path) as img:
        if asset["kind"] == "sprite":
            out = trim_and_scale_sprite(chroma_key(img))
        else:
            out = ImageOps.fit(img.convert("RGB"), BG_SIZE, Image.Resampling.LANCZOS)
        out.save(final, "WEBP", quality=90, method=6)
    asset["sha256"] = sha256_file(final)
    asset["status"] = "processed"


def placeholder(asset: dict[str, Any], label_color: str = "#d8cfc0") -> Image.Image:
    """Deterministic stand-in image so the game always runs."""
    if asset["kind"] == "background":
        img = Image.new("RGB", BG_SIZE, "#1d1a17")
        draw = ImageDraw.Draw(img)
        for y in range(BG_SIZE[1]):
            shade = 23 + round(30 * y / BG_SIZE[1])
            draw.line([(0, y), (BG_SIZE[0], y)], fill=(shade, shade - 4, shade - 8))
    else:
        img = Image.new("RGBA", (500, SPRITE_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([170, 40, 330, 220], fill=(60, 52, 46, 230))
        draw.rounded_rectangle([110, 230, 390, SPRITE_HEIGHT], 60, fill=(60, 52, 46, 230))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    draw.text((30, 30), asset["id"], fill=label_color, font=font)
    return img


def ensure_placeholder(work_root: Path, asset: dict[str, Any]) -> None:
    final = work_root / "assets" / asset["file"]
    final.parent.mkdir(parents=True, exist_ok=True)
    placeholder(asset).save(final, "WEBP", quality=90)
    asset.update(source="placeholder", status="placeholder", sha256=sha256_file(final))


def contact_sheet(work_root: Path, ids: list[str], out: Path, thumb: int = 360) -> Path:
    """Tile raw or processed images (sprites over a checkerboard) for visual review."""
    manifest = load_manifest(work_root)
    tiles = []
    for asset_id in ids:
        asset = find_asset(manifest, asset_id)
        rel = asset["file"] if asset["status"] in USABLE else asset.get("raw_file")
        if not rel:
            continue
        with Image.open(work_root / "assets" / rel) as img:
            img = img.convert("RGBA")
            img.thumbnail((thumb, thumb))
            tile = _checker((thumb, thumb))
            tile.alpha_composite(img, ((thumb - img.width) // 2, (thumb - img.height) // 2))
            ImageDraw.Draw(tile).text((6, 6), asset_id, fill="white")
            tiles.append(tile)
    cols = min(4, max(1, len(tiles)))
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * thumb, max(1, rows) * thumb), "black")
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % cols) * thumb, (i // cols) * thumb))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out)
    return out


def _checker(size: tuple[int, int], cell: int = 20) -> Image.Image:
    img = Image.new("RGBA", size, (90, 90, 90, 255))
    draw = ImageDraw.Draw(img)
    for y in range(0, size[1], cell):
        for x in range((y // cell % 2) * cell, size[0], cell * 2):
            draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(130, 130, 130, 255))
    return img


# --- CLI --------------------------------------------------------------------


def _cmd_import(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    result = load_tool_result(args.tool_result)
    raw = decode_output(result, args.index)
    path = import_image(work.root, args.asset_id, raw, result.get("generationId"))
    print(f"{args.asset_id}: imported {path} ({len(raw)} bytes, sha256 verified)")
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    for asset in manifest["assets"]:
        if args.ids and asset["id"] not in args.ids:
            continue
        if asset["status"] == "generated" or (args.force and asset.get("raw_file")):
            process_asset(work.root, asset)
            print(f"{asset['id']}: processed")
    save_manifest(work.root, manifest)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    if args.set:
        asset = find_asset(manifest, args.set[0])
        asset["status"] = args.set[1]
        if len(args.set) > 2:
            asset["notes"] = args.set[2]
        save_manifest(work.root, manifest)
    for asset in manifest["assets"]:
        print(f"{asset['status']:<11} {asset['id']}")
    return 0


def _cmd_placeholders(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    for asset in manifest["assets"]:
        if asset["status"] not in USABLE:
            ensure_placeholder(work.root, asset)
            print(f"{asset['id']}: placeholder")
    save_manifest(work.root, manifest)
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    print(full_prompt(manifest, find_asset(manifest, args.asset_id)))
    return 0


def _cmd_sheet(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    print(contact_sheet(work.root, args.ids, args.out))
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("assets", help="Manage the asset manifest and images")
    cmds = parser.add_subparsers(dest="assets_command", required=True)

    p = cmds.add_parser("prompt", help="Print the full generation prompt for an asset")
    p.add_argument("asset_id")
    p.set_defaults(handler=_cmd_prompt)

    p = cmds.add_parser("import", help="Decode a stored query_tasks result into an asset")
    p.add_argument("asset_id")
    p.add_argument("tool_result", type=Path)
    p.add_argument("--index", type=int, default=0)
    p.set_defaults(handler=_cmd_import)

    p = cmds.add_parser("process", help="Cut out sprites / fit backgrounds")
    p.add_argument("ids", nargs="*")
    p.add_argument("--force", action="store_true", help="Reprocess already processed assets")
    p.set_defaults(handler=_cmd_process)

    p = cmds.add_parser("status", help="List statuses, or --set ID STATUS [NOTE]")
    p.add_argument("--set", nargs="+", metavar="ARG")
    p.set_defaults(handler=_cmd_status)

    p = cmds.add_parser("placeholders", help="Create placeholders for unusable assets")
    p.set_defaults(handler=_cmd_placeholders)

    p = cmds.add_parser("sheet", help="Build a contact sheet PNG for review")
    p.add_argument("out", type=Path)
    p.add_argument("ids", nargs="+")
    p.set_defaults(handler=_cmd_sheet)
