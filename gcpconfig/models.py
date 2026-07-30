from django.conf import settings
from django.db import models


class Configuration(models.Model):
    """
    GCP / Document AI configuration set via the admin-only setup wizard
    (gcpconfig/views.py). Singleton by convention -- always written via
    Configuration.objects.update_or_create(pk=1, ...) -- rather than
    enforced at the DB level, since a single small app with one obvious
    write path doesn't need a dedicated singleton-model library for this.

    pipeline/ocr.py reads this (via get_configuration()) fresh on every
    call and prefers it over settings.py/.env when a row exists -- see
    pipeline/ocr.py's _effective_config() and AGENTS.md "Local
    development" for why plain Django settings can't just be updated at
    runtime here (they're read once at process boot, and the actual
    OCR call happens in a separate `worker` container/process from
    wherever this wizard runs, so nothing short of the shared database
    crosses that boundary).
    """

    gcp_project_id = models.CharField(max_length=255)
    gcp_docai_processor_id = models.CharField(max_length=255)
    # Path to the credentials JSON file on disk (see
    # gcpconfig/storage.py) -- not the file's contents. Kept as a real
    # file, not a DB blob, so pipeline/ocr.py can load it the same way
    # (google.auth.load_credentials_from_file()) regardless of whether
    # the path came from the wizard or from GOOGLE_APPLICATION_CREDENTIALS.
    gcp_credentials_path = models.CharField(max_length=1024)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Configuration(project={self.gcp_project_id}, processor={self.gcp_docai_processor_id})"


def get_configuration():
    """The one Configuration row, or None if the wizard hasn't been used."""
    return Configuration.objects.first()


def is_configured():
    """
    True if GCP/Document AI is usable right now -- either via a saved
    Configuration row (the wizard) or via settings.py/.env (the original
    manual setup path). Used to gate the wizard-redirect middleware and
    the upload view's pre-flight check; NOT used by pipeline/ocr.py
    itself, which needs the actual effective values, not just a yes/no
    (see pipeline/ocr.py's _effective_config()).
    """
    if Configuration.objects.exists():
        return True
    return bool(
        settings.GCP_PROJECT_ID
        and settings.GCP_DOCAI_PROCESSOR_ID
        and settings.GOOGLE_APPLICATION_CREDENTIALS
    )
