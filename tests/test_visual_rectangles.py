"""Closed pixel boundaries can supply missing grid extents without UIA or OCR."""

import pytest
from PIL import Image, ImageDraw

from brain.visual.rectangles import rectangular_candidates

pytest.importorskip("cv2")


def test_grid_frame_and_cells_are_image_derived():
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((37, 61, 550, 330), outline="black")
    draw.line((37, 101, 550, 101), fill="black")
    draw.line((201, 61, 201, 101), fill="black")
    draw.line((390, 61, 390, 101), fill="black")
    boxes = rectangular_candidates(image)
    assert [37, 61, 514, 270] in boxes
    assert any(
        abs(x - 201) <= 1 and abs(y - 61) <= 1 and w < 200 and h < 44 for x, y, w, h in boxes
    )
    assert len(rectangular_candidates(image, limit=1)) == 1
    # Translating pixels translates geometry; there are no expected coordinates.
    shifted = Image.new("RGB", (640, 440), "white")
    shifted.paste(image, (17, 23))
    assert [54, 84, 514, 270] in rectangular_candidates(shifted)


def test_open_border_and_image_edge_are_not_complete_extents():
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(50, 50), (300, 50), (300, 200), (50, 200)], fill="black")
    draw.rectangle((0, 230, 350, 390), outline="black")
    assert rectangular_candidates(image) == []


@pytest.mark.parametrize("bottom", [131, 82])
def test_pale_input_borders_survive_local_contrast_detection(bottom):
    image = Image.new("RGB", (720, 320), (247, 247, 247))
    draw = ImageDraw.Draw(image)
    expected = [(43, 67, 651, 34), (46, 178, 512, 34)]
    for x, y, width, height in expected:
        draw.rectangle(
            (x, y, x + width - 1, y + height - 1),
            fill="white",
            outline=(236, 236, 236),
        )
        draw.line((x + 1, y + height - 1, x + width - 2, y + height - 1), fill=(bottom,) * 3)
    # A focused field's caret and an unrelated underline are not input extents.
    draw.line((46, 70, 46, 96), fill="black")
    draw.line((50, 245, 500, 245), fill=(236,) * 3)
    boxes = rectangular_candidates(image)
    for expected_box in expected:
        assert any(
            all(
                abs(actual - desired) <= 2
                for actual, desired in zip(box, expected_box, strict=True)
            )
            for box in boxes
        )
    assert all(y < 240 for _, y, _, _ in boxes)
