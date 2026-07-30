"""
Step 2: Cleanup pass (custom blank-page detection).

Rasterizes each page of the input PDF, measures the percentage of
non-white ("ink") pixels, and applies a two-tier threshold:

  - Below settings.BLANK_PAGE_DROP_THRESHOLD_PCT: confidently blank,
    dropped from the output PDF.
  - Between the drop and review thresholds: borderline. Kept in the
    output (never silently dropped) and logged as an
    ingest.models.BorderlinePage for manual review, since a
    false-positive drop is unrecoverable once the source paper is
    shredded.
  - At or above settings.BLANK_PAGE_REVIEW_THRESHOLD_PCT: clearly not
    blank. Kept, nothing logged.

Deskew/auto-rotate is NOT done here. See PROJECT_SPEC.md "Decisions
Changed": that now happens at the PDF/A conversion stage (pdfa.py) via
ocrmypdf's own --deskew/--rotate-pages flags. This stage originally
called the Stirling PDF API for both concerns, but Stirling's rotation
endpoint only supports fixed 90-degree increments (not real skew
detection) and its blank-page removal is a binary delete/keep with no
way to express the confident/borderline distinction above -- so this
stage was rebuilt as custom code instead.

Page removal is done losslessly on the original PDF via pikepdf; the
rasterized images used for measurement are for scoring only and are
never written back into the output.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pikepdf
from django.conf import settings
from pdf2image import convert_from_path
from PIL.Image import Image

# DPI used only for the ink-coverage measurement rasterization -- distinct
# from the pipeline's archival scan resolution (400 DPI, see
# PROJECT_SPEC.md "Inputs"). Measuring blank-vs-not doesn't need
# scan-quality detail: ink coverage is a ratio of dark to total pixels,
# which is stable across render resolution, so a coarser raster gets the
# same answer for a fraction of the rasterization + pixel-scan cost. 100
# DPI keeps per-page measurement fast while still resolving marks a few
# millimeters across.
MEASUREMENT_DPI = 100

# A pixel counts as "ink" if its grayscale value is at or below this. Not
# 255 (pure white): scanned "blank" pages carry faint noise/antialiasing
# from the scan itself, and this tolerance keeps that noise from
# registering as ink. This is a low-level measurement detail, not a
# product-tunable setting like the two coverage thresholds below --
# revisit if real scans show it needs adjusting for the actual scanner's
# noise floor.
INK_LUMINANCE_THRESHOLD = 250


@dataclass
class CleanupResult:
    """
    Result of cleanup(): the cleaned PDF plus what happened, so
    run_pipeline can log it.
    """

    output_path: Path
    pages_total: int
    pages_dropped: int
    pages_borderline: int


def _ink_coverage_percent(page_image: Image) -> float:
    """Percentage of non-white ("ink") pixels in a rasterized page image."""
    grayscale = page_image.convert("L")
    total_pixels = grayscale.width * grayscale.height
    if total_pixels == 0:
        return 0.0
    histogram = grayscale.histogram()
    ink_pixels = sum(histogram[: INK_LUMINANCE_THRESHOLD + 1])
    return 100.0 * ink_pixels / total_pixels


def cleanup(input_path: Path, job_id: int) -> CleanupResult:
    """
    Measure ink coverage for every page of `input_path`, drop
    confidently-blank pages, and record borderline pages against `job_id`
    for review.

    Returns a CleanupResult with the cleaned PDF's path and per-stage
    counts.
    """
    from ingest.models import BorderlinePage

    page_images: List[Image] = convert_from_path(str(input_path), dpi=MEASUREMENT_DPI)
    pages_total = len(page_images)

    drop_threshold = settings.BLANK_PAGE_DROP_THRESHOLD_PCT
    review_threshold = settings.BLANK_PAGE_REVIEW_THRESHOLD_PCT

    drop_indices: List[int] = []
    pages_borderline = 0

    for index, page_image in enumerate(page_images):
        coverage = _ink_coverage_percent(page_image)
        if coverage < drop_threshold:
            drop_indices.append(index)
        elif coverage < review_threshold:
            BorderlinePage.objects.create(
                job_id=job_id,
                page_number=index + 1,
                ink_coverage_percent=coverage,
            )
            pages_borderline += 1

    output_path = Path(settings.SCAN_OUTPUT_DIR) / f"{job_id}_cleaned.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with pikepdf.open(str(input_path)) as pdf:
        for index in reversed(drop_indices):
            del pdf.pages[index]
        pdf.save(str(output_path))

    return CleanupResult(
        output_path=output_path,
        pages_total=pages_total,
        pages_dropped=len(drop_indices),
        pages_borderline=pages_borderline,
    )
