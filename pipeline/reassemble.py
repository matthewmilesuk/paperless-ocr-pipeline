"""
Step 5: Reassemble + overlay.

Rebuilds a single PDF in original page order, overlaying Document AI's
per-page text (with its layout/bounding-box data) as an invisible layer
on top of the original page image, so the result looks like the scan but
is fully searchable/copyable (see PROJECT_SPEC.md "Reassemble + overlay").
"""
from pathlib import Path
from typing import List

from PIL.Image import Image

from .ocr import OcrResult


def reassemble(page_images: List[Image], ocr_result: OcrResult, output_path: Path) -> Path:
    """
    Combine `page_images` with the corresponding text/layout from
    `ocr_result` (see pipeline/ocr.py -- ocr_result.document["pages"] has
    per-page bounding boxes and text anchors into ocr_result.document["text"])
    into a single PDF at `output_path`, preserving original page order.

    TODO:
      - This is typically handled by ocrmypdf itself when given a hOCR
        sidecar, or manually via reportlab/pikepdf if doing the overlay
        by hand. Decide which approach and wire it in here -- either way,
        ocr_result.document's per-page layout needs converting into
        whatever overlay format is chosen (e.g. hOCR) first.
    """
    raise NotImplementedError
