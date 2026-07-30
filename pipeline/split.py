"""
Step 3: Split.

Splits the cleaned PDF into individual page images for OCR. Pages are
kept in-memory or in temp files only -- never persisted to disk as
separate files (see PROJECT_SPEC.md "Split").
"""
from pathlib import Path
from typing import List

from PIL.Image import Image


def split_to_page_images(cleaned_path: Path) -> List[Image]:
    """
    Render each page of `cleaned_path` to an in-memory image
    (e.g. via pdf2image.convert_from_path) at the source DPI.

    TODO:
      - Use pdf2image.convert_from_path(cleaned_path, dpi=400) or
        equivalent, keeping output in memory / a tempdir that's
        cleaned up after the pipeline run.
    """
    raise NotImplementedError
