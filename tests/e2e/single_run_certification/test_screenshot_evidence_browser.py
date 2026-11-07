"""Real Chromium capture of long JSON and every region of a coloured document."""

import base64
import json
import struct

import pytest
from evidence_output import output_directory

from single_run_certification import screenshot_evidence
from single_run_certification.screenshot_evidence import capture_page_evidence


@pytest.mark.e2e
def test_long_native_json_document_has_complete_bounded_screenshot_evidence(page):
    page.set_viewport_size({"width": 1600, "height": 900})
    body = json.dumps(
        {
            "evidence": [
                {"ticker": f"TICKER{i:04}", "detail": "retained evidence " * 40}
                for i in range(1200)
            ]
        }
    )
    page.route(
        "http://screenshot.test/evidence",
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )
    page.goto("http://screenshot.test/evidence")
    assert page.evaluate("document.documentElement.scrollHeight") > 30_000
    target = output_directory("screenshot-json") / "complete.png"
    images = capture_page_evidence(page, target)
    manifest = json.loads(target.with_suffix(".tiles.json").read_text())
    assert manifest["capture_method"] == "scrolled_viewport"
    geometry, tiles = manifest["geometry"], manifest["tiles"]
    assert sum(tile["clip"]["width"] * tile["clip"]["height"] for tile in tiles) == (
        geometry["width"] * geometry["height"]
    )
    assert len(images) == len(tiles) > 30
    for image, tile in zip(images, tiles, strict=True):
        raw = image.read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", raw[16:24])
        assert (width, height) == (tile["clip"]["width"], tile["clip"]["height"])
        assert height <= 900 and width <= 1600
    assert page.evaluate("[scrollX, scrollY]") == [0, 0]


@pytest.mark.e2e
def test_scrolled_tiles_capture_actual_content_on_both_axes_and_restore_scroll(page, monkeypatch):
    monkeypatch.setattr(screenshot_evidence, "PIXEL_LIMIT", 2048)
    page.set_viewport_size({"width": 1200, "height": 800})
    boxes = "".join(
        f'<div style="position:absolute;left:{x * 1000}px;top:{y * 1000}px;'
        f'width:1000px;height:1000px;background:rgb({50 + 70 * x},{50 + 70 * y},80)"></div>'
        for x in range(3)
        for y in range(3)
    )
    page.set_content('<html><body style="margin:0">' + boxes + "</body></html>")
    page.evaluate("window.scrollTo(700, 600)")
    target = output_directory("screenshot-colours") / "complete.png"
    images = capture_page_evidence(page, target)
    manifest = json.loads(target.with_suffix(".tiles.json").read_text())
    assert (
        sum(tile["clip"]["width"] * tile["clip"]["height"] for tile in manifest["tiles"])
        == 3000 * 3000
    )
    for image, tile in zip(images, manifest["tiles"], strict=True):
        x, y = tile["clip"]["x"] + 10, tile["clip"]["y"] + 10
        pixel = page.evaluate(
            """async encoded => {
          const blob = await (await fetch('data:image/png;base64,' + encoded)).blob();
          const bitmap = await createImageBitmap(blob);
          const context = new OffscreenCanvas(1, 1).getContext('2d');
          context.drawImage(bitmap, 10, 10, 1, 1, 0, 0, 1, 1);
          bitmap.close();
          return Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3);
        }""",
            base64.b64encode(image.read_bytes()).decode(),
        )
        assert pixel == [50 + 70 * (x // 1000), 50 + 70 * (y // 1000), 80]
    assert page.evaluate("[scrollX, scrollY]") == [700, 600]
