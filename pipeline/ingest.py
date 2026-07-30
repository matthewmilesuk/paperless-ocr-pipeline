"""
Step 1: Ingest.

Confirms the input file is a well-formed, readable PDF before the rest
of the pipeline touches it. The watcher and the web-upload view both
call into pipeline.run.run_pipeline() with a path to the file already
on disk -- this step is the shared validation point for both entry
paths (see PROJECT_SPEC.md "Ingest").
"""
from pathlib import Path


def ingest(input_path: Path) -> Path:
    """
    Validate `input_path` is a readable PDF and return the path to use
    for the rest of the pipeline.

    TODO:
      - Verify the file is a valid PDF (e.g. header check / PyPDF open).
      - Reject empty or zero-byte files.
    """
    raise NotImplementedError
