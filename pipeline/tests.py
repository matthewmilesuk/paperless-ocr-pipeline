import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pikepdf
from django.conf import settings
from django.test import TestCase, override_settings
from google.api_core import exceptions as google_exceptions
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import documentai
from pdf2image import convert_from_path
from PIL import Image, ImageDraw

from ingest.models import BorderlinePage, Job
from pipeline.cleanup import MEASUREMENT_DPI, cleanup
from pipeline.ocr import SYNC_PAGE_LIMIT, DocumentTooLongForSyncOCR, ocr_document

PAGE_SIZE = (400, 500)  # px, arbitrary -- only the coverage ratio matters
PAGE_AREA = PAGE_SIZE[0] * PAGE_SIZE[1]


def _blank_page():
    return Image.new("RGB", PAGE_SIZE, color="white")


def _page_with_mark(coverage_fraction):
    """
    A white page with a black square sized to cover roughly
    `coverage_fraction` of the page area.
    """
    image = Image.new("RGB", PAGE_SIZE, color="white")
    side = round((coverage_fraction * PAGE_AREA) ** 0.5)
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 10 + side, 10 + side), fill=0)
    return image


def _write_pdf(path, pages):
    """
    Save `pages` (PIL Images) as a multi-page PDF at `path`, with the PDF's
    resolution metadata matching MEASUREMENT_DPI. cleanup() rasterizes at
    MEASUREMENT_DPI to measure ink coverage -- matching resolutions here
    means poppler renders back the same pixel grid we drew, instead of
    resampling, so coverage percentages come out exact rather than fuzzed
    by interpolation at page edges.
    """
    first, rest = pages[0], pages[1:]
    first.save(path, "PDF", save_all=True, append_images=rest, resolution=MEASUREMENT_DPI)


class CleanupTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        override = override_settings(SCAN_OUTPUT_DIR=self.tmp_dir)
        override.enable()
        self.addCleanup(override.disable)

    def _make_job(self, input_pdf):
        return Job.objects.create(
            original_filename=input_pdf.name,
            source=Job.Source.WATCHER,
            input_path=str(input_pdf),
        )

    def test_confidently_blank_page_is_dropped(self):
        # A blank page next to a clearly-inked one: only the blank one
        # should disappear from the output, proving drop and page-order
        # preservation together.
        input_pdf = Path(self.tmp_dir) / "input.pdf"
        _write_pdf(input_pdf, [_blank_page(), _page_with_mark(0.25)])
        job = self._make_job(input_pdf)

        result = cleanup(input_pdf, job.id)

        self.assertEqual(result.pages_total, 2)
        self.assertEqual(result.pages_dropped, 1)
        self.assertEqual(result.pages_borderline, 0)
        self.assertEqual(BorderlinePage.objects.count(), 0)

        with pikepdf.open(result.output_path) as output_pdf:
            self.assertEqual(len(output_pdf.pages), 1)

    def test_non_blank_page_is_kept_untouched(self):
        input_pdf = Path(self.tmp_dir) / "input.pdf"
        _write_pdf(input_pdf, [_page_with_mark(0.25)])
        job = self._make_job(input_pdf)

        result = cleanup(input_pdf, job.id)

        self.assertEqual(result.pages_total, 1)
        self.assertEqual(result.pages_dropped, 0)
        self.assertEqual(result.pages_borderline, 0)
        self.assertEqual(BorderlinePage.objects.count(), 0)

        with pikepdf.open(result.output_path) as output_pdf:
            self.assertEqual(len(output_pdf.pages), 1)

    def test_borderline_page_is_kept_and_logged(self):
        # 1.5% ink coverage sits comfortably between the default drop
        # (0.5%) and review (3.0%) thresholds.
        input_pdf = Path(self.tmp_dir) / "input.pdf"
        _write_pdf(input_pdf, [_page_with_mark(0.015)])
        job = self._make_job(input_pdf)

        result = cleanup(input_pdf, job.id)

        self.assertEqual(result.pages_total, 1)
        self.assertEqual(result.pages_dropped, 0)
        self.assertEqual(result.pages_borderline, 1)

        with pikepdf.open(result.output_path) as output_pdf:
            self.assertEqual(len(output_pdf.pages), 1)

        record = BorderlinePage.objects.get()
        self.assertEqual(record.job, job)
        self.assertEqual(record.page_number, 1)
        self.assertGreaterEqual(
            record.ink_coverage_percent, settings.BLANK_PAGE_DROP_THRESHOLD_PCT
        )
        self.assertLess(
            record.ink_coverage_percent, settings.BLANK_PAGE_REVIEW_THRESHOLD_PCT
        )
        self.assertAlmostEqual(record.ink_coverage_percent, 1.5, delta=0.5)

    def test_page_order_preserved_after_drop(self):
        # Two visually distinct non-blank pages either side of a blank
        # one -- confirms the surviving pages keep their original order
        # rather than just their original count.
        input_pdf = Path(self.tmp_dir) / "input.pdf"
        _write_pdf(
            input_pdf,
            [_page_with_mark(0.10), _blank_page(), _page_with_mark(0.40)],
        )
        job = self._make_job(input_pdf)

        result = cleanup(input_pdf, job.id)

        self.assertEqual(result.pages_total, 3)
        self.assertEqual(result.pages_dropped, 1)

        rendered = convert_from_path(str(result.output_path), dpi=MEASUREMENT_DPI)
        self.assertEqual(len(rendered), 2)

        def coverage(image):
            grayscale = image.convert("L")
            histogram = grayscale.histogram()
            return 100.0 * sum(histogram[:251]) / (grayscale.width * grayscale.height)

        self.assertAlmostEqual(coverage(rendered[0]), 10.0, delta=2.0)
        self.assertAlmostEqual(coverage(rendered[1]), 40.0, delta=2.0)


