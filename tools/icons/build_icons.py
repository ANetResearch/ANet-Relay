"""Build ANet Relay application icons from the full-bleed source artwork.

Source: brand/icon-1024.png, a full-bleed rounded square (dark body, thin lighter
bezel, artwork in the middle).

Outputs:
  brand/icon-macos-1024.png   master on the Apple macOS grid (824 px shape on a
                              transparent 1024 canvas)
  brand/icon.icns             16..1024 including @2x variants
  brand/icon.ico              16..256
  brand/icon.png              512, macOS grid (Linux, About panel, dev dock)
  web/public/brand/favicon-32.png        rounded shape, cropped to its bounds
  web/public/brand/apple-touch-icon.png  180, opaque full-bleed square (iOS masks it)

The shape is rebuilt instead of shrinking the source as is: the source's own
corner and its 1 px darker outer edge would otherwise show up as a fringe. The
body gradient and bezel colours are fitted from the source, the artwork interior
is kept verbatim, the outline is an analytic rounded rectangle with smoothed
(superellipse) corners rendered at 4x and reduced. Pixels outside the shape carry
the bezel colour at zero alpha, so consumers that filter without premultiplying
alpha cannot pull dark colour into the edge.

Usage: .venv/bin/python tools/icons/build_icons.py [--preview /tmp/icon-preview.png]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "brand" / "icon-1024.png"
BRAND = ROOT / "brand"
WEB_BRAND = ROOT / "web" / "public" / "brand"

CANVAS = 1024
SHAPE = 824  # Apple macOS icon grid: 824 x 824 shape centred on a 1024 canvas.
CORNER_R = 250.0  # Extent of the smoothed corner, in 1024-canvas pixels.
CORNER_N = 3.0  # Superellipse exponent; apex sits about where a 185 px arc would.
BEZEL = 7.0  # Bezel width in 1024-canvas pixels (source: 9 px at full bleed).
SS = 4  # Supersampling factor for edges.

# Source geometry (full bleed): bezel occupies the outer ~10 px; artwork is kept
# from this depth inwards.
SRC_KEEP_DEPTH = 14
SRC_FEATHER = 3


# ---------------------------------------------------------------------------
# Colour models fitted from the source


def _fit_plane(samples: list[tuple[float, float, tuple[int, ...]]]) -> list[tuple[float, float, float]]:
    """Least-squares fit c = a + b*x + c*y per RGB channel (x, y normalised 0..1)."""
    n = len(samples)
    sx = sum(s[0] for s in samples)
    sy = sum(s[1] for s in samples)
    sxx = sum(s[0] * s[0] for s in samples)
    syy = sum(s[1] * s[1] for s in samples)
    sxy = sum(s[0] * s[1] for s in samples)
    m = [[n, sx, sy], [sx, sxx, sxy], [sy, sxy, syy]]
    planes = []
    for ch in range(3):
        sc = sum(s[2][ch] for s in samples)
        sxc = sum(s[0] * s[2][ch] for s in samples)
        syc = sum(s[1] * s[2][ch] for s in samples)
        planes.append(_solve3(m, [sc, sxc, syc]))
    return planes


def _solve3(m: list[list[float]], v: list[float]) -> tuple[float, float, float]:
    def det(a: list[list[float]]) -> float:
        return (
            a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
            - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
        )

    d = det(m)
    out = []
    for i in range(3):
        mi = [row[:] for row in m]
        for r in range(3):
            mi[r][i] = v[r]
        out.append(det(mi) / d)
    return out[0], out[1], out[2]


def _edge_band_samples(src: Image.Image, depths: range) -> list[tuple[float, float, tuple[int, ...]]]:
    """Sample the straight parts of all four edges at the given depths."""
    px = src.load()
    w, h = src.size
    out = []
    for t in range(260, w - 260, 6):
        for d in depths:
            for x, y in ((t, d), (t, h - 1 - d), (d, t), (w - 1 - d, t)):
                out.append((x / (w - 1), y / (h - 1), px[x, y][:3]))
    return out


def _plane_image(planes: list[tuple[float, float, float]], size: int) -> Image.Image:
    """Render a linear colour field; bilinear upscaling reproduces a plane exactly."""
    grid = 33
    small = Image.new("RGB", (grid, grid))
    sp = small.load()
    for j in range(grid):
        for i in range(grid):
            x = i / (grid - 1)
            y = j / (grid - 1)
            sp[i, j] = tuple(
                max(0, min(255, round(a + b * x + c * y))) for (a, b, c) in planes
            )
    return small.resize((size, size), Image.Resampling.BILINEAR)


# ---------------------------------------------------------------------------
# Shape


def _shape_polygon(x0: float, y0: float, size: float, r: float, n: float) -> list[tuple[float, float]]:
    """Rounded rectangle whose corners are superellipse quadrants (continuous curvature)."""
    pts: list[tuple[float, float]] = []
    steps = 256
    corners = (
        (x0 + size - r, y0 + r, 1, -1, -90.0),  # top-right
        (x0 + size - r, y0 + size - r, 1, 1, 0.0),  # bottom-right
        (x0 + r, y0 + size - r, -1, 1, 90.0),  # bottom-left
        (x0 + r, y0 + r, -1, -1, 180.0),  # top-left
    )
    for cx, cy, sx, sy, start in corners:
        for k in range(steps + 1):
            a = math.radians(start + 90.0 * k / steps)
            ca, sa = math.cos(a), math.sin(a)
            px = abs(ca) ** (2.0 / n) * r
            py = abs(sa) ** (2.0 / n) * r
            pts.append((cx + sx * px, cy + sy * py))
    return pts


def _shape_mask(size_px: int, inset: float) -> Image.Image:
    """Supersampled mask of the icon shape (optionally inset), reduced to size_px."""
    big = size_px * SS
    scale = big / CANVAS
    off = (CANVAS - SHAPE) / 2 + inset
    side = SHAPE - 2 * inset
    r = max(1.0, CORNER_R - inset)
    # ImageDraw fills columns int(left)..int(right) inclusive. Pull every vertex
    # towards the centre by up to half a pixel so an edge at X covers exactly the
    # pixels below X on the far sides and from X on the near sides.
    def snap(v: float) -> float:
        return v + 0.5 - v / big

    poly = [(snap(x * scale), snap(y * scale)) for x, y in _shape_polygon(off, off, side, r, CORNER_N)]
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    return mask.resize((size_px, size_px), Image.Resampling.BOX)


# ---------------------------------------------------------------------------
# Composition


def build_master(src: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Return (opaque colour layer, alpha mask) at 1024 on the macOS grid."""
    body_planes = _fit_plane(_edge_band_samples(src, range(18, 44, 3)))
    bezel_planes = _fit_plane(_edge_band_samples(src, range(3, 8)))

    # Artwork: the source wherever it is deeper than SRC_KEEP_DEPTH from its own
    # edge, laid over the fitted body gradient so the seam is invisible.
    alpha = src.getchannel("A").point(lambda v: 255 if v >= 250 else 0)
    keep = alpha.filter(ImageFilter.MinFilter(2 * SRC_KEEP_DEPTH + 1))
    keep = keep.filter(ImageFilter.GaussianBlur(SRC_FEATHER))
    full_bleed = _plane_image(body_planes, CANVAS)
    full_bleed.paste(src.convert("RGB"), (0, 0), keep)

    # Place the artwork inside the shape (shape box minus bezel maps to the
    # source box minus its bezel, so proportions match the source).
    inner_src = 10  # source bezel width
    inner_dst = SHAPE - 2 * BEZEL
    scale = inner_dst / (CANVAS - 2 * inner_src)
    scaled_side = round(CANVAS * scale)
    art = full_bleed.resize((scaled_side, scaled_side), Image.Resampling.LANCZOS)
    offset = round((CANVAS - scaled_side) / 2)

    # Colour fields in output coordinates: the shape box spans the full source.
    box0 = (CANVAS - SHAPE) / 2
    def remap(planes: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
        # source u = (x - box0) / SHAPE, x normalised to the canvas: u = (X*1023 - box0) / SHAPE
        k = (CANVAS - 1) / SHAPE
        c0 = -box0 / SHAPE
        return [(a + b * c0 + c * c0, b * k, c * k) for (a, b, c) in planes]

    colour = _plane_image(remap(bezel_planes), CANVAS)
    body_field = _plane_image(remap(body_planes), CANVAS)
    inner = _shape_mask(CANVAS, BEZEL)
    colour.paste(body_field, (0, 0), inner)
    art_layer = colour.copy()
    art_layer.paste(art, (offset, offset))
    colour.paste(art_layer, (0, 0), inner)

    mask = _shape_mask(CANVAS, 0.0)
    return colour, mask


def _resize(colour: Image.Image, mask: Image.Image, size: int) -> Image.Image:
    """Resize colour and alpha separately (colour is opaque and bled past the edge)."""
    if size == colour.width:
        rgb, a = colour, mask
    else:
        rgb = colour.resize((size, size), Image.Resampling.LANCZOS)
        a = mask.resize((size, size), Image.Resampling.LANCZOS)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


# OSType -> pixel size. Pillow's writer omits the 16 and 32 px @1x entries, so the
# container is written here; all entries are PNG (supported since macOS 10.7).
ICNS_TYPES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 32),  # 16@2x
    (b"ic12", 64),  # 32@2x
    (b"ic07", 128),
    (b"ic13", 256),  # 128@2x
    (b"ic08", 256),
    (b"ic14", 512),  # 256@2x
    (b"ic09", 512),
    (b"ic10", 1024),  # 512@2x
)


