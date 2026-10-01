"""Pure OCR checks shared by adapters and combined evidence views."""

from collections.abc import Sequence

from .schemas import OcrBlock


def validate_ocr_blocks(blocks: Sequence[OcrBlock], *, width: int, height: int) -> None:
    """Validate a view before its original block IDs are replaced during merging."""
    if len({block.id for block in blocks}) != len(blocks):
        raise ValueError("duplicate_ocr_block_id")
    for block in blocks:
        x, y, block_width, block_height = block.bbox
        if x + block_width > width or y + block_height > height:
            raise ValueError("ocr_bbox_outside_page")


def validate_ocr_limits(
    blocks: Sequence[OcrBlock], *, max_blocks: int, max_characters: int
) -> None:
    """Apply the same resource limits to one view or the accumulated views."""
    if len(blocks) > max_blocks:
        raise ValueError("ocr_block_limit")
    if sum(len(block.text) for block in blocks) > max_characters:
        raise ValueError("ocr_text_limit")
