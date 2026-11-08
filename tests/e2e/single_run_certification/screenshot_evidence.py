"""Capture every document region without exceeding browser PNG dimensions."""

import json
import math
from pathlib import Path

PIXEL_LIMIT = 8192


def screenshot_regions(
    width: float,
    height: float,
    ratio: float,
    *,
    viewport_width: float | None = None,
    viewport_height: float | None = None,
) -> list[dict[str, int]]:
    if not all(math.isfinite(value) and value > 0 for value in (width, height, ratio)):
        raise ValueError("Screenshot geometry must be finite and positive")
    width, height = math.ceil(width), math.ceil(height)
    edge = math.floor(PIXEL_LIMIT / max(1, ratio))
    if edge < 1:
        raise ValueError("Screenshot pixel ratio exceeds its dimension budget")
    for value in (viewport_width, viewport_height):
        if value is not None and (not math.isfinite(value) or value < 1):
            raise ValueError("Screenshot viewport must be finite and positive")
    tile_width = min(edge, math.floor(viewport_width)) if viewport_width is not None else edge
    tile_height = min(edge, math.floor(viewport_height)) if viewport_height is not None else edge
    return [
        {
            "x": x,
            "y": y,
            "width": min(tile_width, width - x),
            "height": min(tile_height, height - y),
        }
        for y in range(0, height, tile_height)
        for x in range(0, width, tile_width)
    ]


def capture_page_evidence(page, path: Path) -> list[Path]:
    geometry = page.evaluate("""async () => {
      await document.fonts.ready;
      return {
        width: Math.max(innerWidth, document.documentElement.scrollWidth,
                        document.body.scrollWidth),
        height: Math.max(innerHeight, document.documentElement.scrollHeight,
                         document.body.scrollHeight),
        ratio: devicePixelRatio,
        viewport_width: innerWidth,
        viewport_height: innerHeight,
        scroll_x: scrollX,
        scroll_y: scrollY
      };
    }""")
    regions = screenshot_regions(geometry["width"], geometry["height"], geometry["ratio"])
    if len(regions) == 1:
        page.screenshot(path=path, full_page=True)
        return [path]
    # Keep repeated sticky navigation outside interior tile clips. These insets
    # exceed SwingLens navigation height while retaining original viewport size.
    inset_x = min(128, (geometry["viewport_width"] - 1) // 3)
    inset_y = min(128, (geometry["viewport_height"] - 1) // 3)
    regions = screenshot_regions(
        geometry["width"],
        geometry["height"],
        geometry["ratio"],
        viewport_width=geometry["viewport_width"] - 2 * inset_x,
        viewport_height=geometry["viewport_height"] - 2 * inset_y,
    )
    images = []
    tiles = []
    try:
        for index, region in enumerate(regions):
            scroll = page.evaluate(
                """async ([x, y]) => {
              window.scrollTo({left: x, top: y, behavior: 'instant'});
              await new Promise(requestAnimationFrame);
              return {x: scrollX, y: scrollY};
            }""",
                [max(0, region["x"] - inset_x), max(0, region["y"] - inset_y)],
            )
            clip = {**region, "x": region["x"] - scroll["x"], "y": region["y"] - scroll["y"]}
            if not (
                0 <= clip["x"] <= geometry["viewport_width"] - clip["width"]
                and 0 <= clip["y"] <= geometry["viewport_height"] - clip["height"]
            ):
                raise ValueError("Screenshot region is not fully visible after scrolling")
            target = path if index == 0 else path.with_name(f"{path.stem}.tile-{index:04d}.png")
            # A bounded PNG clip with full_page=True still asks Chromium to
            # rasterize beyond the viewport; long native JSON documents hang.
            page.screenshot(path=target, full_page=False, clip=clip)
            images.append(target)
            tiles.append(
                {"path": target.name, "clip": region, "viewport_clip": clip, "scroll": scroll}
            )
    finally:
        page.evaluate(
            """([x, y]) => window.scrollTo({left: x, top: y, behavior: 'instant'})""",
            [geometry["scroll_x"], geometry["scroll_y"]],
        )
    with path.with_suffix(".tiles.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": "swinglens.screenshot-tiles.v1",
                "geometry": geometry,
                "pixel_limit": PIXEL_LIMIT,
                "capture_method": "scrolled_viewport",
                "tiles": tiles,
            },
            stream,
            indent=2,
        )
    return images
