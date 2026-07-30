"""
Step 8: Output.

Delivers the finished pipeline run: moves the validated PDF/A into
settings.SCAN_OUTPUT_DIR (the folder Paperless-NGX watches) on success,
or into settings.SCAN_FAILED_DIR on failure -- never deleted, since a
failed archival document may still need manual review, not to just
vanish (see PROJECT_SPEC.md "Output").

Also responsible for cleaning up this job's intermediate pipeline
outputs (cleanup.py/orient.py/reassemble.py all write their working
files into SCAN_OUTPUT_DIR, per pipeline/run.py) -- no earlier stage
deletes its own intermediates, so this is the one place that happens;
without it, every job would leave a trail of {job_id}_cleaned.pdf /
{job_id}_oriented.pdf / {job_id}_reassembled.pdf files sitting in the
folder Paperless-NGX watches alongside the real output.
"""
import errno
import logging
import os
import shutil
from pathlib import Path

from django.conf import settings

from ingest.models import Job

logger = logging.getLogger(__name__)

# Matches the {job_id}_<stage>.pdf naming pipeline/run.py already uses
# for intermediate outputs (cleanup.py, orient.py, reassemble.py). Not
# pdfa.py's -- that's `pdfa_path`, the file being delivered/moved here.
_INTERMEDIATE_SUFFIXES = ("cleaned", "oriented", "reassembled")


def _atomic_move(src: Path, dest: Path) -> None:
    """
    Moves `src` to `dest`. Atomic when they're on the same filesystem --
    the common case here, since SCAN_OUTPUT_DIR and SCAN_FAILED_DIR are
    both subdirectories of the same docker-compose volume by default.
    Falls back, only if that's not true (a genuine cross-device move),
    to copying into a temp file *on the destination's own filesystem*
    and atomically renaming that into place, rather than a plain
    copy-then-delete -- so a partially-written file is never visible at
    `dest`'s final name to whatever's watching that directory.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        tmp_path = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
        shutil.copy2(src, tmp_path)
        tmp_path.rename(dest)
        src.unlink()


def _cleanup_intermediates(job_id: int) -> None:
    """
    Removes this job's intermediate pipeline outputs from
    SCAN_OUTPUT_DIR. Best-effort: a file already gone is fine (not every
    stage necessarily ran), and a real error here shouldn't undo an
    otherwise-successful delivery -- logged, not raised.
    """
    output_dir = Path(settings.SCAN_OUTPUT_DIR)
    for suffix in _INTERMEDIATE_SUFFIXES:
        intermediate = output_dir / f"{job_id}_{suffix}.pdf"
        try:
            intermediate.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "[output] job=%s could not remove intermediate file %s: %s",
                job_id,
                intermediate,
                exc,
            )


def deliver_output(pdfa_path: Path, output_dir: Path, job: Job) -> Path:
    """
    Moves the validated PDF/A into `output_dir` under a job-scoped
    filename ({job.id}_{job.original_filename} -- same {job_id}_ prefix
    convention the pipeline's intermediate files already use) so two
    jobs can never collide even if their original filenames match.
    Marks `job` DONE, records the final path, and cleans up this job's
    intermediate files from `output_dir`.
    """
    final_path = output_dir / f"{job.id}_{job.original_filename}"
    _atomic_move(pdfa_path, final_path)

    _cleanup_intermediates(job.id)

    job.status = Job.Status.DONE
    job.output_path = str(final_path)
    job.save(update_fields=["status", "output_path", "updated_at"])

    logger.info("[output] job=%s delivered to %s", job.id, final_path)
    return final_path


def deliver_failed(pdfa_path: Path, failed_dir: Path, job: Job, reason: str) -> Path:
    """
    Moves a file that failed PDF/A validation into `failed_dir` --
    inspectable, not deleted. Records `reason` on `job.error_message`
    (the field already exists for exactly this) rather than a sidecar
    file, and marks `job` FAILED. Same job-scoped naming and
    intermediate cleanup as deliver_output().
    """
    final_path = failed_dir / f"{job.id}_{job.original_filename}"
    _atomic_move(pdfa_path, final_path)

    _cleanup_intermediates(job.id)

    job.status = Job.Status.FAILED
    job.output_path = str(final_path)
    job.error_message = reason
    job.save(update_fields=["status", "output_path", "error_message", "updated_at"])

    logger.error("[output] job=%s FAILED, moved to %s: %s", job.id, final_path, reason)
    return final_path
