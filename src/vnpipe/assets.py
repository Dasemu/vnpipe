"""Asset manifest management: import generated images, key out sprite backgrounds,
fall back to placeholders, and build contact sheets for visual review.

The manifest (``<work>/assets/assets.json``, v2) is the single source of truth for every
image. Art *styles* (prompt base + templates) are declared once; each asset has one
*rendition* per style (origin, hash, review status). Image generation itself happens
outside this module (an MCP tool driven by an agent); ``import`` ingests its result.
"""

import argparse
import base64
import hashlib
import io
import json
import shutil
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

# Sprite sanity bounds (checked on the keyed, untrimmed image when the raw file exists).
COVERAGE_MIN = 0.15
COVERAGE_MAX = 0.85
ALPHA_VISIBLE = 16


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(work_root: Path) -> dict[str, Any]:
    data = json.loads((work_root / MANIFEST).read_text(encoding="utf-8"))
    validate(data, "manifest")
    styles = data["styles"]
    if data["default_style"] not in styles:
        raise ValueError(f"default_style {data['default_style']!r} is not in styles")
    for asset in data["assets"]:
        unknown = set(asset["renditions"]) - set(styles)
        if unknown:
            raise ValueError(f"{asset['id']}: renditions for unknown styles {sorted(unknown)}")
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


def resolve_style(manifest: dict[str, Any], style: str | None) -> str:
    """``style`` or the manifest default; raises ``ValueError`` for an unknown style."""
    style = style or manifest["default_style"]
    if style not in manifest["styles"]:
        raise ValueError(f"unknown style {style!r}; known: {sorted(manifest['styles'])}")
    return style


def asset_name(asset: dict[str, Any]) -> str:
    """File stem: the id without its ``bg_`` / ``sprite_`` prefix."""
    return asset["id"].split("_", 1)[1]


def style_file(asset: dict[str, Any], style: str) -> str:
    """Final image path (relative to the assets dir): ``<bg|sprites>/<style>/<name>.webp``."""
    kind_dir = "bg" if asset["kind"] == "background" else "sprites"
    return f"{kind_dir}/{style}/{asset_name(asset)}.webp"


def raw_path_rel(asset: dict[str, Any], style: str) -> str:
    return f"raw/{style}/{asset['id']}.png"


def usable_rendition(asset: dict[str, Any], style: str) -> dict[str, Any] | None:
    """The style's rendition if it is processed/approved, else None."""
    rendition = asset["renditions"].get(style)
    return rendition if rendition and rendition["status"] in USABLE else None


def full_prompt(manifest: dict[str, Any], asset: dict[str, Any], style: str | None = None) -> str:
    """Prompt actually sent to the generator: style base + kind template + asset prompt."""
    cfg = manifest["styles"][resolve_style(manifest, style)]
    kind = "variant" if asset.get("reference") and asset["kind"] == "sprite" else asset["kind"]
    return "\n\n".join(
        p for p in (cfg["base_prompt"], cfg["templates"][kind], asset["prompt"]) if p
    )


# --- migration ----------------------------------------------------------------


def migrate_v1(work_root: Path, style: str, label: str | None = None) -> dict[str, Any]:
    """Convert a v1 manifest (flat base_prompt/templates/per-asset files) to v2 in place.

    The old data becomes the rendition of ``style`` (the default); image files move into the
    per-style directories. Returns the new manifest.
    """
    path = work_root / MANIFEST
    old = json.loads(path.read_text(encoding="utf-8"))
    if "styles" in old:
        raise ValueError("manifest is already v2")
    assets_dir = work_root / "assets"
    new_assets = []
    for a in old["assets"]:
        asset = {k: a[k] for k in ("id", "kind", "character", "expression", "prompt") if k in a}
        asset["reference"] = a.get("reference")
        rendition = {
            k: a[k]
            for k in ("source", "generation_id", "raw_file", "file", "sha256", "status", "notes")
            if k in a
        }
        target = style_file(asset, style)
        raw_target = raw_path_rel(asset, style)
        for key, dest in (("file", target), ("raw_file", raw_target)):
            src = rendition.get(key)
            if not src:
                continue
            if (assets_dir / src).is_file() and src != dest:
                (assets_dir / dest).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(assets_dir / src, assets_dir / dest)
            rendition[key] = dest
        asset["renditions"] = {style: rendition}
        new_assets.append(asset)
    new = {
        "default_style": style,
        "styles": {
            style: {
                "label": label or style,
                "base_prompt": old["base_prompt"],
                "templates": old["templates"],
            }
        },
        "assets": new_assets,
    }
    save_manifest(work_root, new)
    return new


