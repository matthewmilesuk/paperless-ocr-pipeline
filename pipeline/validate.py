"""
Step 7: Validate (veraPDF).

Confirms actual PDF/A compliance. If validation fails, the file must
NOT proceed to the output folder -- it's moved to settings.SCAN_FAILED_DIR
with a log entry for manual review instead (see PROJECT_SPEC.md
"Validate - veraPDF").
"""
from pathlib import Path


def validate_pdfa(pdfa_path: Path) -> bool:
    """
    Run veraPDF against `pdfa_path` and return True if it passes PDF/A
    compliance validation, False otherwise.

    TODO:
      - Shell out to (or call the REST API of) veraPDF against
        `pdfa_path`.
      - Parse the validation report; return False (and surface the
        report) on any compliance failure.
    """
    raise NotImplementedError
