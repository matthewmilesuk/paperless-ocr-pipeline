import io
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
from pdfminer.high_level import extract_text
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas as reportlab_canvas

from ingest.models import BorderlinePage, Job
from pipeline.cleanup import MEASUREMENT_DPI, cleanup
from pipeline.ocr import SYNC_PAGE_LIMIT, DocumentTooLongForSyncOCR, OcrResult, ocr_document
from pipeline.reassemble import (
    _PageGeometry,
    _text_draw_rotation,
    _visual_to_raw,
    reassemble,
)

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


def _make_ocr_result(pages_words):
    """
    Builds a synthetic OcrResult matching Document AI's real
    document["text"]/document["pages"][i]["tokens"] shape closely enough
    for reassemble() to consume, without a real API call.

    `pages_words`: one list per page, each a list of
    (word, nx0, ny0, nx1, ny1) tuples -- word text plus its normalized
    (top-left, bottom-right) box.
    """
    text_parts = []
    pages_data = []
    cursor = 0
    for words in pages_words:
        tokens = []
        for word, nx0, ny0, nx1, ny1 in words:
            start = cursor
            text_parts.append(word)
            cursor += len(word)
            end = cursor
            text_parts.append(" ")
            cursor += 1
            tokens.append(
                {
                    "layout": {
                        "text_anchor": {
                            "text_segments": [{"start_index": start, "end_index": end}]
                        },
                        "bounding_poly": {
                            "normalized_vertices": [
                                {"x": nx0, "y": ny0},
                                {"x": nx1, "y": ny0},
                                {"x": nx1, "y": ny1},
                                {"x": nx0, "y": ny1},
                            ]
                        },
                    }
                }
            )
        pages_data.append({"tokens": tokens})
    return OcrResult(
        document={"text": "".join(text_parts), "pages": pages_data},
        page_count=len(pages_words),
    )


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


class ReassembleTransformTests(TestCase):
    """
    Fixed-point regression checks for the rotation-aware coordinate
    transform (_visual_to_raw / _text_draw_rotation). These values are
    cross-checked against real PDF rasterization -- not just re-derived
    -- in ReassembleRotationRenderTests below; these are fast regression
    guards for the same formulas, not independent proof on their own.
    """

    def test_visual_top_left_corner_maps_to_the_correct_raw_corner(self):
        # Each PDF /Rotate value cycles which raw-page corner ends up at
        # the visual top-left when displayed. raw page is 300x400.
        expected_by_rotation = {
            0: (0.0, 400.0),
            90: (0.0, 0.0),
            180: (300.0, 0.0),
            270: (300.0, 400.0),
        }
        for rotation, (expected_x, expected_y) in expected_by_rotation.items():
            with self.subTest(rotation=rotation):
                geometry = _PageGeometry(raw_width=300, raw_height=400, rotation=rotation)
                x, y = _visual_to_raw(0.0, 0.0, geometry)
                self.assertAlmostEqual(x, expected_x)
                self.assertAlmostEqual(y, expected_y)

    def test_text_draw_rotation_cancels_page_rotation(self):
        self.assertEqual(_text_draw_rotation(0), 0)
        self.assertEqual(_text_draw_rotation(90), 270)
        self.assertEqual(_text_draw_rotation(180), 180)
        self.assertEqual(_text_draw_rotation(270), 90)


