"""
Where the setup wizard's uploaded service account key actually lands on
disk. Writes to a temp file first, validates it (gcpconfig/validation.py,
called from the view -- not here), and only promotes it to the real path
via an atomic rename once validation passes -- so a failed wizard
submission can never clobber a previously-working credentials file with
an unvalidated one.
"""
import os
from pathlib import Path

from django.conf import settings

CREDENTIALS_FILENAME = "gcp-credentials.json"


def _upload_dir() -> Path:
    upload_dir = Path(settings.GCP_CREDENTIALS_UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def write_temp_credentials_file(uploaded_file) -> Path:
    """Writes an uploaded Django file to a temp path in the same
    directory the real credentials file lives in (so the later promote
    step is an atomic same-filesystem rename), with 0600 permissions."""
    tmp_path = _upload_dir() / f".{CREDENTIALS_FILENAME}.tmp-{os.getpid()}"
    with open(tmp_path, "wb") as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)
    tmp_path.chmod(0o600)
    return tmp_path


def promote_credentials_file(tmp_path: Path) -> Path:
    """Atomically moves a validated temp credentials file into place."""
    final_path = _upload_dir() / CREDENTIALS_FILENAME
    tmp_path.rename(final_path)
    return final_path


def discard_temp_credentials_file(tmp_path: Path) -> None:
    """Removes a temp credentials file that failed validation."""
    tmp_path.unlink(missing_ok=True)
