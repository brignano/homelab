"""Rasterize the review site's mark into the forms platforms probe.

    python3 scripts/gen-review-icons.py        # needs cairosvg + Pillow

The same two rules as scripts/gen-dashboard-icons.py, which explains them: the
SVG (docker/clipreview/app/static/favicon.svg) is the real mark and has no
ground; the rasters take the design system's ink ground. Everything written is
committed, because nothing on the box runs a build.
"""

import io
from pathlib import Path

import cairosvg
from PIL import Image

ICONS = Path(__file__).resolve().parent.parent / "docker" / "clipreview" / "app" / "static"
SOURCE = ICONS / "icon-source.svg"
INK = (17, 17, 20, 255)  # the design system's n-900, light value (#111114)


def tile(size: int, pad: int = 0) -> Image.Image:
    """The mark at `size`, inset by `pad`, composited onto the ink ground."""
    inner = size - pad * 2
    mark = Image.open(
        io.BytesIO(
            cairosvg.svg2png(url=str(SOURCE), output_width=inner, output_height=inner)
        )
    ).convert("RGBA")
    ground = Image.new("RGBA", (size, size), INK)
    ground.alpha_composite(mark, (pad, pad))
    return ground


def main() -> None:
    # 180 is what iOS asks for; the 32 is the form with the fewest ways to go
    # wrong for a browser that declines the SVG — a plain PNG, no ICO container
    # to parse and no inline <style> to evaluate.
    #
    # 192 and 512 are the manifest's, and they are not negotiable rather than
    # conventional: Chrome refuses to treat a site as installable without at
    # least a 192, and uses the 512 for the splash and the larger shell chrome.
    for name, size in (
        ("apple-touch-icon.png", 180),
        ("favicon-32.png", 32),
        ("icon-192.png", 192),
        ("icon-512.png", 512),
    ):
        tile(size).convert("RGB").save(ICONS / name)
        print("wrote", name)

    # The one maskable copy, and the only caller `pad` has ever had.
    #
    # An Android adaptive icon is cropped to a shape the page does not get to
    # choose — a circle on one launcher, a squircle on the next — and only the
    # middle 80% is guaranteed to survive. This mark is near full-bleed by
    # design (it has to hold at 16px), so declaring the same file `maskable`
    # would hand the launcher a drawing whose top and bottom bars are exactly
    # what it crops. Inset by 10% a side, the figure lands inside the safe zone
    # and the ink ground takes the cropping.
    tile(512, pad=round(512 * 0.1)).convert("RGB").save(ICONS / "icon-maskable-512.png")
    print("wrote icon-maskable-512.png")

    # Multi-size .ico for the root probe. PNG-in-ICO is understood by every
    # browser that still matters.
    #
    # Each frame is rendered from the vector at its own size and handed over
    # with `append_images`. Passing `sizes=` alone looks equivalent and is not:
    # Pillow then downsamples the single image it was given, so the 16px frame
    # — the one that actually gets drawn in a tab — would be a resampled 48,
    # with the rounded corners smeared. An earlier version of this file
    # rendered all three and then used only the first, which is that bug with
    # the evidence of the intent still in it.
    frames = [tile(s).convert("RGB") for s in (48, 32, 16)]
    frames[0].save(
        ICONS / "favicon.ico",
        sizes=[(48, 48), (32, 32), (16, 16)],
        append_images=frames[1:],
    )
    print("wrote favicon.ico")


if __name__ == "__main__":
    main()