def _write_icns(path: Path, frames: dict[int, Image.Image]) -> None:
    import io
    import struct

    entries = []
    for ostype, size in ICNS_TYPES:
        buf = io.BytesIO()
        frames[size].save(buf, format="PNG", optimize=True)
        entries.append((ostype, buf.getvalue()))
    toc = b"TOC " + struct.pack(">I", 8 + 8 * len(entries))
    toc += b"".join(t + struct.pack(">I", 8 + len(d)) for t, d in entries)
    body = toc + b"".join(t + struct.pack(">I", 8 + len(d)) + d for t, d in entries)
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def build(preview: Path | None) -> None:
    src = Image.open(SOURCE).convert("RGBA")
    if src.size != (CANVAS, CANVAS):
        raise SystemExit(f"{SOURCE} must be {CANVAS}x{CANVAS}, got {src.size}")
    colour, mask = build_master(src)

    master = _resize(colour, mask, CANVAS)
    master.save(BRAND / "icon-macos-1024.png", optimize=True)
    _resize(colour, mask, 512).save(BRAND / "icon.png", optimize=True)

    _write_icns(BRAND / "icon.icns", {s: _resize(colour, mask, s) for s in (16, 32, 64, 128, 256, 512, 1024)})

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_frames = [_resize(colour, mask, s) for s in ico_sizes]
    ico_frames[-1].save(
        BRAND / "icon.ico",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_frames[:-1],
    )

    WEB_BRAND.mkdir(parents=True, exist_ok=True)
    box = round((CANVAS - SHAPE) / 2)
    crop = (box, box, CANVAS - box, CANVAS - box)
    tight_colour, tight_mask = colour.crop(crop), mask.crop(crop)
    _resize(tight_colour, tight_mask, 32).save(WEB_BRAND / "favicon-32.png", optimize=True)

    # iOS applies its own mask and turns transparency black: ship an opaque square.
    tight_colour.resize((180, 180), Image.Resampling.LANCZOS).save(
        WEB_BRAND / "apple-touch-icon.png", optimize=True
    )

    if preview is not None:
        _write_preview(master, preview)

    for p in (
        BRAND / "icon-macos-1024.png",
        BRAND / "icon.png",
        BRAND / "icon.icns",
        BRAND / "icon.ico",
        WEB_BRAND / "favicon-32.png",
        WEB_BRAND / "apple-touch-icon.png",
    ):
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")


