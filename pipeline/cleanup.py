"""
Step 2: Cleanup pass (Stirling PDF API).

Deskew / auto-rotate and blank-page removal. Confidently-blank pages
(< BLANK_PAGE_DROP_THRESHOLD_PCT ink coverage) are dropped automatically.
Borderline pages (between the drop and review thresholds) are kept in
the document by default and logged as ingest.models.BorderlinePage for
manual review -- a false-positive drop is unrecoverable once the source
paper is shredded (see PROJECT_SPEC.md "Cleanup pass").
"""
from pathlib import Path


def cleanup(input_path: Path, job_id: int) -> Path:
    """
    Call the Stirling PDF API (settings.STIRLING_PDF_URL) to deskew,
    auto-rotate, and drop confidently-blank pages from `input_path`.
    Records any borderline-blank pages against `job_id` for review.

    Returns the path to the cleaned-up PDF.

    TODO:
      - POST to Stirling PDF's deskew/auto-rotate endpoint.
      - POST to Stirling PDF's blank-page-removal endpoint using
        settings.BLANK_PAGE_DROP_THRESHOLD_PCT.
      - For pages between BLANK_PAGE_DROP_THRESHOLD_PCT and
        BLANK_PAGE_REVIEW_THRESHOLD_PCT, create
        ingest.models.BorderlinePage entries instead of dropping them.
    """
    raise NotImplementedError
