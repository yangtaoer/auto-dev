from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = ROOT / "app" / "static" / "brand"
SOURCE = ROOT / "design" / "brand" / "autodev-logo-source.png"
ICON_BACKGROUND = (21, 21, 21, 255)


def extract_transparent_mark() -> Image.Image:
    """Remove the approved source image's flat charcoal plate reproducibly.

    The selected artwork contains dark polygon facets, so a simple exact-color
    replacement is not enough. A short color-distance ramp removes the #151515
    plate while preserving antialiased edges and the darker facets.
    """

    source = Image.open(SOURCE).convert("RGB")
    background = source.getpixel((0, 0))
    rgba = source.convert("RGBA")
    pixels = []
    for red, green, blue, _ in rgba.get_flattened_data():
        distance = max(
            abs(red - background[0]),
            abs(green - background[1]),
            abs(blue - background[2]),
        )
        if distance <= 2:
            pixels.append((0, 0, 0, 0))
            continue
        alpha = 255 if distance >= 26 else round((distance - 2) / 24 * 255)
        coverage = alpha / 255
        foreground = tuple(
            max(0, min(255, round((channel - background[index] * (1 - coverage)) / coverage)))
            for index, channel in enumerate((red, green, blue))
        )
        if max(foreground) < 40 or alpha < 18:
            pixels.append((0, 0, 0, 0))
        else:
            pixels.append((*foreground, alpha))
    rgba.putdata(pixels)
    bounds = rgba.getchannel("A").getbbox()
    if not bounds:
        raise RuntimeError("AutoDev 品牌源图缺少可见内容")
    return rgba.crop(bounds)


def extract_infinity_core(mark: Image.Image) -> Image.Image:
    """Select the largest separated horizontal component: the infinity loop."""

    alpha = mark.getchannel("A")
    visible_columns = []
    for x in range(alpha.width):
        if alpha.crop((x, 0, x + 1, alpha.height)).getbbox():
            visible_columns.append(x)
    if not visible_columns:
        raise RuntimeError("AutoDev 品牌源图无法识别核心标志")

    runs: list[tuple[int, int]] = []
    start = previous = visible_columns[0]
    for column in visible_columns[1:]:
        if column != previous + 1:
            runs.append((start, previous + 1))
            start = column
        previous = column
    runs.append((start, previous + 1))
    left, right = max(runs, key=lambda item: item[1] - item[0])
    core = mark.crop((left, 0, right, mark.height))
    bounds = core.getchannel("A").getbbox()
    if not bounds:
        raise RuntimeError("AutoDev 品牌源图核心标志为空")
    return core.crop(bounds)


def fitted(source: Image.Image, width: int, height: int, padding: int) -> Image.Image:
    available_width = width - padding * 2
    available_height = height - padding * 2
    ratio = min(available_width / source.width, available_height / source.height)
    resized = source.convert("RGBa").resize(
        (max(1, round(source.width * ratio)), max(1, round(source.height * ratio))),
        Image.Resampling.LANCZOS,
    ).convert("RGBA")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas


def icon(mark: Image.Image, size: int, padding: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), ICON_BACKGROUND)
    canvas.alpha_composite(fitted(mark, size, size, padding))
    return canvas


def main() -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    mark = extract_transparent_mark()
    core = extract_infinity_core(mark)

    fitted(mark, 960, 420, 22).save(BRAND_DIR / "autodev-logo.png", optimize=True)
    fitted(mark, 512, 512, 28).save(BRAND_DIR / "autodev-mark.png", optimize=True)
    fitted(mark, 320, 128, 7).save(BRAND_DIR / "autodev-email-mark.png", optimize=True)
    fitted(mark, 160, 64, 3).save(BRAND_DIR / "autodev-mark-64.png", optimize=True)
    fitted(mark, 560, 200, 4).save(BRAND_DIR / "autodev-sidebar-mark.png", optimize=True)

    icon(core, 512, 42).save(BRAND_DIR / "autodev-app-icon.png", optimize=True)
    icon(core, 180, 15).save(BRAND_DIR / "apple-touch-icon.png", optimize=True)
    fitted(core, 512, 512, 18).save(
        BRAND_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