# --- automatic checks -----------------------------------------------------------


def sprite_metrics(img: Image.Image) -> tuple[float, tuple[int, int, int, int] | None]:
    """Opaque-pixel fraction and bbox of the visible area of an RGBA image."""
    alpha = img.getchannel("A").point(lambda a: 255 if a > ALPHA_VISIBLE else 0)
    hist = alpha.histogram()
    return hist[255] / (img.width * img.height), alpha.getbbox()


def check_rendition(
    work_root: Path, manifest: dict[str, Any], asset: dict[str, Any], style: str
) -> list[str]:
    """Automatic sanity checks for one usable rendition; returns failure messages."""
    rendition = usable_rendition(asset, style)
    if not rendition:
        return []
    assets_dir = work_root / "assets"
    final = assets_dir / rendition["file"]
    tag = f"{asset['id']} [{style}]"
    if not final.is_file():
        return [f"{tag}: file {rendition['file']} is missing"]
    failures = []
    if asset["kind"] == "background":
        with Image.open(final) as img:
            if img.size != BG_SIZE:
                failures.append(
                    f"{tag}: background is {img.width}x{img.height}, "
                    f"expected {BG_SIZE[0]}x{BG_SIZE[1]}"
                )
    else:
        raw_rel = rendition.get("raw_file")
        raw = assets_dir / raw_rel if raw_rel else None
        if raw and raw.is_file():  # untrimmed: coverage and top-edge are meaningful
            with Image.open(raw) as img:
                coverage, bbox = sprite_metrics(chroma_key(img))
            if bbox and bbox[1] <= 0:
                failures.append(f"{tag}: sprite touches the top edge (head cropped)")
        else:  # final files are trimmed to the bbox: only coverage applies
            with Image.open(final) as img:
                coverage, _ = sprite_metrics(img.convert("RGBA"))
        if not COVERAGE_MIN <= coverage <= COVERAGE_MAX:
            failures.append(
                f"{tag}: sprite opaque coverage {coverage:.0%} outside "
                f"{COVERAGE_MIN:.0%}-{COVERAGE_MAX:.0%}"
            )
    ref_id = asset.get("reference")
    if ref_id and asset["kind"] == "sprite":
        ref_r = find_asset(manifest, ref_id)["renditions"].get(style)
        ref_file = assets_dir / ref_r["file"] if ref_r and ref_r.get("file") else None
        if ref_file and ref_file.is_file() and sha256_file(final) == sha256_file(ref_file):
            failures.append(f"{tag}: identical to its reference {ref_id} (sha256 match)")
    return failures


def check_style(
    work_root: Path, manifest: dict[str, Any], style: str, ids: list[str] | None = None
) -> list[str]:
    failures: list[str] = []
    for asset in manifest["assets"]:
        if not ids or asset["id"] in ids:
            failures += check_rendition(work_root, manifest, asset, style)
    return failures


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