class OcrDocumentTests(TestCase):
    """
    ocr_document() against a mocked Document AI client -- no real API
    calls here or anywhere else in the automatic test suite. See
    scripts/smoke-test-ocr.py for the one deliberate, opt-in, real call.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _pdf_with_pages(self, count):
        path = Path(self.tmp_dir) / "input.pdf"
        _write_pdf(path, [_blank_page() for _ in range(count)])
        return path

    @mock.patch("pipeline.ocr._client")
    def test_normal_document_processes_with_mocked_response(self, mock_client_factory):
        fake_document = documentai.Document(
            text="hello world",
            pages=[
                documentai.Document.Page(page_number=1),
                documentai.Document.Page(page_number=2),
            ],
        )
        mock_client_factory.return_value.process_document.return_value = (
            documentai.ProcessResponse(document=fake_document)
        )

        input_pdf = self._pdf_with_pages(2)
        result = ocr_document(input_pdf, job_id=1)

        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.document["text"], "hello world")
        self.assertEqual(len(result.document["pages"]), 2)

        mock_client_factory.return_value.process_document.assert_called_once()
        request = mock_client_factory.return_value.process_document.call_args.kwargs["request"]
        self.assertEqual(
            request.name,
            f"projects/{settings.GCP_PROJECT_ID}"
            f"/locations/{settings.GCP_DOCAI_LOCATION}"
            f"/processors/{settings.GCP_DOCAI_PROCESSOR_ID}",
        )
        self.assertEqual(request.raw_document.mime_type, "application/pdf")

    @mock.patch("pipeline.ocr._client")
    def test_document_over_page_limit_raises_before_api_call(self, mock_client_factory):
        input_pdf = self._pdf_with_pages(SYNC_PAGE_LIMIT + 1)

        with self.assertRaises(DocumentTooLongForSyncOCR):
            ocr_document(input_pdf, job_id=2)

        mock_client_factory.assert_not_called()

    @mock.patch("pipeline.ocr._client")
    def test_auth_failure_logged_and_reraised(self, mock_client_factory):
        mock_client_factory.return_value.process_document.side_effect = (
            google_exceptions.Unauthenticated("bad credentials")
        )
        input_pdf = self._pdf_with_pages(1)

        with self.assertLogs("pipeline.ocr", level="ERROR") as logs:
            with self.assertRaises(google_exceptions.Unauthenticated):
                ocr_document(input_pdf, job_id=3)

        self.assertTrue(any("rejected the credentials" in message for message in logs.output))

    @mock.patch("pipeline.ocr._client")
    def test_missing_credentials_logged_and_reraised(self, mock_client_factory):
        mock_client_factory.return_value.process_document.side_effect = (
            DefaultCredentialsError("no credentials found")
        )
        input_pdf = self._pdf_with_pages(1)

        with self.assertLogs("pipeline.ocr", level="ERROR") as logs:
            with self.assertRaises(DefaultCredentialsError):
                ocr_document(input_pdf, job_id=4)

        self.assertTrue(any("credentials not found" in message for message in logs.output))

    @mock.patch("pipeline.ocr._client")
    def test_quota_exceeded_logged_and_reraised(self, mock_client_factory):
        mock_client_factory.return_value.process_document.side_effect = (
            google_exceptions.ResourceExhausted("quota exceeded")
        )
        input_pdf = self._pdf_with_pages(1)

        with self.assertLogs("pipeline.ocr", level="ERROR") as logs:
            with self.assertRaises(google_exceptions.ResourceExhausted):
                ocr_document(input_pdf, job_id=5)

        self.assertTrue(any("quota/rate limit" in message for message in logs.output))

    @mock.patch("pipeline.ocr._client")
    def test_processor_not_found_logged_and_reraised(self, mock_client_factory):
        mock_client_factory.return_value.process_document.side_effect = (
            google_exceptions.NotFound("processor not found")
        )
        input_pdf = self._pdf_with_pages(1)

        with self.assertLogs("pipeline.ocr", level="ERROR") as logs:
            with self.assertRaises(google_exceptions.NotFound):
                ocr_document(input_pdf, job_id=6)

        self.assertTrue(any("processor not found" in message for message in logs.output))
