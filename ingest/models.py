from django.db import models


class Job(models.Model):
    """A single pipeline run for one input PDF (upload or watcher-triggered)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    class Source(models.TextChoices):
        UPLOAD = "upload", "Web upload"
        WATCHER = "watcher", "Samba watcher"

    original_filename = models.CharField(max_length=255)
    source = models.CharField(max_length=16, choices=Source.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    input_path = models.CharField(max_length=1024)
    output_path = models.CharField(max_length=1024, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Job({self.original_filename}, {self.status})"


class BorderlinePage(models.Model):
    """
    A page flagged during the cleanup pass as borderline-blank
    (ink coverage between the drop and review thresholds).

    Kept in the output document by default; logged here for manual
    review rather than silently dropped, per PROJECT_SPEC.md.
    """

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="borderline_pages")
    page_number = models.PositiveIntegerField()
    ink_coverage_percent = models.FloatField()
    reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Page {self.page_number} of {self.job.original_filename} ({self.ink_coverage_percent}%)"
