"""
Step 4: OCR -- Google Document AI.

Sends the cleaned PDF to the Enterprise Document OCR processor via
Document AI's *synchronous* process endpoint -- one API call for the
whole document, not one call per page. Document AI handles page
segmentation internally and returns structured per-page text + layout
(bounding boxes, blocks, lines, tokens) in a single response (see
PROJECT_SPEC.md "OCR - Google Document AI").

The synchronous endpoint caps input at SYNC_PAGE_LIMIT pages. Documents
over that limit raise DocumentTooLongForSyncOCR before any API call is
attempted -- batch processing (via Cloud Storage, up to 500 pages) would
handle those, but that's a known future enhancement, not built yet (see
PROJECT_SPEC.md "Things Deliberately Decided Against").

Configuration (project ID, processor ID, credentials) comes from the
gcpconfig app's Configuration model if a row exists there (set via the
admin-only setup wizard), falling back to settings.py/.env values
otherwise -- see _effective_config(). This is what lets the wizard take
effect without a process restart: values are re-resolved from the
database on every call, not read once at import time the way plain
Django settings are.
"""
import logging
from dataclasses import dataclass
from pathlib import Path

import google.auth
import pikepdf
from django.conf import settings
from google.api_core import exceptions as google_exceptions
from google.api_core.client_options import ClientOptions
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import documentai

logger = logging.getLogger(__name__)

# Document AI's synchronous process endpoint caps input at 15 pages (30
# with imageless_mode, which trades off some accuracy for the higher
# limit -- not enabled here). Documents over this need batch processing
# instead of a hard failure or silent truncation.
SYNC_PAGE_LIMIT = 15


class DocumentTooLongForSyncOCR(Exception):
    """Raised when a document exceeds Document AI's synchronous page limit."""


@dataclass
class OcrResult:
    """
    Result of ocr_document(): the full Document AI response, serialized,
    plus a convenience page count.

    `document` is documentai.Document.to_dict(response.document) --
    nothing discarded from what Document AI returned. In particular,
    document["text"] is the full extracted text and document["pages"] is
    a list of per-page dicts carrying layout/bounding-box data (page
    dimensions, blocks/paragraphs/lines/tokens with bounding polys and
    text anchors into document["text"]) -- what reassemble.py will need
    later to overlay text at the right position on each page image.
    """

    document: dict
    page_count: int


@dataclass
class _EffectiveGcpConfig:
    project_id: str
    processor_id: str
    credentials_path: str  # "" if nothing explicit is configured anywhere


def _effective_config() -> _EffectiveGcpConfig:
    """
    gcpconfig.models.Configuration wins if a row exists (set via the
    setup wizard); falls back to settings.py/.env values otherwise, so
    existing manual .env-only setups keep working unchanged. Imported
    lazily -- matches how pipeline/*.py already lazily imports
    ingest.models inside functions rather than at module top level, to
    avoid an import-time coupling between apps.
    """
    from gcpconfig.models import get_configuration

    config = get_configuration()
    if config is not None:
        return _EffectiveGcpConfig(
            project_id=config.gcp_project_id,
            processor_id=config.gcp_docai_processor_id,
            credentials_path=config.gcp_credentials_path,
        )
    return _EffectiveGcpConfig(
        project_id=settings.GCP_PROJECT_ID,
        processor_id=settings.GCP_DOCAI_PROCESSOR_ID,
        credentials_path="",
    )


def _build_processor_name(project_id: str, location: str, processor_id: str) -> str:
    return f"projects/{project_id}/locations/{location}/processors/{processor_id}"


def _build_client(credentials_path: str) -> documentai.DocumentProcessorServiceClient:
    """
    Builds a Document AI client for settings.GCP_DOCAI_LOCATION (always
    from settings -- not part of the wizard's 3 configurable values).

    If `credentials_path` is given, credentials are loaded explicitly
    from that file via google.auth.load_credentials_from_file() rather
    than relying on the ambient GOOGLE_APPLICATION_CREDENTIALS
    environment variable -- this is what lets a DB-configured
    credentials path (written by the setup wizard, potentially in a
    different OS process than whatever last read the environment) take
    effect correctly. If `credentials_path` is empty, falls back to
    today's original behaviour: no explicit credentials are passed, and
    the underlying google-auth library resolves them ambiently via
    google.auth.default() (which reads GOOGLE_APPLICATION_CREDENTIALS
    itself) -- unchanged for existing manual .env-only setups.
    """
    client_options = ClientOptions(
        api_endpoint=f"{settings.GCP_DOCAI_LOCATION}-documentai.googleapis.com"
    )
    if credentials_path:
        credentials, _ = google.auth.load_credentials_from_file(credentials_path)
        return documentai.DocumentProcessorServiceClient(
            credentials=credentials, client_options=client_options
        )
    return documentai.DocumentProcessorServiceClient(client_options=client_options)


