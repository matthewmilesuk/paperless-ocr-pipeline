from pathlib import Path

import django_rq
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UploadForm
from .models import Job


def upload(request):
    """
    Web UI upload entry point. Saves the incoming file into the same
    input directory the Samba watcher monitors, then enqueues the same
    pipeline entrypoint the watcher uses -- one code path regardless of
    how the job was triggered (see PROJECT_SPEC.md).
    """
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data["file"]
            input_path = Path(settings.SCAN_INPUT_DIR) / uploaded.name
            # TODO: stream `uploaded` to `input_path`.

            job = Job.objects.create(
                original_filename=uploaded.name,
                source=Job.Source.UPLOAD,
                input_path=str(input_path),
            )

            # TODO: below ~5 files, run pipeline.run_pipeline() synchronously
            # instead of enqueuing -- see PROJECT_SPEC.md "Processing Mode".
            queue = django_rq.get_queue("default")
            queue.enqueue("pipeline.run.run_pipeline", job.id)

            return redirect("job_status", job_id=job.id)
    else:
        form = UploadForm()

    return render(request, "ingest/upload.html", {"form": form})


def job_status(request, job_id):
    job = get_object_or_404(Job, id=job_id)
    return render(request, "ingest/job_status.html", {"job": job})
