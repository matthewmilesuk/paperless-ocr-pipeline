"""
Step 5: Reassemble + overlay.

Rebuilds a single PDF in original page order, overlaying each page's
hOCR text as an invisible layer on top of the original page image, so
the result looks like the scan but is fully searchable/copyable (see
PROJECT_SPEC.md "Reassemble + overlay").
"""
from pathlib import Path
from typing import List

from PIL.Image import Image


def reassemble(page_images: List[Image], hocr_pages: List[str], output_path: Path) -> Path:
    """
    Combine `page_images` with their corresponding `hocr_pages` (invisible
    text layer) into a single PDF at `output_path`, preserving original
    page order.

    TODO:
      - This is typically handled by ocrmypdf itself when given a hOCR
        sidecar, or manually via reportlab/pikepdf if doing the overlay
        by hand. Decide which approach and wire it in here.
    """
    raise NotImplementedError
