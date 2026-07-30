"""
Shared job-creation logic for both pipeline entry points (web upload,
watcher) -- see PROJECT_SPEC.md "Ingest": one code path regardless of how
a job started. Neither entry point triggers the pipeline itself here;
pipeline/*.py stage functions are still stubs (see AGENTS.md "Current
state") -- these just get a real file onto disk and a pending Job row
recorded, ready for the pipeline to eventually pick up.
"""
from pathlib import Path

from django.conf import settings

from .models import Job


def _create_job(*, original_filename, input_path, owner, source):
    """
    The one place that actually inserts a Job row. Job.status defaults to
    PENDING (see ingest/models.py) -- nothing here enqueues or runs the
    pipeline.
    """
    return Job.objects.create(
        original_filename=original_filename,
        source=source,
        input_path=str(input_path),
        uploaded_by=owner,
    )


def create_job_from_upload(uploaded_file, owner):
    """
    Web-upload entry point. Saves `uploaded_file` (a Django UploadedFile)
    into settings.SCAN_INPUT_DIR -- the same folder the Samba watcher
    monitors -- then records a pending Job owned by `owner`.

    Known gap (not handled here, matches PROJECT_SPEC.md's open question
    on output/file naming): if a file with the same name already exists
    in SCAN_INPUT_DIR, it gets overwritten. No collision handling yet.
    """
    input_dir = Path(settings.SCAN_INPUT_DIR)
    input_dir.mkdir(parents=True, exist_ok=True)
    destination = input_dir / uploaded_file.name

    with open(destination, "wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)

    return _create_job(
        original_filename=uploaded_file.name,
        input_path=destination,
        owner=owner,
        source=Job.Source.UPLOAD,
    )


def create_job_from_watched_file(path, owner):
    """
    Watcher entry point. Unlike create_job_from_upload(), there's no file
    to save -- `path` is already sitting inside settings.SCAN_INPUT_DIR
    (that's the point of watching that directory), so re-reading and
    rewriting it in place would just risk truncating a file that's
    already exactly where it needs to be. This just records the pending
    Job for the file that's already there.
    """
    path = Path(path)
    return _create_job(
        original_filename=path.name,
        input_path=path,
        owner=owner,
        source=Job.Source.WATCHER,
    )
