"""
Step 6: PDF/A conversion (ocrmypdf).

Converts the reassembled, text-overlaid PDF into archival PDF/A, with
--optimize 3 handling file size reduction -- never the source scan
resolution, which stays untouched as the archival "source of truth"
(see PROJECT_SPEC.md "PDF/A conversion").
"""
from pathlib import Path


def convert_to_pdfa(reassembled_path: Path, output_path: Path) -> Path:
    """
    Run ocrmypdf against `reassembled_path`, producing a PDF/A file at
    `output_path`.

    TODO:
      - Call ocrmypdf with:
          --output-type pdfa
          --rotate-pages --deskew   (safety net)
          --optimize 3
      - Since the text layer already exists from the reassemble step,
        pass --skip-text or run ocrmypdf in a mode that won't
        re-OCR/duplicate the existing layer.
    """
    raise NotImplementedError
