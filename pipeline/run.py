"""
Pipeline orchestrator.

Single entrypoint used by both the watcher and the web-upload view
(see PROJECT_SPEC.md "Ingest") so there's exactly one code path from
"file appeared" to "validated PDF/A delivered", regardless of how the
job started. Enqueued via Django-RQ for batches, or called directly
for small (<= SYNCHRONOUS_BATCH_SIZE_LIMIT) batches.
"""
from pathlib import Path

from django.conf import settings

from . import cleanup, ingest, ocr, orient, output, pdfa, reassemble, validate


def run_pipeline(job_id: int) -> None:
    """
    Run the full pipeline for ingest.models.Job `job_id`:

      ingest -> cleanup -> orient -> ocr -> reassemble -> pdfa -> validate -> output

    Updates the Job's status/output_path/error_message as it progresses,
    and emails the user on completion for async (queued) runs.

    NOTE: pipeline/ingest.py (stage 1) is still a NotImplementedError
    stub -- this function cannot actually complete a real run yet, even
    though every stage from cleanup.py onward is implemented. Don't
    assume this works end to end just because it reads that way below;
    check ingest.py's own status first (see AGENTS.md "Current state").

    TODO:
      - Load the Job, set status=PROCESSING.
      - Wire the stage functions below together, handling failures at
        any stage (see PROJECT_SPEC.md "Open Questions" re: retry /
        quarantine / notify behavior) -- currently an exception from any
        stage propagates uncaught; nothing routes it to
        output.deliver_failed().
      - send_mail() to settings.NOTIFY_EMAIL when the job completes.
    """
    from ingest.models import Job

    job = Job.objects.get(id=job_id)

    input_path = Path(job.input_path)

    validated_input = ingest.ingest(input_path)
    cleanup_result = cleanup.cleanup(validated_input, job_id)
    cleaned_path = cleanup_result.output_path
    # cleanup_result.pages_dropped / .pages_borderline are available here
    # for stage-progress logging once that's wired up (see TODO above).
    # orient() deskews/auto-rotates BEFORE Document AI ever sees the
    # document -- ocrmypdf's --deskew/--rotate-pages skip all processing
    # on pages that already have a text layer, so this can't happen after
    # reassemble() adds one (see PROJECT_SPEC.md "Decisions Changed").
    oriented_path = orient.orient(cleaned_path, job_id)
    ocr_result = ocr.ocr_document(oriented_path, job_id)
    # reassemble() overlays directly onto oriented_path's own pages --
    # no rasterized page images needed (see pipeline/reassemble.py).
    reassembled_path = reassemble.reassemble(
        oriented_path, ocr_result, output_path=Path(settings.SCAN_OUTPUT_DIR) / f"{job_id}_reassembled.pdf"
    )
    pdfa_path = pdfa.convert_to_pdfa(
        reassembled_path, output_path=Path(settings.SCAN_OUTPUT_DIR) / f"{job_id}_pdfa.pdf"
    )

    validation_result = validate.validate_pdfa(pdfa_path, job_id)
    if validation_result.compliant:
        output.deliver_output(pdfa_path, Path(settings.SCAN_OUTPUT_DIR), job)
    else:
        output.deliver_failed(
            pdfa_path, Path(settings.SCAN_FAILED_DIR), job, reason=validation_result.summary
        )
