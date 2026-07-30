"""
Step 8: Output.

Moves a validated PDF/A into settings.SCAN_OUTPUT_DIR, the folder
Paperless-NGX watches (see PROJECT_SPEC.md "Output").
"""
from pathlib import Path


def deliver_output(pdfa_path: Path, output_dir: Path) -> Path:
    """
    Move `pdfa_path` into `output_dir` for Paperless-NGX to pick up.

    TODO:
      - Move (not copy) the validated file into output_dir.
      - Decide on final filename convention (open question in
        PROJECT_SPEC.md).
    """
    raise NotImplementedError


def deliver_failed(pdfa_path: Path, failed_dir: Path, reason: str) -> Path:
    """
    Move a file that failed veraPDF validation into `failed_dir` with
    a log entry explaining `reason`, for manual review.

    TODO:
      - Move the file and write an accompanying log/report next to it.
    """
    raise NotImplementedError
