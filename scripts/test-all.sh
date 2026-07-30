#!/usr/bin/env bash
#
# test-all.sh — full verification for paperless-ocr-pipeline
#
# Default: runs Django unit tests only (fast, no external services needed).
# --smoke:  additionally runs a full pipeline smoke test against a sample
#           scan, checking the final output validates as PDF/A via veraPDF.
#           Requires the docker-compose stack to be up (Stirling PDF, etc.)
#           and real GCP credentials configured — not run by default since
#           it costs a small amount (Document AI OCR calls) and needs infra.
#
# Usage:
#   ./scripts/test-all.sh            # unit tests only
#   ./scripts/test-all.sh --smoke    # unit tests + full pipeline smoke test

set -euo pipefail

RUN_SMOKE=false
if [[ "${1:-}" == "--smoke" ]]; then
  RUN_SMOKE=true
fi

echo "== Django unit tests =="
python manage.py test

if [[ "$RUN_SMOKE" == "false" ]]; then
  echo ""
  echo "Skipping smoke test (pass --smoke to run it)."
  echo "Note: pipeline stages are currently stubs (see AGENTS.md) — the"
  echo "smoke test will fail with NotImplementedError until they're built."
  exit 0
fi

echo ""
echo "== Full pipeline smoke test =="

SAMPLE_SCAN="tests/fixtures/sample_scan.pdf"
SMOKE_OUTPUT_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_OUTPUT_DIR"' EXIT

if [[ ! -f "$SAMPLE_SCAN" ]]; then
  echo "Missing $SAMPLE_SCAN — add a sample duplex scan (a few pages,"
  echo "including at least one genuinely blank page) to test against."
  exit 1
fi

echo "Running $SAMPLE_SCAN through the full pipeline..."
# NOTE: pipeline.run.run_pipeline() has no CLI entrypoint -- it takes a
# Job id (not a file path) and looks up job.input_path from the DB, and it
# writes to settings.SCAN_OUTPUT_DIR / SCAN_FAILED_DIR rather than an
# --output-dir argument. There's no `python -m pipeline.run <file>
# --output-dir <dir>` to call. Instead, create a Job row pointing at the
# sample scan and override the output dirs via env for this run so files
# land in our temp dir instead of the real configured one.
export SCAN_OUTPUT_DIR="$SMOKE_OUTPUT_DIR"
export SCAN_FAILED_DIR="$SMOKE_OUTPUT_DIR/failed"
mkdir -p "$SCAN_FAILED_DIR"

python manage.py shell -c "
from ingest.models import Job
from pipeline.run import run_pipeline

job = Job.objects.create(
    original_filename='sample_scan.pdf',
    source=Job.Source.WATCHER,
    input_path='$SAMPLE_SCAN',
)
run_pipeline(job.id)
"

OUTPUT_PDF=$(find "$SMOKE_OUTPUT_DIR" -name "*.pdf" | head -n 1)

if [[ -z "$OUTPUT_PDF" ]]; then
  echo "FAIL: no output PDF produced."
  exit 1
fi

echo "Validating $OUTPUT_PDF against PDF/A with veraPDF..."
if ! command -v verapdf &> /dev/null; then
  echo "FAIL: veraPDF not found on PATH. Install it or run this inside the"
  echo "docker-compose stack where it's available."
  exit 1
fi

verapdf --format text "$OUTPUT_PDF"

echo ""
echo "Smoke test complete. Review the veraPDF output above for compliance"
echo "details — a non-zero exit above would already have stopped this script."
