# Document Digitization Pipeline — Project Spec

## Purpose

A self-hosted tool to convert scanned documents (from an HP N9120 FN2 scanner,
duplex, 400 DPI, color) into validated, archival-quality PDF/A files with a
searchable text layer, for ingestion into Paperless-NGX.

~~Single user, local network only. No authentication or public-facing
deployment needed — this is a personal utility, not a hosted service.~~

**Updated:** this is a multi-user tool with enforced 2FA (see
`README.md` "Authentication & Access Control"), intended to be usable by
other Paperless-NGX users too, not a single personal install. Still
local-network-only in the sense that nothing here is hardened for direct
internet exposure — no TLS, no rate limiting, permissive `ALLOWED_HOSTS`
by default — and `docker-compose.yml`'s port bindings aren't restricted
to localhost, so don't put this host directly on the public internet
without adding that hardening yourself.

## Background / Why This Exists

Inspired by a community write-up on Paperless-NGX + AI classification pipelines.
Key lesson taken from that article: **OCR quality is the bottleneck**, not the
downstream classification model. Tesseract (Paperless's default) wasn't
sufficient for archival-grade text extraction; Google Document AI performed
significantly better in side-by-side testing.

This project focuses specifically on the **OCR + PDF/A conversion** stage —
turning a raw scan into a clean, validated, searchable archival PDF. It does
NOT cover downstream classification (correspondent/title/tag assignment) —
that remains Paperless-NGX's job once the file lands in its watch folder.

## Inputs

- One PDF per scan job from the HP N9120 FN2, delivered via its
  scan-to-network-folder (Samba) feature.
- Duplex, 400 DPI, color.
- **One PDF in → one PDF out.** No file-splitting or multi-file output;
  page order must be preserved (minus dropped blank pages).

## Pipeline (in order)

1. **Ingest** — file lands in the Samba input folder. A `watchdog`-based
   watcher process detects the new file and kicks off the pipeline. The same
   pipeline function is also triggered by the web UI upload, so there's one
   code path regardless of entry point. A small validation gate, not a
   transformation: confirms the file genuinely opens as a PDF via
   `pikepdf` (not just a `.pdf` extension) and has at least one page,
   rejecting an empty file, a corrupted/non-PDF file, and a
   technically-valid zero-page PDF as three distinct, clearly-logged
   failure cases.

2. **Cleanup pass (custom blank-page detection)**
   - Blank page removal only. Deskew/auto-rotate is a separate stage
     (stage 3, below) — see "Decisions Changed" for why it's not part of
     cleanup and not part of the PDF/A conversion stage either.
   - Each page is rasterized and measured for ink/pixel coverage.
     Confidently-blank pages (≈ under 0.5% ink coverage) are dropped
     automatically. Borderline pages (≈ 0.5–3%) are **kept in the final
     document by default** (never silently dropped) but logged for manual
     review, since a false-positive blank-page drop is unrecoverable once
     the source paper is shredded. Threshold is configurable.

3. **Orient (deskew + auto-rotate)** — via `ocrmypdf` (`--deskew
   --rotate-pages`), run here, *before* Document AI ever sees the
   document. See "Decisions Changed" for why this can't happen later:
   ocrmypdf skips all per-page processing (not just OCR) on pages that
   already have a text layer, so this has to run before any text layer
   exists at all. `--rotate-pages` needs a text-orientation signal, so
   ocrmypdf runs its own OCR engine (tesseract) internally as a side
   effect and embeds a throwaway invisible text layer — that layer is
   stripped immediately after orienting, before this stage hands off to
   Document AI, so it never lands in the final document.

4. **OCR — Google Document AI**
   - Processor: Enterprise Document OCR
   - Cost: ~$1.50 per 1,000 pages (current tier, well under the
     5,000,000 pages/month threshold for this project's volume)
   - Sends the oriented PDF directly to Document AI's synchronous process
     endpoint (one API call for the whole document — Document AI
     segments pages itself internally; there is no separate page-image
     splitting step before this). Output: the full Document AI response,
     serialized (whole-document text plus per-page layout/bounding-box
     data, including both absolute and *normalized* — resolution-
     independent — bounding boxes) — `reassemble.py` converts this into
     the overlay directly, so nothing Document AI returns is discarded
     before it's needed.
   - **Known v1 limitation:** the synchronous endpoint caps input at 15
     pages (30 with `imageless_mode`, which trades off some accuracy —
     not used). Documents over 15 pages raise a clear, specific error
     rather than being silently truncated or attempted anyway. Batch
     processing (via Cloud Storage, up to 500 pages) would lift this cap
     but isn't built yet — see "Things Deliberately Decided Against".

5. **Reassemble + overlay** — adds Document AI's recognized text as an
   invisible (searchable, not painted) layer directly onto the oriented
   PDF's own pages via `pikepdf.Page.add_overlay()` — no rasterized page
   images, no intermediate split step (see "Decisions Changed" for why
   `pipeline/split.py` was removed). Positioning uses Document AI's
   normalized bounding boxes against each page's actual size and
   rotation (read from the PDF itself, not assumed from Document AI's
   own dimension data). Page order and content are otherwise untouched —
   only text is added.

6. **PDF/A conversion** — via `ocrmypdf`, called directly:
   - `--output-type pdfa`
   - `--skip-text` — required, not optional: the page already has a text
     layer (from stage 5) by this point, and without `--skip-text`
     ocrmypdf raises an error on the first page it finds with existing
     text. Deliberately **no** `--rotate-pages`/`--deskew` here — that
     already happened in stage 3, and per the same skip-all-processing
     behavior, those flags would be silent no-ops at this point in the
     pipeline anyway (see "Decisions Changed").
   - `--optimize 3` for file size reduction (JBIG2 for bitonal content,
     smarter JPEG handling for color) — this is where file size gets
     controlled, NOT by downscaling the source scan. The 400 DPI color
     source is the archival "source of truth" and should never be
     pre-shrunk before this point.
   - `--optimize 3` requires `pngquant`, in the Dockerfile alongside
     `ghostscript`/`tesseract-ocr`/`qpdf` (confirmed missing at first —
     a hard failure, not a soft warning — by actually running this
     locally without it; added and re-verified in both a local venv and
     a rebuilt container). `jbig2` is also recommended for this optimize
     level but only produces a warning, not a failure, if missing —
     not currently installed, lower priority.

7. **Validate — veraPDF** — confirms actual PDF/A-2b compliance via the
   real `verapdf` CLI (`--flavour 2b --format json`; not apt-installable —
   see the `Dockerfile` and `docker/verapdf-auto-install.xml` for how it's
   installed). If
   validation fails, the file does NOT proceed to the output folder. It's
   moved to a `failed/` folder with a log entry for manual review instead.
   Two distinct failure modes, not lumped together: a genuine PDF/A rule
   violation (logged with the specific ISO clause + description) versus
   veraPDF being unable to parse the file at all, which means something
   upstream in the pipeline produced a broken file rather than a
   legitimate document merely failing a compliance check — logged more
   loudly, since it points at a real bug rather than a routine edge case.

8. **Output** — finished PDF/A lands in the folder Paperless-NGX watches
   (on validation failure, in `failed/` instead — kept for manual review,
   never deleted). Filename is `{job.id}_{original filename}`, so two
   jobs can never collide even if their source filenames match. The move
   is atomic where the filesystem allows it (same-volume rename, not
   copy-then-delete, so nothing half-written is ever visible to
   Paperless-NGX's watcher). Also removes this job's intermediate
   pipeline files from the output folder — nothing upstream cleans up
   after itself.

## Processing Mode

- **Small batches (under ~5 files): synchronous.** Upload/detect → wait a
  few seconds → done.
- **Larger batches: asynchronous**, via Django-RQ (Redis-backed queue) —
  chosen over Celery for lower operational complexity given this project's
  modest scale (a handful of users, not high-volume production traffic).
  Emails the user (via SMTP, `send_mail()`) when the job completes.

## Architecture / Services (docker-compose)

| Service   | Purpose                                                          |
|-----------|-------------------------------------------------------------------|
| `web`     | Django app — upload UI, job status                                |
| `worker`  | Django-RQ worker (same codebase, different entrypoint)             |
| `redis`   | Queue backend for `worker`                                        |
| `samba`   | Samba share for scan-to-folder input + watched output folder (`dperson/samba` image) |
| `watcher` | `watchdog`-based process monitoring the Samba input folder, triggers pipeline |

Everything reproducible via `docker-compose.yml` + a `.env.example` +
a setup script. Should be shareable with other Paperless-NGX users, not
just usable on this one machine.

## GCP / Document AI Configuration

The three values `pipeline/ocr.py` needs — GCP project ID, Document AI
processor ID, and the service account key — can be set two ways:

- **`.env`** (`GCP_PROJECT_ID`, `GCP_DOCAI_PROCESSOR_ID`,
  `GOOGLE_APPLICATION_CREDENTIALS`), read once at process boot, same as
  every other setting. This is the only mechanism before the wizard
  existed, and still works unchanged for anyone who set it up this way.
- **The `gcpconfig` app's admin-only setup wizard** (`/gcp-setup/`) —
  walks a staff user through the manual GCP console steps that can't be
  automated (create project, enable billing, enable the Document AI
  API, create a processor, create a service account) with direct
  console links at each step, then accepts the three values via a form
  (the key as a file upload). On submit, it makes one real, cheap
  Document AI call (`get_processor` — metadata only, not a
  page-processing call) to validate the submission immediately;
  distinguishable failures (bad credentials, wrong processor ID,
  missing IAM role, etc. — the same mapping `ocr.py` uses for its own
  error logging, `describe_document_ai_error()`) are surfaced in the
  wizard UI, and nothing is saved unless validation actually passes.

**DB wins, `.env` is the fallback.** `pipeline.ocr._effective_config()`
checks `gcpconfig.models.Configuration` first (a singleton row, `pk=1`)
and falls back to `.env`/`settings.py` only if no row exists yet — the
resolution happens fresh on every OCR call, not once at import time.
This matters because `web` (where the wizard runs) and `worker` (where
`ocr_document()` actually executes) are separate OS processes in
separate containers that don't share memory or environment variables —
only the SQLite DB and the `scan-data` volume are shared between them.
A DB-backed value is the only way a wizard submission can take effect
without restarting `worker`.

The uploaded key file is written to `GCP_CREDENTIALS_UPLOAD_DIR`
(default `/data/secrets`, a subdirectory of the same writable
`scan-data` volume `SCAN_INPUT_DIR`/`SCAN_OUTPUT_DIR`/`SCAN_FAILED_DIR`
already use) with `0600` permissions, via a write-to-temp-then-rename
so a failed validation can never clobber a previously-working key.
This is deliberately not the same path `GOOGLE_APPLICATION_CREDENTIALS`
points at — that's a read-only bind mount sourced from the host
filesystem (`docker-compose.yml`), so the app could never write there
even if it wanted to.

**Readiness gates.** `gcpconfig.middleware.RequireGcpConfigMiddleware`
redirects staff users to the wizard on any page if nothing is
configured yet (DB or `.env`) — scoped to staff only, not a blanket
gate like 2FA enforcement, since non-staff users have no ability to act
on it. `ingest.views.upload()` separately refuses to queue a job at all
if unconfigured, with a distinct message for staff vs. non-staff — this
exists for non-staff users specifically, since the middleware normally
keeps staff from ever reaching the upload page unconfigured in the
first place.

`GCP_DOCAI_LOCATION` is deliberately not one of the wizard's
configurable values — it's a one-time-per-deployment choice, not
something that benefits from live editing, so it stays `.env`-only.

## Things Deliberately Decided Against (for now)

- ~~No authentication / user accounts.~~ **Superseded:** the web front end
  now supports multiple users with enforced 2FA — see `README.md`
  "Authentication & Access Control". This is separate from the
  local-network-only deployment assumption above, which still holds.
- No Celery — Django-RQ is simpler for this scale.
- ~~No custom-built blank-page detection or rotation logic — Stirling PDF
  already solves this well; don't reinvent it.~~ **Superseded:** see
  "Decisions Changed" below — Stirling PDF has been removed, and
  blank-page detection is now custom-built after all.
- No downscaling of source scans to control file size — let `ocrmypdf`
  optimization handle that after OCR, not before.
- No batch OCR processing (Cloud Storage-based, up to 500 pages) yet —
  the synchronous endpoint's 15-page cap is enough for this project's
  actual volume (single-scanner, one-document-at-a-time). A future
  enhancement, not an oversight: documents over the cap raise
  `pipeline.ocr.DocumentTooLongForSyncOCR` rather than being silently
  truncated or mishandled, so this is a deliberate, visible scope cut
  rather than a bug waiting to happen.

## Decisions Changed

- **Stirling PDF removed from the pipeline** (was: cleanup pass — deskew/
  auto-rotate + blank-page removal — and available as an alternate PDF/A
  conversion path). A closer look at its actual API showed it doesn't
  cover what this project needs:
  - Its rotation endpoint only supports fixed 90-degree increments, not
    auto skew/orientation detection. `ocrmypdf`'s own `--deskew` and
    `--rotate-pages` flags do this properly, so nothing is lost by
    dropping Stirling for this. (At the time of this decision, the plan
    was to run those flags at the PDF/A conversion stage; that moved
    again once `pdfa.py` was actually built — see the `pipeline/orient.py`
    entry below for why.)
  - Its remove-blanks endpoint is binary (delete or don't), with no way
    to express the "confidently blank → auto-drop, borderline → keep and
    log" requirement (stage 2), and no per-page report of what it
    removed. Blank-page detection now needs custom code — rasterize each
    page, measure ink/pixel coverage, apply the two-tier threshold — to
    support that safely.
  - Net effect: `cleanup.py` becomes custom blank-page detection only;
    deskew/auto-rotate moved to `pdfa.py` as `ocrmypdf` flags; the
    `stirling-pdf` docker-compose service and its `STIRLING_PDF_URL`
    integration point are gone. The custom blank-page detection logic
    itself is not yet implemented — this change updates the spec/infra
    ahead of that work.
- **`pipeline/split.py` removed** (was: stage 3, rasterizing the cleaned
  PDF into per-page images for OCR to consume). It existed for an OCR
  design where each page image was sent to Document AI individually.
  Once `ocr.py` was actually built, it turned out Document AI's
  synchronous endpoint takes the whole cleaned PDF in one call and
  segments pages itself — no pre-split images needed there. Investigated
  whether `reassemble.py` needed them either: it doesn't, because (a)
  Document AI's bounding boxes are available as `normalized_vertices` —
  fractions of the page, resolution-independent — so positioning text
  never depended on knowing any particular rasterization DPI, and (b)
  `cleanup.py` only ever deletes pages via `pikepdf`, never rasterizes
  ones it keeps, so the cleaned PDF's pages already are the original
  scan content at full quality; `pikepdf.Page.add_overlay()` can merge
  the invisible text layer directly onto them. Nothing in the pipeline
  needed rasterized page images once both facts were confirmed, so the
  stage was removed rather than kept as unused scaffolding.
- **`pipeline/orient.py` added, deskew/auto-rotate moved out of
  `pdfa.py`.** The plan (see the Stirling entry above) was for `pdfa.py`
  to run `--deskew --rotate-pages` as part of PDF/A conversion, after
  `reassemble.py` had already added Document AI's text layer. Checked
  this against ocrmypdf's actual source before building `pdfa.py`, not
  just its docs: `is_ocr_required()` (`ocrmypdf/_pipeline.py`) returns
  `False` — logging "skipping all processing on this page" — for any
  page that already has text, under `--skip-text` (required, since
  without it ocrmypdf aborts outright on a page with existing text).
  `process_page()`, where both `--deskew` and `--rotate-pages` detection
  actually happen, is never called for such pages. So running those
  flags at the `pdfa.py` stage as originally planned would have been a
  silent no-op on every page, every run — not a misalignment risk, a
  dead feature. Moved deskew/auto-rotate to a new stage 3
  (`pipeline/orient.py`), running before Document AI/`reassemble.py` add
  any text layer. `--rotate-pages` needs a text-orientation signal, so
  ocrmypdf runs tesseract internally as a side effect and embeds a
  throwaway invisible text layer; that gets stripped before this stage
  hands off to `ocr.py`. Also found (again, by checking real output, not
  assuming): ocrmypdf's own `--mode strip` doesn't remove this, because
  ocrmypdf's default and sandwich renderers both wrap OCR text in a Form
  XObject that `strip_invisible_text()` doesn't recurse into —
  `orient.py` reimplements that function's algorithm generalized to
  handle nested XObjects.

## Open Questions / To Be Decided

- ~~Exact naming/tagging convention for output files.~~ **Resolved:**
  `{job.id}_{original filename}` (`pipeline/output.py`) — the job ID
  guarantees no collision even when two jobs share a source filename.
- Behavior when a job fails partway through (retry? quarantine? notify?).
  Still open for failures at any pipeline *stage* — `run.py` doesn't
  catch exceptions from cleanup/orient/ocr/reassemble/pdfa yet, only
  routes a veraPDF *validation* failure to `output.deliver_failed()`.
- ~~Whether the "borderline blank page" log should be a flat file, a DB
  table visible in the Django admin, or something else.~~ **Resolved:**
  a DB table, `ingest.models.BorderlinePage` (visible in Django admin),
  populated by `pipeline/cleanup.py`.
