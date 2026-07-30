#!/usr/bin/env python
"""
Deliberate, opt-in smoke test for pipeline/ocr.py against the REAL
Google Document AI API.

THIS MAKES ONE REAL, BILLABLE API CALL. It costs a small amount of real
money (a fraction of a cent per call -- see PROJECT_SPEC.md "OCR -
Google Document AI" for pricing) and uses the real credentials
configured in .env/.env.local. It is NOT run by `manage.py test`,
scripts/test-all.sh, or any other automatic process -- run this only
when you deliberately want to confirm the real GCP setup (credentials,
project ID, processor ID, IAM role) actually works end to end. See
AGENTS.md "Local development" for the .env/.env.local split this relies
on.

Usage:
    python scripts/smoke-test-ocr.py [path/to/scan.pdf]

Defaults to tests/fixtures/sample_scan.pdf if no path is given -- a
synthetic single-page fixture (placeholder text drawn with PIL, not a
real scan) good enough to confirm connectivity/auth/plumbing, but not a
meaningful check of real-world OCR accuracy. Pass a real scan for that.
"""
import logging
import os
import sys
from pathlib import Path

import django

DEFAULT_SAMPLE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_scan.pdf"


def main():
    sample_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLE
    if not sample_path.exists():
        print(f"FAIL: {sample_path} does not exist.", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from django.conf import settings

    from pipeline.ocr import ocr_document

    print(
        f"Calling Document AI (REAL, BILLABLE call) for {sample_path}\n"
        f"  project={settings.GCP_PROJECT_ID} "
        f"location={settings.GCP_DOCAI_LOCATION} "
        f"processor={settings.GCP_DOCAI_PROCESSOR_ID}"
    )

    result = ocr_document(sample_path, job_id=0)

    print(f"OK: {result.page_count} page(s) processed.")
    text = result.document.get("text", "")
    preview = text[:300] + ("..." if len(text) > 300 else "")
    print("Extracted text preview:")
    print(preview or "(no text extracted)")


if __name__ == "__main__":
    main()