def import_image(
    work_root: Path, asset_id: str, raw: bytes, generation_id: str | None, style: str | None = None
) -> Path:
    manifest = load_manifest(work_root)
    style = resolve_style(manifest, style)
    asset = find_asset(manifest, asset_id)
    raw_rel = raw_path_rel(asset, style)
    raw_path = work_root / "assets" / raw_rel
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(raw)) as img:
        img.convert("RGB").save(raw_path, "PNG")
    rendition = asset["renditions"].setdefault(style, {})
    rendition.update(
        raw_file=raw_rel,
        file=style_file(asset, style),
        generation_id=generation_id,
        sha256=None,
        status="generated",
    )
    rendition.setdefault("source", "chatgpt-web")
    save_manifest(work_root, manifest)
    return raw_path


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


def process_asset(work_root: Path, asset: dict[str, Any], style: str) -> None:
    rendition = asset["renditions"][style]
    raw_path = work_root / "assets" / rendition["raw_file"]
    final = work_root / "assets" / rendition["file"]
    final.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(raw_path) as img:
        if asset["kind"] == "sprite":
            out = trim_and_scale_sprite(chroma_key(img))
        else:
            out = ImageOps.fit(img.convert("RGB"), BG_SIZE, Image.Resampling.LANCZOS)
        out.save(final, "WEBP", quality=90, method=6)
    rendition["sha256"] = sha256_file(final)
    rendition["status"] = "processed"


def placeholder(
    asset: dict[str, Any], label_color: str = "#d8cfc0", label: bool = True
) -> Image.Image:
    """Deterministic stand-in image so the game always runs.

    ``label`` draws the asset id (for the art pipeline); the game ships it without, since
    the id is an internal name the player should not see."""
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
    if label:
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=40)
        draw.text((30, 30), asset["id"], fill=label_color, font=font)
    return img


def ensure_placeholder(work_root: Path, asset: dict[str, Any], style: str) -> None:
    rendition = asset["renditions"].setdefault(style, {})
    rendition["file"] = style_file(asset, style)
    final = work_root / "assets" / rendition["file"]
    final.parent.mkdir(parents=True, exist_ok=True)
    placeholder(asset).save(final, "WEBP", quality=90)
    rendition.update(source="placeholder", status="placeholder", sha256=sha256_file(final))


def contact_sheet(
    work_root: Path, ids: list[str], out: Path, style: str | None = None, thumb: int = 360
) -> Path:
    """Tile raw or processed images (sprites over a checkerboard) for visual review."""
    manifest = load_manifest(work_root)
    style = resolve_style(manifest, style)
    tiles = []
    for asset_id in ids:
        asset = find_asset(manifest, asset_id)
        rendition = asset["renditions"].get(style) or {}
        rel = (
            rendition.get("file")
            if rendition.get("status") in USABLE
            else rendition.get("raw_file")
        )
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


def _style_of(args: argparse.Namespace, manifest: dict[str, Any]) -> str:
    try:
        return resolve_style(manifest, args.style)
    except ValueError as exc:
        raise SystemExit(f"assets: {exc}") from exc


