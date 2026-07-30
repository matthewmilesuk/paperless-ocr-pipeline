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
   code path regardless of entry point.

2. **Cleanup pass (custom blank-page detection)**
   - Blank page removal only — deskew/auto-rotate now happens at the
     PDF/A conversion stage (stage 6), via `ocrmypdf` flags. See
     "Decisions Changed" below for why this moved.
   - Each page is rasterized and measured for ink/pixel coverage.
     Confidently-blank pages (≈ under 0.5% ink coverage) are dropped
     automatically. Borderline pages (≈ 0.5–3%) are **kept in the final
     document by default** (never silently dropped) but logged for manual
     review, since a false-positive blank-page drop is unrecoverable once
     the source paper is shredded. Threshold is configurable.

3. **Split** into individual page images (in-memory / temp files only —
   never persisted to disk as separate files).

4. **OCR — Google Document AI**
   - Processor: Enterprise Document OCR
   - Cost: ~$1.50 per 1,000 pages (current tier, well under the
     5,000,000 pages/month threshold for this project's volume)
   - Output: the full Document AI response, serialized (whole-document
     text plus per-page layout/bounding-box data) — `reassemble.py`
     converts this into whatever overlay format it ends up using (e.g.
     hOCR) at that stage, not here, so nothing Document AI returns is
     discarded before it's needed.
   - **Known v1 limitation:** uses Document AI's *synchronous* process
     endpoint (one API call for the whole document), which caps input at
     15 pages (30 with `imageless_mode`, which trades off some accuracy —
     not used). Documents over 15 pages raise a clear, specific error
     rather than being silently truncated or attempted anyway. Batch
     processing (via Cloud Storage, up to 500 pages) would lift this cap
     but isn't built yet — see "Things Deliberately Decided Against".

5. **Reassemble + overlay** — rebuild a single PDF in original page order,
   overlaying the OCR'd text as an invisible layer on top of the original
   page image (so the file looks like the scan but is fully searchable/
   copyable).

6. **PDF/A conversion** — via `ocrmypdf`, called directly:
   - `--output-type pdfa`
   - `--rotate-pages` / `--deskew` — this is where auto-rotation and
     deskew actually happen (moved here from the cleanup pass; see
     "Decisions Changed" below)
   - `--optimize 3` for file size reduction (JBIG2 for bitonal content,
     smarter JPEG handling for color) — this is where file size gets
     controlled, NOT by downscaling the source scan. The 400 DPI color
     source is the archival "source of truth" and should never be
     pre-shrunk before this point.

7. **Validate — veraPDF** — confirms actual PDF/A compliance. If validation
   fails, the file does NOT proceed to the output folder. It's moved to a
   `failed/` folder with a log entry for manual review instead.

8. **Output** — finished PDF/A lands in the folder Paperless-NGX watches.

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
    `--rotate-pages` flags do this properly and were always going to run
    at the PDF/A conversion stage anyway (stage 6) — nothing is lost by
    dropping Stirling for this; that work just moves to `pdfa.py`.
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

## Open Questions / To Be Decided

- Exact naming/tagging convention for output files.
- Behavior when a job fails partway through (retry? quarantine? notify?).
- Whether the "borderline blank page" log should be a flat file, a DB
  table visible in the Django admin, or something else.
