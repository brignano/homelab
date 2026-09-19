"""Rasterize the dashboard's mark into the forms platforms actually probe.

    python3 scripts/gen-dashboard-icons.py        # needs cairosvg + Pillow

This is the homelab half of the icon standard that `life` sets in
scripts/gen-icons.mjs, and it follows the same two rules:

  THE SVG IS THE REAL MARK, with a transparent ground and a
  prefers-color-scheme step, so it stays legible on a light tab strip and a
  dark one alike. It is icons/favicon.svg, hand-written, and nothing here
  touches it.

  THE RASTERS TAKE AN INK GROUND, because they land where a transparent
  figure does not: Safari's Favorites tiles and an iOS home screen put the
  icon on a surface this file does not control, and a 4px amber bar on
  whatever that surface happens to be is a coin toss. A solid tile is not.

Everything written here is committed. Nothing on the box runs a build, so an
icon that only exists after someone remembers to run a command is an icon that
is missing — the same reason the tile icons are vendored rather than fetched.

Why these particular files, when Homepage's `favicon:` setting only emits two
link tags: Safari's address bar, its suggestion list and its Favorites tiles
probe well-known ROOT paths (/favicon.ico, /apple-touch-icon.png) instead of
parsing the page. Those probes never see Homepage's markup at all. The proxy
rewrites them onto these files — see the dashboard's site block in
docker/proxy/Caddyfile — and config/custom.js declares the rest.

And one more surface that reads no markup either: an INSTALLED app. Chrome
draws the window titlebar, the taskbar entry and the install dialog from the
web app manifest's `icons`, never from `rel="icon"`, so every link tag above is
invisible to it. icons/site.webmanifest is the answer and these are the files
it names.
"""

import io
from pathlib import Path

import cairosvg
from PIL import Image

ICONS = Path(__file__).resolve().parent.parent / "docker" / "dashboard" / "icons"
SOURCE = ICONS / "icon-source.svg"
INK = (17, 17, 17, 255)  # the design system's n-900, light scope


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
