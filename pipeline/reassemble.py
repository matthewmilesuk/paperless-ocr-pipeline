"""
Step 5: Reassemble + overlay.

Adds Document AI's recognized text as an invisible (searchable, but not
painted) text layer directly onto the cleaned PDF's own pages -- no
rasterized page images involved. Positioning uses Document AI's
normalized_vertices (fractions of the page, resolution-independent)
against each page's *actual* dimensions and rotation, read from the
cleaned PDF itself via pikepdf, never assumed from Document AI's own
(informational-only) Dimension field. See PROJECT_SPEC.md "Reassemble +
overlay" for why pipeline/split.py was removed and isn't needed here.

Rotation handling (PDF /Rotate) is the one piece of this module that's
geometrically derived and unit-tested (see pipeline/tests.py
ReassembleRotationTests) rather than verified against a real Document AI
response for an actually-rotated scan -- that would need a real,
billable API call against a real rotated document, which hasn't been
done. The geometry itself (see _visual_to_raw / _text_draw_rotation) is
correct for the *assumption* that Document AI reports coordinates
relative to the visually-displayed (post-rotation) page in standard
image convention (origin top-left, y down) -- a reasonable assumption
given how Document AI is documented to behave, but flagged here as an
assumption, not a confirmed fact, per AGENTS.md's request to be explicit
about this rather than silently asserting it's handled.

Also not handled: Document AI's per-element `orientation` field (text
Document AI itself detected as sideways/upside-down *within* an
otherwise-upright page, e.g. a rotated stamp) -- only page-level
/Rotate is accounted for. Out of scope for this pass.
"""
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import pikepdf
from reportlab.pdfgen import canvas as reportlab_canvas

from .ocr import OcrResult

logger = logging.getLogger(__name__)

# PDF text render mode 3 ("Tr 3"): text is present in the content stream
# for copy/paste and search, but neither filled nor stroked -- i.e.
# invisible. The same technique ocrmypdf/Tesseract-based OCR text layers
# use for exactly this purpose.
_INVISIBLE_TEXT_RENDER_MODE = 3
_DEFAULT_FONT = "Helvetica"


@dataclass
class _PageGeometry:
    raw_width: float
    raw_height: float
    rotation: int  # PDF /Rotate, clockwise degrees, normalized to [0, 360)


def _page_geometry(page: pikepdf.Page) -> _PageGeometry:
    """
    Reads `page`'s actual raw (unrotated, content-stream) size from its
    MediaBox and its effective /Rotate -- deliberately not taken from
    Document AI's own Dimension field, in case they ever differ.
    """
    media_box = page.mediabox
    return _PageGeometry(
        raw_width=float(media_box[2] - media_box[0]),
        raw_height=float(media_box[3] - media_box[1]),
        rotation=int(page.rotation) % 360,
    )


def _visual_dimensions(geometry: _PageGeometry) -> Tuple[float, float]:
    """(width, height) of the page as it's actually displayed, in points."""
    if geometry.rotation in (90, 270):
        return geometry.raw_height, geometry.raw_width
    return geometry.raw_width, geometry.raw_height


def _visual_to_raw(nx: float, ny: float, geometry: _PageGeometry) -> Tuple[float, float]:
    """
    Maps a Document AI normalized coordinate (nx, ny -- fractions of the
    *visually displayed* page, origin top-left, y down) to a point in
    the page's raw content-stream coordinate space (origin bottom-left,
    y up, in points) -- the space overlay content actually gets drawn
    in, since /Rotate is applied by the viewer on top of the raw content
    stream, not baked into it.

    Derived by tracing each MediaBox corner through a clockwise rotation
    by `geometry.rotation` degrees and solving for the inverse mapping;
    see pipeline/tests.py ReassembleRotationTests for the point-by-point
    verification this is checked against.
    """
    w, h = geometry.raw_width, geometry.raw_height
    rotation = geometry.rotation
    if rotation == 0:
        return nx * w, h - ny * h
    if rotation == 90:
        return ny * w, nx * h
    if rotation == 180:
        return (1 - nx) * w, ny * h
    if rotation == 270:
        return (1 - ny) * w, (1 - nx) * h
    raise ValueError(f"Unsupported page rotation: {rotation}")


def _text_draw_rotation(rotation: int) -> int:
    """
    Counter-clockwise degrees (reportlab canvas.rotate()'s convention)
    to draw text in raw content-stream space so it reads upright once
    the viewer applies the page's own clockwise /Rotate on top of it.
    """
    return (360 - rotation) % 360


