"""
Step 5: PDF/A conversion (ocrmypdf).

Converts the reassembled, text-overlaid PDF into archival PDF/A.
Deliberately does NOT pass --deskew/--rotate-pages here: pipeline/orient.py
already did that upstream, before Document AI/reassemble.py added any
text layer. ocrmypdf skips all per-page processing (not just OCR) on
pages that already have text, so those flags would be silent no-ops at
this stage regardless -- worse, leaving them here would make pdfa.py
*appear* to support rotation correction it can no longer actually
perform once a text layer exists (see PROJECT_SPEC.md "Decisions
Changed").

--skip-text is required, not optional: without it, ocrmypdf raises
PriorOcrFoundError on the first page it finds with existing text --
every page, since reassemble.py already added Document AI's text layer
to all of them by this point.
"""
import logging
from pathlib import Path

import ocrmypdf
from ocrmypdf.exceptions import EncryptedPdfError, SubprocessOutputError

logger = logging.getLogger(__name__)


def convert_to_pdfa(reassembled_path: Path, output_path: Path) -> Path:
    """
    Run ocrmypdf against `reassembled_path`, producing PDF/A at
    `output_path`. The text layer already exists (from reassemble.py) --
    --skip-text tells ocrmypdf to leave every page's content untouched
    and just perform the PDF/A structural conversion + optimization.
    """
    try:
        ocrmypdf.ocr(
            str(reassembled_path),
            str(output_path),
            output_type="pdfa",
            skip_text=True,
            optimize=3,
        )
    except EncryptedPdfError as exc:
        logger.error("[pdfa] input PDF is encrypted, cannot convert: %s", exc)
        raise
    except SubprocessOutputError as exc:
        logger.error("[pdfa] a subprocess (ghostscript/etc.) failed: %s", exc)
        raise

    return output_path
