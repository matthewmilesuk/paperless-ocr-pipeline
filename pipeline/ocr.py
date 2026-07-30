"""
Step 4: OCR -- Google Document AI.

Sends the cleaned PDF to the Enterprise Document OCR processor
(settings.GCP_DOCAI_PROCESSOR_ID) via Document AI's *synchronous*
process endpoint -- one API call for the whole document, not one call
per page. Document AI handles page segmentation internally and returns
structured per-page text + layout (bounding boxes, blocks, lines,
tokens) in a single response (see PROJECT_SPEC.md "OCR - Google Document
AI").

The synchronous endpoint caps input at SYNC_PAGE_LIMIT pages. Documents
over that limit raise DocumentTooLongForSyncOCR before any API call is
attempted -- batch processing (via Cloud Storage, up to 500 pages) would
handle those, but that's a known future enhancement, not built yet (see
PROJECT_SPEC.md "Things Deliberately Decided Against").
"""
import logging
from dataclasses import dataclass
from pathlib import Path

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


def _processor_name() -> str:
    return (
        f"projects/{settings.GCP_PROJECT_ID}"
        f"/locations/{settings.GCP_DOCAI_LOCATION}"
        f"/processors/{settings.GCP_DOCAI_PROCESSOR_ID}"
    )


def _client() -> documentai.DocumentProcessorServiceClient:
    # Document AI requires a location-specific endpoint for any location
    # other than "global" -- the default endpoint won't resolve a
    # location="us" processor. Credentials are picked up automatically
    # from GOOGLE_APPLICATION_CREDENTIALS via Application Default
    # Credentials; nothing is passed explicitly here.
    return documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(
            api_endpoint=f"{settings.GCP_DOCAI_LOCATION}-documentai.googleapis.com"
        )
    )


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
    except DefaultCredentialsError as exc:
        logger.error(
            "[ocr] job=%s Document AI credentials not found/invalid "
            "(check GOOGLE_APPLICATION_CREDENTIALS): %s",
            job_id,
            exc,
        )
        raise
    except google_exceptions.Unauthenticated as exc:
        logger.error(
            "[ocr] job=%s Document AI rejected the credentials used "
            "(check the service account key is valid and not revoked): %s",
            job_id,
            exc,
        )
        raise
    except google_exceptions.PermissionDenied as exc:
        logger.error(
            "[ocr] job=%s Document AI denied permission (check the "
            "service account has roles/documentai.apiUser on the "
            "project): %s",
            job_id,
            exc,
        )
        raise
    except google_exceptions.ResourceExhausted as exc:
        logger.error(
            "[ocr] job=%s Document AI quota/rate limit exceeded: %s",
            job_id,
            exc,
        )
        raise
    except google_exceptions.NotFound as exc:
        logger.error(
            "[ocr] job=%s Document AI processor not found (check "
            "GCP_DOCAI_PROCESSOR_ID=%r and GCP_DOCAI_LOCATION=%r): %s",
            job_id,
            settings.GCP_DOCAI_PROCESSOR_ID,
            settings.GCP_DOCAI_LOCATION,
            exc,
        )
        raise
    except google_exceptions.GoogleAPICallError as exc:
        logger.error("[ocr] job=%s Document AI call failed: %s", job_id, exc)
        raise

    return OcrResult(
        document=documentai.Document.to_dict(response.document),
        page_count=page_count,
    )
