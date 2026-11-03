"""Capture every document region without exceeding browser PNG dimensions."""

import json
import math
from pathlib import Path

PIXEL_LIMIT = 8192


def screenshot_regions(width: float, height: float, ratio: float) -> list[dict[str, int]]:
    if not all(math.isfinite(value) and value > 0 for value in (width, height, ratio)):
        raise ValueError("Screenshot geometry must be finite and positive")
    width, height = math.ceil(width), math.ceil(height)
    edge = math.floor(PIXEL_LIMIT / max(1, ratio))
    if edge < 1:
        raise ValueError("Screenshot pixel ratio exceeds its dimension budget")
    return [
        {"x": x, "y": y, "width": min(edge, width - x), "height": min(edge, height - y)}
        for y in range(0, height, edge)
        for x in range(0, width, edge)
    ]


def capture_page_evidence(page, path: Path) -> list[Path]:
    geometry = page.evaluate("""async () => {
      await document.fonts.ready;
      return {
        width: Math.max(innerWidth, document.documentElement.scrollWidth,
                        document.body.scrollWidth),
        height: Math.max(innerHeight, document.documentElement.scrollHeight,
                         document.body.scrollHeight),
        ratio: devicePixelRatio
      };
    }""")
    regions = screenshot_regions(**geometry)
    if len(regions) == 1:
        page.screenshot(path=path, full_page=True)
        return [path]
    images = []
    tiles = []
    for index, region in enumerate(regions):
        target = path if index == 0 else path.with_name(f"{path.stem}.tile-{index:04d}.png")
        page.screenshot(path=target, full_page=True, clip=region)
        images.append(target)
        tiles.append({"path": target.name, "clip": region})
    with path.with_suffix(".tiles.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": "swinglens.screenshot-tiles.v1",
                "geometry": geometry,
                "pixel_limit": PIXEL_LIMIT,
                "tiles": tiles,
            },
            stream,
            indent=2,
        )
    return images
