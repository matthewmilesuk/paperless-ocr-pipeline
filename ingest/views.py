import django_rq
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UploadForm
from .models import Job
from .services import create_job_from_upload


def _jobs_visible_to(user):
    """
    Scope a Job queryset per AGENTS.md "Auth & job visibility": staff see
    everything, everyone else sees only their own jobs. Any new view over
    Job should build its queryset through this rather than Job.objects
    directly.
    """
    if user.is_staff:
        return Job.objects.all()
    return Job.objects.filter(uploaded_by=user)


@login_required
def upload(request):
    """
    Web UI upload entry point. Saves the file and records a pending Job
    via ingest.services.create_job_from_upload() -- the same
    job-creation logic the watcher uses (see ingest/services.py) -- then
    enqueues pipeline.run.run_pipeline(), which currently fails
    immediately since the pipeline stages are still stubs (see
    AGENTS.md "Current state").
    """
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            job = create_job_from_upload(form.cleaned_data["file"], owner=request.user)

            # TODO: below ~5 files, run pipeline.run_pipeline() synchronously
            # instead of enqueuing -- see PROJECT_SPEC.md "Processing Mode".
            queue = django_rq.get_queue("default")
            queue.enqueue("pipeline.run.run_pipeline", job.id)

            return redirect("job_status", job_id=job.id)
    else:
        form = UploadForm()

    return render(request, "ingest/upload.html", {"form": form})


@login_required
def job_list(request):
    jobs = _jobs_visible_to(request.user).order_by("-created_at")
    return render(request, "ingest/job_list.html", {"jobs": jobs})


@login_required
def job_status(request, job_id):
    job = get_object_or_404(_jobs_visible_to(request.user), id=job_id)
    return render(request, "ingest/job_status.html", {"job": job})