class ReassembleRotationRenderTests(TestCase):
    """
    Renders -- via real poppler rasterization, the same rendering path a
    PDF viewer uses -- where a token positioned at a known normalized
    coordinate actually lands after applying each PDF /Rotate value.
    This is the genuine visual-correctness check for rotation handling,
    as opposed to the transform re-checking itself.

    This confirms the geometry against poppler's rendering, NOT against
    a real Document AI response for an actually-rotated scan -- that
    would need a real, billable API call against a real rotated
    document, which hasn't been done. See pipeline/reassemble.py's
    module docstring for that caveat.

    Uses visible text (fill color, default Tr 0 render mode) purely for
    this test's own verification -- the real overlay code always uses
    invisible Tr 3. Render mode doesn't affect where reportlab positions
    a glyph, only whether ink gets painted, so this still exercises the
    real position/rotation logic (_visual_to_raw / _text_draw_rotation)
    completely unmodified.
    """

    RAW_SIZE = (300, 400)

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _render_darkest_quadrant(self, rotation, nx, ny):
        geometry = _PageGeometry(
            raw_width=self.RAW_SIZE[0], raw_height=self.RAW_SIZE[1], rotation=rotation
        )

        buf = io.BytesIO()
        c = reportlab_canvas.Canvas(buf, pagesize=self.RAW_SIZE)
        x, y = _visual_to_raw(nx, ny, geometry)
        c.saveState()
        c.translate(x, y)
        c.rotate(_text_draw_rotation(rotation))
        c.setFont("Helvetica", 60)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(0, 0, "Z")
        c.restoreState()
        c.showPage()
        c.save()

        with pikepdf.open(io.BytesIO(buf.getvalue())) as pdf:
            pdf.pages[0].rotation = rotation
            path = Path(self.tmp_dir) / f"rot{rotation}_{nx}_{ny}.pdf"
            pdf.save(str(path))

        image = convert_from_path(str(path), dpi=100)[0].convert("L")
        width, height = image.size
        quadrants = {
            "top-left": image.crop((0, 0, width // 2, height // 2)),
            "top-right": image.crop((width // 2, 0, width, height // 2)),
            "bottom-left": image.crop((0, height // 2, width // 2, height)),
            "bottom-right": image.crop((width // 2, height // 2, width, height)),
        }
        return min(
            quadrants,
            key=lambda name: sum(quadrants[name].getdata())
            / (quadrants[name].width * quadrants[name].height),
        )

    def test_token_near_visual_top_left_renders_top_left_regardless_of_rotation(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                self.assertEqual(self._render_darkest_quadrant(rotation, 0.15, 0.15), "top-left")

    def test_token_near_visual_bottom_right_renders_bottom_right_regardless_of_rotation(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                self.assertEqual(
                    self._render_darkest_quadrant(rotation, 0.85, 0.85), "bottom-right"
                )


class ReassembleTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _cleaned_pdf(self, page_count=1, size=(300, 400)):
        path = Path(self.tmp_dir) / "cleaned.pdf"
        _write_pdf(path, [Image.new("RGB", size, "white") for _ in range(page_count)])
        return path

    def test_overlay_text_is_extractable_on_unrotated_page(self):
        cleaned = self._cleaned_pdf(page_count=1)
        ocr_result = _make_ocr_result(
            [[("Hello", 0.1, 0.1, 0.3, 0.2), ("World", 0.4, 0.1, 0.6, 0.2)]]
        )
        output_path = Path(self.tmp_dir) / "reassembled.pdf"

        result_path = reassemble(cleaned, ocr_result, output_path)

        self.assertTrue(result_path.exists())
        with pikepdf.open(result_path) as pdf:
            self.assertEqual(len(pdf.pages), 1)

        extracted = extract_text(str(result_path))
        self.assertIn("Hello", extracted)
        self.assertIn("World", extracted)

    def test_page_order_preserved_across_multiple_pages(self):
        cleaned = self._cleaned_pdf(page_count=2)
        ocr_result = _make_ocr_result(
            [
                [("First", 0.1, 0.1, 0.3, 0.2)],
                [("Second", 0.1, 0.1, 0.3, 0.2)],
            ]
        )
        output_path = Path(self.tmp_dir) / "reassembled.pdf"

        reassemble(cleaned, ocr_result, output_path)

        with pikepdf.open(output_path) as pdf:
            self.assertEqual(len(pdf.pages), 2)

        page1_text = extract_text(str(output_path), page_numbers=[0])
        page2_text = extract_text(str(output_path), page_numbers=[1])
        self.assertIn("First", page1_text)
        self.assertNotIn("Second", page1_text)
        self.assertIn("Second", page2_text)
        self.assertNotIn("First", page2_text)

    def test_page_count_mismatch_raises(self):
        cleaned = self._cleaned_pdf(page_count=2)
        ocr_result = _make_ocr_result([[("Only", 0.1, 0.1, 0.3, 0.2)]])
        output_path = Path(self.tmp_dir) / "reassembled.pdf"

        with self.assertRaises(ValueError):
            reassemble(cleaned, ocr_result, output_path)
