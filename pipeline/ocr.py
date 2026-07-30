"""
Step 4: OCR -- Google Document AI.

Uses the Enterprise Document OCR processor (settings.GCP_DOCAI_PROCESSOR_ID)
to extract text + layout position per page, returned as hOCR (see
PROJECT_SPEC.md "OCR - Google Document AI").
"""
from typing import List

from PIL.Image import Image


def ocr_pages(page_images: List[Image]) -> List[str]:
    """
    Send each page image to Google Document AI and return a list of
    hOCR documents, one per page, in the same order as `page_images`.

    TODO:
      - Build a documentai.DocumentProcessorServiceClient using
        settings.GOOGLE_APPLICATION_CREDENTIALS.
      - Call client.process_document() against
        projects/{GCP_PROJECT_ID}/locations/{GCP_DOCAI_LOCATION}/processors/{GCP_DOCAI_PROCESSOR_ID}
        for each page image.
      - Convert the Document AI response into hOCR for the reassemble
        step to overlay.
    """
    raise NotImplementedError
