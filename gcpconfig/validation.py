"""
Live validation for the setup wizard: one cheap Document AI call
(get_processor -- metadata only, no OCR processing or per-page billing)
to confirm a submitted project ID + processor ID + credentials file
actually work together, before gcpconfig.models.Configuration is ever
saved. Reuses pipeline.ocr's exception-to-message mapping so the wizard
surfaces the same specific diagnosis (bad credentials, wrong processor
ID, missing IAM role, etc.) ocr.py itself would log for the same
failure, rather than a separately-invented, possibly-inconsistent
message.
"""
import logging

from django.conf import settings

from pipeline.ocr import _build_client, _build_processor_name, describe_document_ai_error

logger = logging.getLogger(__name__)


def validate_configuration(project_id: str, processor_id: str, credentials_path: str):
    """
    Returns (True, "") if project_id/processor_id/credentials_path work
    together against the real Document AI API, or (False, message) with
    a clear, specific reason otherwise. Never raises -- any failure from
    building the client or calling the API is caught and turned into the
    (False, message) result.
    """
    processor_name = _build_processor_name(
        project_id, settings.GCP_DOCAI_LOCATION, processor_id
    )
    try:
        client = _build_client(credentials_path)
        client.get_processor(name=processor_name)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
        # failure here (bad credentials file, network error, wrong IDs,
        # ...) must produce a wizard-displayable message, not a 500.
        message = describe_document_ai_error(exc)
        logger.error("[gcpconfig] validation failed: %s", message)
        return False, message

    logger.info(
        "[gcpconfig] validation succeeded for project=%s processor=%s",
        project_id,
        processor_id,
    )
    return True, ""