def _cmd_import(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    style = _style_of(args, load_manifest(work.root))
    result = load_tool_result(args.tool_result)
    raw = decode_output(result, args.index)
    path = import_image(work.root, args.asset_id, raw, result.get("generationId"), style)
    print(f"{args.asset_id} [{style}]: imported {path} ({len(raw)} bytes, sha256 verified)")
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    style = _style_of(args, manifest)
    done = []
    for asset in manifest["assets"]:
        if args.ids and asset["id"] not in args.ids:
            continue
        rendition = asset["renditions"].get(style)
        if rendition and (
            rendition["status"] == "generated" or (args.force and rendition.get("raw_file"))
        ):
            process_asset(work.root, asset, style)
            done.append(asset["id"])
            print(f"{asset['id']} [{style}]: processed")
    save_manifest(work.root, manifest)
    failures = check_style(work.root, manifest, style, done)
    for line in failures:
        print(f"CHECK FAILED {line}")
    return 1 if failures else 0


def _cmd_check(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    styles = [_style_of(args, manifest)] if args.style else list(manifest["styles"])
    failures = []
    for style in styles:
        failures += check_style(work.root, manifest, style, args.ids)
    for line in failures:
        print(f"CHECK FAILED {line}")
    print(f"check: {len(failures)} failure(s) in {', '.join(styles)}")
    return 1 if failures else 0


def _bucket(rendition: dict[str, Any] | None) -> str:
    if rendition is None:
        return "missing"
    return "approved" if rendition["status"] == "approved" else "pending"


def _cmd_status(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    if args.set:
        style = _style_of(args, manifest)
        rendition = find_asset(manifest, args.set[0])["renditions"].get(style)
        if rendition is None:
            raise SystemExit(f"assets: {args.set[0]} has no {style!r} rendition")
        rendition["status"] = args.set[1]
        if len(args.set) > 2:
            rendition["notes"] = args.set[2]
        save_manifest(work.root, manifest)
    styles = [_style_of(args, manifest)] if args.style else list(manifest["styles"])
    print(f"{'asset':<28}" + "".join(f"{s:<14}" for s in styles))
    for asset in manifest["assets"]:
        cells = [(asset["renditions"].get(s) or {}).get("status", "missing") for s in styles]
        print(f"{asset['id']:<28}" + "".join(f"{c:<14}" for c in cells))
    for s in styles:
        counts = {"approved": 0, "pending": 0, "missing": 0}
        for asset in manifest["assets"]:
            counts[_bucket(asset["renditions"].get(s))] += 1
        print(f"{s}: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 0


def _cmd_placeholders(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    style = _style_of(args, manifest)
    for asset in manifest["assets"]:
        if not usable_rendition(asset, style):
            ensure_placeholder(work.root, asset, style)
            print(f"{asset['id']} [{style}]: placeholder")
    save_manifest(work.root, manifest)
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    manifest = load_manifest(work.root)
    print(full_prompt(manifest, find_asset(manifest, args.asset_id), _style_of(args, manifest)))
    return 0


def _cmd_sheet(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    style = _style_of(args, load_manifest(work.root))
    print(contact_sheet(work.root, args.ids, args.out, style))
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    new = migrate_v1(work.root, args.style, args.label)
    print(f"migrated {len(new['assets'])} assets to manifest v2 (style {args.style!r})")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("assets", help="Manage the asset manifest and images")
    cmds = parser.add_subparsers(dest="assets_command", required=True)

    def add(name: str, help_: str, handler, style: bool = True) -> argparse.ArgumentParser:
        p = cmds.add_parser(name, help=help_)
        if style:
            p.add_argument("--style", help="Art style id (default: the manifest's default_style)")
        p.set_defaults(handler=handler)
        return p

    p = add("prompt", "Print the full generation prompt for an asset", _cmd_prompt)
    p.add_argument("asset_id")

    p = add("import", "Decode a stored query_tasks result into an asset", _cmd_import)
    p.add_argument("asset_id")
    p.add_argument("tool_result", type=Path)
    p.add_argument("--index", type=int, default=0)

    p = add("process", "Cut out sprites / fit backgrounds, then run the checks", _cmd_process)
    p.add_argument("ids", nargs="*")
    p.add_argument("--force", action="store_true", help="Reprocess already processed assets")

    p = add("check", "Automatic checks (all styles unless --style)", _cmd_check)
    p.add_argument("ids", nargs="*")

    p = add("status", "Per-style status matrix, or --set ID STATUS [NOTE]", _cmd_status)
    p.add_argument("--set", nargs="+", metavar="ARG")

    add("placeholders", "Create placeholders for unusable assets", _cmd_placeholders)

    p = add("sheet", "Build a contact sheet PNG for review", _cmd_sheet)
    p.add_argument("out", type=Path)
    p.add_argument("ids", nargs="+")

    p = add("migrate", "Convert a v1 manifest to v2 (old data becomes STYLE)", _cmd_migrate, False)
    p.add_argument("style", help="Style id for the existing renditions")
    p.add_argument("--label", help="Display label of that style (default: the id)")
