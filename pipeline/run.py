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

from . import cleanup, ingest, ocr, output, pdfa, reassemble, split, validate


def run_pipeline(job_id: int) -> None:
    """
    Run the full pipeline for ingest.models.Job `job_id`:

      ingest -> cleanup -> split -> ocr -> reassemble -> pdfa -> validate -> output

    Updates the Job's status/output_path/error_message as it progresses,
    and emails the user on completion for async (queued) runs.

    TODO:
      - Load the Job, set status=PROCESSING.
      - Wire the stage functions below together, handling failures at
        any stage (see PROJECT_SPEC.md "Open Questions" re: retry /
        quarantine / notify behavior).
      - On veraPDF validation failure, call output.deliver_failed()
        instead of output.deliver_output().
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
    page_images = split.split_to_page_images(cleaned_path)
    hocr_pages = ocr.ocr_pages(page_images)
    reassembled_path = reassemble.reassemble(
        page_images, hocr_pages, output_path=Path(settings.SCAN_OUTPUT_DIR) / f"{job_id}_reassembled.pdf"
    )
    pdfa_path = pdfa.convert_to_pdfa(
        reassembled_path, output_path=Path(settings.SCAN_OUTPUT_DIR) / f"{job_id}_pdfa.pdf"
    )

    if validate.validate_pdfa(pdfa_path):
        output.deliver_output(pdfa_path, Path(settings.SCAN_OUTPUT_DIR))
    else:
        output.deliver_failed(pdfa_path, Path(settings.SCAN_FAILED_DIR), reason="veraPDF validation failed")