def _processor_name() -> str:
    effective = _effective_config()
    return _build_processor_name(
        effective.project_id, settings.GCP_DOCAI_LOCATION, effective.processor_id
    )


def _client() -> documentai.DocumentProcessorServiceClient:
    effective = _effective_config()
    return _build_client(effective.credentials_path)


def describe_document_ai_error(exc: Exception) -> str:
    """
    Maps a Document AI-related exception to a clear, distinguishable,
    human-readable diagnosis. Shared between ocr_document()'s own error
    logging below and gcpconfig's setup-wizard validation
    (gcpconfig/validation.py), so both surface the same specific
    diagnosis -- bad credentials vs. wrong processor ID vs. missing IAM
    role, etc. -- rather than the wizard reinventing its own separate
    (and possibly inconsistent) error messages.

    Order matters: google_exceptions.Unauthenticated/PermissionDenied/
    ResourceExhausted/NotFound are all subclasses of GoogleAPICallError,
    so the specific cases must be checked before the general one.
    """
    if isinstance(exc, DefaultCredentialsError):
        return (
            "Document AI credentials not found or invalid -- check the "
            f"service account key file: {exc}"
        )
    if isinstance(exc, google_exceptions.Unauthenticated):
        return (
            "Document AI rejected the credentials used -- check the "
            f"service account key is valid and not revoked: {exc}"
        )
    if isinstance(exc, google_exceptions.PermissionDenied):
        return (
            "Document AI permission denied -- check the service account "
            f"has roles/documentai.apiUser on the project: {exc}"
        )
    if isinstance(exc, google_exceptions.ResourceExhausted):
        return f"Document AI quota/rate limit exceeded: {exc}"
    if isinstance(exc, google_exceptions.NotFound):
        return (
            "Document AI processor not found -- check the project ID, "
            f"location, and processor ID: {exc}"
        )
    if isinstance(exc, google_exceptions.GoogleAPICallError):
        return f"Document AI call failed: {exc}"
    return f"Unexpected error calling Document AI: {exc}"


def ocr_document(cleaned_path: Path, job_id: int) -> OcrResult:
    """
    Send `cleaned_path` to Document AI's synchronous process endpoint and
    return the structured result.

    Raises DocumentTooLongForSyncOCR (without calling the API at all) if
    the document exceeds SYNC_PAGE_LIMIT pages.
    """
    with pikepdf.open(str(cleaned_path)) as pdf:
        page_count = len(pdf.pages)

    if page_count > SYNC_PAGE_LIMIT:
        logger.error(
            "[ocr] job=%s document has %d pages, exceeds the %d-page "
            "synchronous Document AI limit -- needs batch processing "
            "(not yet implemented, see PROJECT_SPEC.md). Not calling the API.",
            job_id,
            page_count,
            SYNC_PAGE_LIMIT,
        )
        raise DocumentTooLongForSyncOCR(
            f"Job {job_id}: {page_count} pages exceeds the "
            f"{SYNC_PAGE_LIMIT}-page synchronous Document AI limit; "
            "batch processing is required but not yet implemented."
        )

    request = documentai.ProcessRequest(
        name=_processor_name(),
        raw_document=documentai.RawDocument(
            content=Path(cleaned_path).read_bytes(),
            mime_type="application/pdf",
        ),
    )

    try:
        response = _client().process_document(request=request)
    except (
        DefaultCredentialsError,
        google_exceptions.Unauthenticated,
        google_exceptions.PermissionDenied,
        google_exceptions.ResourceExhausted,
        google_exceptions.NotFound,
        google_exceptions.GoogleAPICallError,
    ) as exc:
        logger.error("[ocr] job=%s %s", job_id, describe_document_ai_error(exc))
        raise

    return OcrResult(
        document=documentai.Document.to_dict(response.document),
        page_count=page_count,
    )
