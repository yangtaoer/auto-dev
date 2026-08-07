from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = ROOT / "app" / "static" / "brand"
SOURCE = ROOT / "design" / "brand" / "autodev-logo-source.png"
ICON_BACKGROUND = (7, 11, 28, 255)


def content_runs(alpha: Image.Image, threshold: int = 24, minimum_pixels: int = 20) -> list[tuple[int, int]]:
    rows = []
    for y in range(alpha.height):
        visible = sum(
            1 for value in alpha.crop((0, y, alpha.width, y + 1)).get_flattened_data() if value > threshold
        )
        if visible >= minimum_pixels:
            rows.append(y)
    if not rows:
        raise RuntimeError("AutoDev 品牌源图缺少可见内容")
    runs: list[tuple[int, int]] = []
    start = previous = rows[0]
    for row in rows[1:]:
        if row != previous + 1:
            runs.append((start, previous + 1))
            start = row
        previous = row
    runs.append((start, previous + 1))
    return runs


def significant_horizontal_bounds(alpha: Image.Image, top: int, bottom: int, threshold: int = 24) -> tuple[int, int]:
    columns = []
    for x in range(alpha.width):
        visible = sum(
            1 for value in alpha.crop((x, top, x + 1, bottom)).get_flattened_data() if value > threshold
        )
        if visible >= 3:
            columns.append(x)
    if not columns:
        raise RuntimeError("AutoDev 品牌源图无法识别水平边界")
    return columns[0], columns[-1] + 1


def extract_logo_and_mark() -> tuple[Image.Image, Image.Image]:
    source = Image.open(SOURCE).convert("RGBA")
    runs = content_runs(source.getchannel("A"))
    if len(runs) < 2:
        raise RuntimeError("AutoDev 品牌源图必须同时包含图形标志和文字标志")
    mark_top, mark_bottom = runs[0]
    logo_top, logo_bottom = runs[0][0], runs[-1][1]
    mark_left, mark_right = significant_horizontal_bounds(source.getchannel("A"), mark_top, mark_bottom)
    logo_left, logo_right = significant_horizontal_bounds(source.getchannel("A"), logo_top, logo_bottom)
    return (
        source.crop((logo_left, logo_top, logo_right, logo_bottom)),
        source.crop((mark_left, mark_top, mark_right, mark_bottom)),
    )


def fitted(source: Image.Image, width: int, height: int, padding: int) -> Image.Image:
    available_width = width - padding * 2
    available_height = height - padding * 2
    ratio = min(available_width / source.width, available_height / source.height)
    resized = source.resize(
        (max(1, round(source.width * ratio)), max(1, round(source.height * ratio))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas


def icon(mark: Image.Image, size: int, padding: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), ICON_BACKGROUND)
    canvas.alpha_composite(fitted(mark, size, size, padding))
    return canvas


def main() -> None:
    logo, mark = extract_logo_and_mark()
    fitted(logo, 960, 420, 24).save(BRAND_DIR / "autodev-logo.png", optimize=True)
    fitted(mark, 512, 512, 34).save(BRAND_DIR / "autodev-mark.png", optimize=True)
    fitted(mark, 128, 128, 8).save(BRAND_DIR / "autodev-email-mark.png", optimize=True)
    fitted(mark, 64, 64, 5).save(BRAND_DIR / "autodev-mark-64.png", optimize=True)

    app_icon = icon(mark, 512, 54)
    app_icon.save(BRAND_DIR / "autodev-app-icon.png", optimize=True)
    icon(mark, 180, 19).save(BRAND_DIR / "apple-touch-icon.png", optimize=True)
    app_icon.save(
        BRAND_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