def _write_preview(master: Image.Image, path: Path) -> None:
    """Icon on white, mid grey, dark and black, plus 6x zooms of the top-left corner
    and the bottom edge on white and black, to check for fringes."""
    tile = 300
    backgrounds = [(255, 255, 255), (128, 128, 128), (30, 30, 30), (0, 0, 0)]
    sheet = Image.new("RGB", (tile * 4, tile * 2), (90, 90, 90))
    small = master.resize((tile, tile), Image.Resampling.LANCZOS)
    for i, bg in enumerate(backgrounds):
        cell = Image.new("RGBA", (tile, tile), bg + (255,))
        cell.alpha_composite(small)
        sheet.paste(cell.convert("RGB"), (i * tile, 0))
    zooms = [((125, 125, 175, 175), (255, 255, 255)), ((125, 125, 175, 175), (0, 0, 0)),
             ((490, 895, 515, 930), (255, 255, 255)), ((490, 895, 515, 930), (0, 0, 0))]
    for i, (region, bg) in enumerate(zooms):
        part = master.crop(region)
        cell = Image.new("RGBA", part.size, bg + (255,))
        cell.alpha_composite(part)
        sheet.paste(cell.convert("RGB").resize((tile, tile), Image.Resampling.NEAREST), (i * tile, tile))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    print(f"wrote preview {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", type=Path, help="write a fringe-check contact sheet here")
    args = parser.parse_args()
    build(args.preview)


if __name__ == "__main__":
    main()
