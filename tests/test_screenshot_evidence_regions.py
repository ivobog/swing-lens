"""Complete screenshot coverage and native-image dimension boundaries."""

import math

import pytest
from single_run_certification.screenshot_evidence import PIXEL_LIMIT, screenshot_regions


@pytest.mark.parametrize(
    "width,height,ratio", [(1440, 1000, 1), (40000, 700, 1), (700, 40000, 2), (17000, 17000, 1.25)]
)
def test_bounded_regions_cover_the_entire_document_without_gaps(width, height, ratio):
    regions = screenshot_regions(width, height, ratio)
    assert sum(r["width"] * r["height"] for r in regions) == width * height
    assert max(r["x"] + r["width"] for r in regions) == width
    assert max(r["y"] + r["height"] for r in regions) == height
    for row in regions:
        assert 0 < row["width"] * ratio <= PIXEL_LIMIT
        assert 0 < row["height"] * ratio <= PIXEL_LIMIT
    for y in {r["y"] for r in regions}:
        rows = [r for r in regions if r["y"] == y]
        assert rows[0]["x"] == 0
        assert all(a["x"] + a["width"] == b["x"] for a, b in zip(rows, rows[1:], strict=False))
    columns = sorted({r["y"]: r["height"] for r in regions}.items())
    assert columns[0][0] == 0
    assert all(y + h == next_y for (y, h), (next_y, _) in zip(columns, columns[1:], strict=False))


@pytest.mark.parametrize(
    "width,height,ratio", [(0, 100, 1), (100, -1, 1), (100, 100, 0), (math.inf, 100, 1)]
)
def test_invalid_geometry_never_claims_a_complete_capture(width, height, ratio):
    with pytest.raises(ValueError):
        screenshot_regions(width, height, ratio)
