"""
Step 3: Orient (deskew + auto-rotate).

Runs BEFORE Document AI ever sees the document -- see PROJECT_SPEC.md
"Decisions Changed" for why. ocrmypdf's --deskew/--rotate-pages skip ALL
per-page processing (not just OCR) on pages that already have a text
layer -- confirmed by reading ocrmypdf's own source (is_ocr_required()
in ocrmypdf/_pipeline.py returns False for pages with existing text
under skip mode, and process_page() -- where deskew/rotate-pages
detection actually happens -- is never called for such pages). Since
reassemble.py stamps every page with Document AI's text layer, running
--deskew/--rotate-pages after that point would be a silent no-op on
every page, not just a misalignment risk.

Uses ocrmypdf itself (via its Python API) to do the actual deskew/
rotate-pages work -- the same well-tested machinery pdfa.py already
depends on. This has a side effect: --rotate-pages needs a text-
orientation signal, so ocrmypdf runs its own OCR engine (tesseract)
internally to get one, and embeds a throwaway tesseract-generated
invisible text layer as part of that process. That's not what we want
in the final document -- Document AI's text layer (added later by
reassemble.py) is -- so it's stripped immediately after orienting,
before this stage hands off to ocr.py.

IMPORTANT, verified concretely (not just trusted from docs): ocrmypdf's
own `--mode strip` / ProcessingMode.strip_text does NOT remove this
throwaway text layer. Both of ocrmypdf's renderers (fpdf2, the default,
and sandwich) wrap their OCR text in a Form XObject (named
"/OCR-<random>", invoked via a `Do` operator in the page's own content
stream) rather than placing it directly in the page's content stream.
ocrmypdf's built-in strip_invisible_text() (ocrmypdf/_graft.py) only
scans the page's own content stream for Tr-3 (invisible) text -- it
does not recurse into Form XObjects, so on real ocrmypdf output it finds
nothing to remove and silently no-ops (exit code 0, page unchanged).
Confirmed by inspecting real ocrmypdf output's actual PDF structure and
content streams, and by independently checking afterward (a separate
scan, not just re-running the same stripping code) that text was really
gone. _strip_stream() below reimplements ocrmypdf's own algorithm,
generalized to recurse into Form XObjects -- this is necessarily coupled
to ocrmypdf's current output structure (confirmed against the pinned
17.8.1) and may need revisiting if a future ocrmypdf version changes how
it embeds OCR text.
"""
import logging
from pathlib import Path

import ocrmypdf
import pikepdf
from django.conf import settings
from ocrmypdf.exceptions import (
    EncryptedPdfError,
    MissingDependencyError,
    PriorOcrFoundError,
    SubprocessOutputError,
)
from pikepdf import Name, Operator

logger = logging.getLogger(__name__)


def _strip_stream(page_or_stream, resources) -> bytes:
    """
    Removes Tr-3 (invisible) text BT...ET blocks from `page_or_stream`'s
    own content stream, recursing into any Form XObjects it invokes via
    `Do`. Same algorithm as ocrmypdf's own strip_invisible_text()
    (ocrmypdf/_graft.py), generalized to handle nested XObjects -- see
    module docstring for why that's needed.
    """
    stream = []
    in_text_obj = False
    render_mode = 0
    render_mode_stack = []
    text_objects = []

    for operands, operator in pikepdf.parse_content_stream(page_or_stream):
        if operator == Operator("Tr"):
            render_mode = int(operands[0])
        if operator == Operator("q"):
            render_mode_stack.append(render_mode)
        if operator == Operator("Q") and render_mode_stack:
            render_mode = render_mode_stack.pop()
        if operator == Operator("Do") and resources is not None:
            xobj_name = operands[0]
            xobjects = resources.get(Name.XObject, {})
            if xobj_name in xobjects:
                xobj = xobjects[xobj_name]
                if xobj.get(Name.Subtype) == Name.Form:
                    xobj.write(_strip_stream(xobj, xobj.get(Name.Resources)))

        if not in_text_obj:
            if operator == Operator("BT"):
                in_text_obj = True
                text_objects.append((operands, operator))
            else:
                stream.append((operands, operator))
        else:
            text_objects.append((operands, operator))
            if operator == Operator("ET"):
                in_text_obj = False
                if render_mode != 3:
                    stream.extend(text_objects)
                text_objects.clear()

    return pikepdf.unparse_content_stream(stream)


def _strip_throwaway_ocr_text(pdf_path: Path) -> None:
    """Strips ocrmypdf's own throwaway invisible text layer, in place."""
    with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
        for page in pdf.pages:
            page.Contents = pdf.make_stream(_strip_stream(page, page.get(Name.Resources)))
        pdf.save(str(pdf_path))


def orient(cleaned_path: Path, job_id: int) -> Path:
    """
    Deskews and auto-rotates `cleaned_path` (via ocrmypdf) before
    Document AI ever sees it, then strips the throwaway tesseract text
    layer ocrmypdf's rotation detection embeds as a side effect. Returns
    the path to the oriented, text-free PDF.
    """
    output_path = Path(settings.SCAN_OUTPUT_DIR) / f"{job_id}_oriented.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        ocrmypdf.ocr(
            str(cleaned_path),
            str(output_path),
            deskew=True,
            rotate_pages=True,
            output_type="pdf",
            optimize=0,
        )
    except MissingDependencyError as exc:
        logger.error(
            "[orient] job=%s missing dependency for deskew/rotate (is "
            "tesseract installed?): %s",
            job_id,
            exc,
        )
        raise
    except PriorOcrFoundError as exc:
        logger.error(
            "[orient] job=%s input already has a text layer -- orient must "
            "run before any OCR text exists on the page: %s",
            job_id,
            exc,
        )
        raise
    except EncryptedPdfError as exc:
        logger.error(
            "[orient] job=%s input PDF is encrypted, cannot process: %s",
            job_id,
            exc,
        )
        raise
    except SubprocessOutputError as exc:
        logger.error(
            "[orient] job=%s a subprocess (tesseract/ghostscript/etc.) failed: %s",
            job_id,
            exc,
        )
        raise

    _strip_throwaway_ocr_text(output_path)

    return output_path
