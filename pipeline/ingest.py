"""
Step 1: Ingest.

Confirms the input file is a well-formed, readable PDF before the rest
of the pipeline touches it. The watcher and the web-upload view both
call into pipeline.run.run_pipeline() with a path to the file already
on disk -- this step is the shared validation point for both entry
paths (see PROJECT_SPEC.md "Ingest").

A small validation gate, not a transformation stage: on success it
returns `input_path` unchanged (nothing is rewritten here). Checks with
pikepdf whether the file can actually be opened, rather than trusting
the `.pdf` extension/a header sniff -- matching how the rest of this
pipeline treats "can this even be opened" as a real, verified question,
not an assumption.
"""
import logging
from pathlib import Path

import pikepdf

logger = logging.getLogger(__name__)


class EmptyFileError(Exception):
    """Raised when the input file is zero bytes."""


class UnreadablePdfError(Exception):
    """Raised when pikepdf can't open the input file as a PDF at all."""


class EmptyPdfError(Exception):
    """Raised when the input file opens fine but has zero pages."""


def ingest(input_path: Path, job_id: int) -> Path:
    """
    Validates `input_path` is a genuinely readable, non-empty PDF and
    returns it unchanged for the rest of the pipeline.

    Raises (each logged distinctly before raising, matching the rest of
    the pipeline's per-failure-type logging):
      - EmptyFileError: the file is zero bytes.
      - UnreadablePdfError: pikepdf can't open it as a PDF at all
        (corrupted, or not actually a PDF despite the extension).
      - EmptyPdfError: it opens fine but has zero pages.
    """
    if input_path.stat().st_size == 0:
        logger.error("[ingest] job=%s %s is empty (0 bytes)", job_id, input_path)
        raise EmptyFileError(f"{input_path} is empty (0 bytes)")

    try:
        with pikepdf.open(str(input_path)) as pdf:
            page_count = len(pdf.pages)
    except pikepdf.PdfError as exc:
        logger.error(
            "[ingest] job=%s %s could not be opened as a PDF: %s",
            job_id,
            input_path,
            exc,
        )
        raise UnreadablePdfError(
            f"{input_path} could not be opened as a PDF: {exc}"
        ) from exc

    if page_count == 0:
        logger.error(
            "[ingest] job=%s %s opened but has zero pages", job_id, input_path
        )
        raise EmptyPdfError(f"{input_path} opened but has zero pages")

    return input_path
