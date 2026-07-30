# Paperless OCR Pipeline

A self-hosted pipeline that turns raw scans into validated, archival-quality
PDF/A files with a searchable text layer, ready to drop into
[Paperless-NGX](https://github.com/paperless-ngx/paperless-ngx)'s watch
folder. It handles ingest, cleanup, OCR (via Google Document AI), PDF/A
conversion, and compliance validation — the OCR quality problem, solved once,
upstream of any downstream classification.

Built for local-network use by one or more accounts (multi-user, with
enforced 2FA): drop a scan in a Samba folder (or upload it through the web
UI), and get a clean, searchable, validated PDF/A back out.

See below for the full project spec, architecture, and setup details.

## Status

This repo is an early-stage scaffold. Here's what's actually wired up
versus what's still a stub, checked against the current code (not the
original plan):

**Implemented and working:**
- Custom Django user model (`accounts.User`) with multi-user accounts.
- Enforced TOTP 2FA (`django-otp` + `django-two-factor-auth`, backup codes
  via `otp_static`) — verified end-to-end: unauthenticated requests are
  redirected to login, authenticated users without a 2FA device are
  redirected to setup on every page including Django admin, and the setup
  page itself stays reachable.
- Per-user job visibility (`ingest.views._jobs_visible_to`) — normal users
  only see/open their own jobs; staff/admin see everything. Verified with
  a real Django test client.
- **Real file-saving on both entry points**, sharing one code path
  (`ingest/services.py`): the web upload view saves the uploaded file into
  `SCAN_INPUT_DIR` and records a pending `Job` owned by the logged-in
  user; the watcher does the same for files dropped in the Samba input
  folder, owned by `DEFAULT_JOB_OWNER_USERNAME` (fails loudly and skips
  the file if that account is missing or unset, rather than silently
  leaving a job unattributed). Covered by tests in `ingest/tests.py`.
- docker-compose scaffold (web, worker, redis, samba, watcher,
  stirling-pdf) and a Dockerfile that builds.
- Stirling PDF and Google Document AI are wired as *integration points*
  (`STIRLING_PDF_URL`, `GCP_DOCAI_*` settings, the stirling-pdf
  docker-compose service) — not yet called by any code.

**Still a stub / not working yet:**
- **Every `pipeline/*.py` stage function raises `NotImplementedError`**
  (`ingest.py`, `cleanup.py`, `split.py`, `ocr.py`, `reassemble.py`,
  `pdfa.py`, `validate.py`, `output.py`). `pipeline/run.py` orchestrates
  them in order, but running it fails at the first stage. The web upload
  view still enqueues it regardless, so that Django-RQ job will fail in
  the worker; the watcher doesn't enqueue anything yet at all (see
  `watcher/watch.py`'s `handle_new_scan`), since there's nothing working
  downstream for it to hand off to.
- No synchronous-vs-async cutover logic (`SYNCHRONOUS_BATCH_SIZE_LIMIT` is
  a setting; nothing reads it yet).
- No completion emails (SMTP settings exist; nothing calls `send_mail()`).

The spec and architecture below describe the target design — check the
list above before assuming something works because it's described there.

## Working in this repo

If you're an AI coding agent (or a human who wants the same context),
see [AGENTS.md](AGENTS.md) for ground rules before making changes.

---

# Document Digitization Pipeline — Project Spec

## Purpose

A self-hosted tool to convert scanned documents (from an HP N9120 FN2 scanner,
duplex, 400 DPI, color) into validated, archival-quality PDF/A files with a
searchable text layer, for ingestion into Paperless-NGX.

~~Single user, local network only. No authentication or public-facing
deployment needed — this is a personal utility, not a hosted service.~~

**Updated:** this is a multi-user tool with enforced 2FA (see
"Authentication & Access Control" below), intended to be usable by other
Paperless-NGX users too, not a single personal install. Still
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

2. **Cleanup pass (Stirling PDF API)**
   - Deskew / auto-rotate
   - Blank page removal
   - Confidently-blank pages (≈ under 0.5% ink coverage) are dropped
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
   - Output: hOCR (text + layout position per page)

5. **Reassemble + overlay** — rebuild a single PDF in original page order,
   overlaying the OCR'd text as an invisible layer on top of the original
   page image (so the file looks like the scan but is fully searchable/
   copyable).

6. **PDF/A conversion** — via `ocrmypdf` (either called directly, or through
   the Stirling PDF API which wraps the same tool):
   - `--output-type pdfa`
   - `--rotate-pages` / `--deskew` as a safety net
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
| `stirling-pdf` | Cleanup pass (deskew, blank-page removal) + PDF/A conversion via its API |

Everything reproducible via `docker-compose.yml` + a `.env.example` +
a setup script. Should be shareable with other Paperless-NGX users, not
just usable on this one machine.

## Things Deliberately Decided Against (for now)

- ~~No authentication / user accounts.~~ **Superseded:** the web front end
  now supports multiple users with enforced 2FA — see "Authentication &
  Access Control" below. (This is separate from the local-network-only
  deployment assumption in "Purpose" above, which still holds.) The
  Samba/watcher/worker side has no auth concept and is unaffected.
- No Celery — Django-RQ is simpler for this scale.
- No custom-built blank-page detection or rotation logic — Stirling PDF
  already solves this well; don't reinvent it.
- No downscaling of source scans to control file size — let `ocrmypdf`
  optimization handle that after OCR, not before.

## Open Questions / To Be Decided

- Exact naming/tagging convention for output files.
- Behavior when a job fails partway through (retry? quarantine? notify?).
- Whether the "borderline blank page" log should be a flat file, a DB
  table visible in the Django admin, or something else.

## Authentication & Access Control

The web front end (upload UI, job status) requires a login and enforces
TOTP-based two-factor authentication (Google Authenticator/Authy-style, plus
backup codes) — a password alone is not enough. Any logged-in user without a
2FA device configured is redirected straight to setup before they can reach
uploads, job status, or Django admin.

Job visibility is scoped per user: normal users only see and can open jobs
they uploaded themselves. Staff/admin accounts (Django's `is_staff` flag)
can see and open every job on the system.

This only applies to the web front end. The Samba share, watcher, and
worker have no auth concept — a scan dropped in the Samba input folder is
attributed to the account named in `DEFAULT_JOB_OWNER_USERNAME` (`.env`),
which must already exist (create it with `manage.py createsuperuser`).

## Getting Started

1. Copy `.env.example` to `.env` and fill in your values (GCP credentials,
   processor IDs, SMTP settings, folder paths, `DEFAULT_JOB_OWNER_USERNAME`).
2. Drop your GCP service account key JSON where `.env` points
   `GOOGLE_APPLICATION_CREDENTIALS` to.
3. Run `./setup.sh` to build and start the stack.
4. Create yourself an account: `docker compose exec web python manage.py
   createsuperuser` (or `python manage.py createsuperuser` if running
   outside Docker).
5. Log in at the web UI with that account. **On first login you're
   redirected straight to 2FA setup** — scan the QR code with an
   authenticator app (Google Authenticator, Authy, etc.) and save the
   generated backup codes. This is required before you can reach uploads
   or job status; a password alone isn't enough (see "Authentication &
   Access Control" below).
6. Visit the web UI to upload a document, or drop a PDF into the Samba
   input share. Either way a `Job` row gets created and the file saved —
   actual OCR/PDF-A processing isn't implemented yet (see "Status" above).