def _token_text(document_text: str, text_anchor: dict) -> str:
    segments = text_anchor.get("text_segments") or []
    parts = []
    for segment in segments:
        start = int(segment.get("start_index", 0) or 0)
        end = int(segment.get("end_index", 0) or 0)
        parts.append(document_text[start:end])
    return "".join(parts)


def _normalized_vertices(bounding_poly: dict) -> List[Tuple[float, float]]:
    vertices = bounding_poly.get("normalized_vertices") or []
    return [(v.get("x", 0.0), v.get("y", 0.0)) for v in vertices]


def _draw_page_overlay(document_text: str, page_data: dict, geometry: _PageGeometry) -> bytes:
    """
    Renders one page's worth of invisible text tokens to a single-page
    PDF sized to match `geometry`'s *raw* (unrotated) page size, and
    returns the PDF bytes, ready to be overlaid via pikepdf.Page.add_overlay().
    """
    visual_w, visual_h = _visual_dimensions(geometry)
    draw_rotation = _text_draw_rotation(geometry.rotation)

    buffer = io.BytesIO()
    c = reportlab_canvas.Canvas(buffer, pagesize=(geometry.raw_width, geometry.raw_height))

    for token in page_data.get("tokens", []):
        layout = token.get("layout") or {}
        text = _token_text(document_text, layout.get("text_anchor") or {})
        if not text.strip():
            continue

        vertices = _normalized_vertices(layout.get("bounding_poly") or {})
        if len(vertices) < 4:
            continue

        # Document AI orders bounding_poly vertices clockwise starting
        # top-left: [top-left, top-right, bottom-right, bottom-left].
        (tl_x, tl_y), (tr_x, tr_y), (_br_x, _br_y), (bl_x, bl_y) = vertices[:4]

        box_height_pts = max((bl_y - tl_y) * visual_h, 1.0)
        box_width_pts = max((tr_x - tl_x) * visual_w, 1.0)
        font_size = box_height_pts

        origin_x, origin_y = _visual_to_raw(bl_x, bl_y, geometry)

        natural_width = c.stringWidth(text, _DEFAULT_FONT, font_size) or 1.0
        # Scale drawn text horizontally to roughly match the recognized
        # box width -- same approach ocrmypdf's own text-layer generation
        # uses. Invisible text doesn't need pixel-perfect glyph shapes,
        # just a selectable/searchable region in roughly the right place
        # and size; clamped to sane bounds against degenerate boxes.
        horiz_scale = max(1.0, min(500.0, 100.0 * box_width_pts / natural_width))

        c.saveState()
        c.translate(origin_x, origin_y)
        c.rotate(draw_rotation)
        text_obj = c.beginText(0, 0)
        text_obj.setTextRenderMode(_INVISIBLE_TEXT_RENDER_MODE)
        text_obj.setFont(_DEFAULT_FONT, font_size)
        text_obj.setHorizScale(horiz_scale)
        text_obj.textOut(text)
        c.drawText(text_obj)
        c.restoreState()

    c.showPage()
    c.save()
    return buffer.getvalue()


def reassemble(cleaned_path: Path, ocr_result: OcrResult, output_path: Path) -> Path:
    """
    Overlay `ocr_result`'s recognized text onto `cleaned_path`'s own
    pages (invisible, searchable layer) and save the result to
    `output_path`, preserving original page order -- the pages
    themselves are cleaned_path's, untouched; only text is added.
    """
    document_text = ocr_result.document.get("text", "")
    pages_data = ocr_result.document.get("pages", [])

    with pikepdf.open(str(cleaned_path)) as pdf:
        if len(pdf.pages) != len(pages_data):
            logger.error(
                "[reassemble] page count mismatch: cleaned PDF %s has %d "
                "pages, OCR result has %d",
                cleaned_path,
                len(pdf.pages),
                len(pages_data),
            )
            raise ValueError(
                f"Page count mismatch: {cleaned_path} has {len(pdf.pages)} "
                f"pages, OCR result has {len(pages_data)} pages."
            )

        for index, page in enumerate(pdf.pages):
            geometry = _page_geometry(page)
            overlay_bytes = _draw_page_overlay(document_text, pages_data[index], geometry)

            with pikepdf.open(io.BytesIO(overlay_bytes)) as overlay_pdf:
                page.add_overlay(overlay_pdf.pages[0])

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(str(output_path))

    return output_path
