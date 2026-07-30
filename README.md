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
- docker-compose scaffold (web, worker, redis, samba, watcher) and a
  Dockerfile that builds.
- **Admin-only GCP/Document AI setup wizard** (`gcpconfig` app,
  `/gcp-setup/`) — walks a staff user through the manual GCP console
  steps, accepts project ID/processor ID/service account key via a
  form, and validates with one real, cheap Document AI call before
  saving. DB-backed config takes priority over `.env`, resolved fresh
  on every OCR call so it takes effect without restarting the `worker`
  container. See "GCP / Document AI Configuration" below.
- **All eight pipeline stages are real, not stubs**, each covered by
  tests in `pipeline/tests.py`. **`pipeline.run.run_pipeline()` has now
  been run once, successfully, end to end against a real scan uploaded
  through the web UI** — real Document AI call, real ocrmypdf
  orient/pdfa passes, real veraPDF validation (compliant), delivered to
  `SCAN_OUTPUT_DIR` with the `Job` marked `done` and no leftover
  intermediate files. **Caveat:** this almost certainly didn't go
  through the actual `django_rq` queue + `worker` path — `worker`
  (and `watcher`) silently crash-looped on every boot until just now
  (see `AGENTS.md` "Current state" for the fix and the direct evidence:
  two jobs left sitting unprocessed in Redis for hours, only picked up
  the moment `worker` first booted successfully). Read this bullet as
  "the pipeline stages work against a real scan," not "the queued path
  was proven" — the latter is now separately verified, for the queue
  mechanism itself, but not yet with a full real OCR call through it.
  That's one confirmed real run, not a guarantee:
  there's still no failure handling between stages (see below), and it
  hasn't been exercised against edge cases like a multi-page or rotated
  document, or one that actually fails PDF/A validation. Don't read "one
  successful run" as "production ready":
  - `ingest.py` — a small validation gate, not a transformation:
    confirms the input genuinely opens as a PDF via `pikepdf` (not a
    `.pdf`-extension sniff) and has at least one page. Three distinct,
    separately-tested failure cases: empty file, corrupted/non-PDF
    file, and a technically-valid zero-page PDF (confirmed as a real,
    separately-constructible case before writing the check, not
    assumed).
  - `cleanup.py` — custom blank-page detection (rasterize, measure ink
    coverage, drop confidently-blank pages, log borderline ones).
  - `orient.py` — deskew/auto-rotate via ocrmypdf, run before Document AI
    or any text layer exists (see `PROJECT_SPEC.md` "Decisions Changed"
    for why it can't run later). Strips the throwaway tesseract text
    layer ocrmypdf's rotation detection embeds as a side effect —
    verified concretely that ocrmypdf's own `--mode strip` does not
    actually do this (see `AGENTS.md` "Current state").
  - `ocr.py` — calls the real Google Document AI API (synchronous
    endpoint, one call per document, 15-page cap). Tests mock the client
    entirely; the one real, billable, deliberately-opt-in call is
    `scripts/smoke-test-ocr.py`, never run automatically.
  - `reassemble.py` — overlays Document AI's recognized text onto the
    oriented PDF's own pages as an invisible, searchable layer, rotation
    included. See `AGENTS.md` "Current state" for the caveat on how far
    the rotation handling has actually been verified.
  - `pdfa.py` — ocrmypdf PDF/A conversion (`--skip-text`, no
    rotate/deskew — see `orient.py` above). `--optimize 3` needs
    `pngquant`, now in the Dockerfile (was missing at first — a hard
    failure, confirmed and then fixed).
  - `validate.py` — calls the real `verapdf` CLI (not apt-installable —
    installed via the Dockerfile + `docker/verapdf-auto-install.xml`).
    Distinguishes a genuine PDF/A rule violation from veraPDF being
    unable to parse the file at all — the latter logged more loudly,
    since it points at a pipeline bug rather than a routine compliance
    miss. Tests run against real files (compliant, non-compliant,
    unparseable), not mocked.
  - `output.py` — moves the validated PDF/A into `SCAN_OUTPUT_DIR`
    (success) or `SCAN_FAILED_DIR` (failure, kept for manual review, not
    deleted), named `{job.id}_{original filename}` so two jobs can never
    collide. Atomic move where the filesystem allows it. Also cleans up
    this job's intermediate pipeline files, since no earlier stage does.

**Still missing / not working yet, even though every stage is implemented:**
- **Only one real end-to-end run has been done, via the web upload path,
  on one well-formed document.** The Samba-drop (watcher) path hasn't
  been exercised this way, and neither has any document that hits a
  real failure partway through — see the next point. Treat "it worked
  once" as encouraging, not as broad coverage.
- **No failure handling between stages.** `run_pipeline()` doesn't catch
  exceptions from `ingest`/`cleanup`/`orient`/`ocr`/`reassemble`/`pdfa` —
  an exception from any of those propagates uncaught and the job is left
  stuck, not routed to `output.deliver_failed()`. Only a veraPDF
  *validation* failure (the last possible failure point) is currently
  handled and delivered to `failed/`.
- The web upload view enqueues the job regardless of any of the above,
  so that Django-RQ job can currently fail in the worker with no
  handling; the watcher doesn't enqueue anything yet at all (see
  `watcher/watch.py`'s `handle_new_scan`).
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
   code path regardless of entry point. A small validation gate, not a
   transformation: confirms the file genuinely opens as a PDF via
   `pikepdf` and has at least one page — rejects an empty file, a
   corrupted/non-PDF file, and a technically-valid zero-page PDF as
   three distinct, clearly-logged failure cases.

2. **Cleanup pass (custom blank-page detection)**
   - Blank page removal only — deskew/auto-rotate is a separate stage
     (step 3, below). See `PROJECT_SPEC.md` "Decisions Changed" for the
     full history: Stirling PDF was evaluated and dropped before
     implementation, then deskew/rotate moved a second time, out of
     `pdfa.py`, once it turned out ocrmypdf can't actually run those
     flags on a page that already has a text layer.
   - Each page is rasterized and measured for ink/pixel coverage.
     Confidently-blank pages (≈ under 0.5% ink coverage) are dropped
     automatically. Borderline pages (≈ 0.5–3%) are **kept in the final
     document by default** (never silently dropped) but logged for manual
     review, since a false-positive blank-page drop is unrecoverable once
     the source paper is shredded. Threshold is configurable.

3. **Orient (deskew + auto-rotate)** — via `ocrmypdf --deskew
   --rotate-pages`, run *before* Document AI ever sees the document.
   Has to run this early: ocrmypdf skips all per-page processing (not
   just OCR) on pages that already have a text layer, confirmed against
   its actual source, not just its docs (see `PROJECT_SPEC.md` "Decisions
   Changed"). `--rotate-pages` runs tesseract internally to detect
   orientation, which embeds a throwaway invisible text layer as a side
   effect — that gets stripped immediately after, before Document AI
   ever runs, since ocrmypdf's own text layer isn't what we want in the
   final document.

4. **OCR — Google Document AI**
   - Processor: Enterprise Document OCR
   - Cost: ~$1.50 per 1,000 pages (current tier, well under the
     5,000,000 pages/month threshold for this project's volume)
   - Sends the oriented PDF directly to Document AI's synchronous process
     endpoint in one call — Document AI segments pages itself, so there's
     no separate page-image splitting step before this. Output: the full
     Document AI response, serialized (whole-document text plus per-page
     layout/bounding-box data, both absolute and resolution-independent
     normalized coordinates) — `reassemble.py` uses this directly.
   - **Known v1 limitation:** the synchronous endpoint caps input at 15
     pages. Documents over that raise a clear error rather than being
     silently truncated. Batch processing (Cloud Storage, up to 500
     pages) would lift this but isn't built yet.

5. **Reassemble + overlay** — adds Document AI's recognized text as an
   invisible (searchable, not painted) layer directly onto the oriented
   PDF's own pages via `pikepdf.Page.add_overlay()` — no rasterized page
   images or intermediate split step (see `PROJECT_SPEC.md` "Decisions
   Changed" for why `pipeline/split.py` was removed). Positioning uses
   Document AI's normalized bounding boxes against each page's actual
   size and rotation, read from the PDF itself.

6. **PDF/A conversion** — via `ocrmypdf`, called directly:
   - `--output-type pdfa`
   - `--skip-text` — required: the page already has a text layer by this
     point, and ocrmypdf errors out on the first page it finds with
     existing text otherwise. Deliberately no `--rotate-pages`/`--deskew`
     here anymore — that's step 3's job now (see above).
   - `--optimize 3` for file size reduction (JBIG2 for bitonal content,
     smarter JPEG handling for color) — this is where file size gets
     controlled, NOT by downscaling the source scan. The 400 DPI color
     source is the archival "source of truth" and should never be
     pre-shrunk before this point.
   - `--optimize 3` needs `pngquant`, which is now in the Dockerfile
     alongside `ghostscript`/`tesseract-ocr`/`qpdf` (was missing at
     first — a hard failure, confirmed by running this locally without
     it before adding it and re-verifying in a rebuilt container).

7. **Validate — veraPDF** — confirms actual PDF/A-2b compliance via the
   real `verapdf` CLI (not apt-installable — see `Dockerfile` /
   `docker/verapdf-auto-install.xml`). If validation fails, the file does
   NOT proceed to the output folder. It's moved to a `failed/` folder
   with a log entry for manual review instead — distinguishing a genuine
   rule violation from veraPDF being unable to parse the file at all
   (the latter meaning a bug upstream in the pipeline, logged louder).

8. **Output** — finished PDF/A lands in the folder Paperless-NGX watches
   (or `failed/` on validation failure, kept for manual review, never
   deleted), named `{job.id}_{original filename}` so two jobs can never
   collide. Atomic move where the filesystem allows it, never
   copy-then-delete. Also cleans up this job's intermediate pipeline
   files from the output folder.

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
  `GOOGLE_APPLICATION_CREDENTIALS`), read once at process boot. This is
  the original mechanism and still works unchanged.
- **The admin-only setup wizard** (`/gcp-setup/`) — walks a staff user
  through the manual GCP console steps that can't be automated (create
  project, enable billing, enable the Document AI API, create a
  processor, create a service account) with direct console links at
  each step, then accepts the three values via a form (the key as a
  file upload). On submit it makes one real, cheap Document AI call
  (`get_processor`) to validate immediately; distinguishable failures
  (bad credentials, wrong processor ID, missing IAM role) are surfaced
  in the wizard UI, and nothing is saved unless validation passes.

**DB wins, `.env` is the fallback**, resolved fresh on every OCR call
(`pipeline.ocr._effective_config()`) — not read once at import time.
This matters because `web` (where the wizard runs) and `worker` (where
OCR actually executes) are separate processes that don't share memory,
only the SQLite DB and the `scan-data` volume — a DB-backed value is
the only way a wizard submission takes effect without restarting
`worker`.

The uploaded key is written to `GCP_CREDENTIALS_UPLOAD_DIR` (default
`/data/secrets`, on the same writable `scan-data` volume the scan
folders use) with `0600` permissions, via write-to-temp-then-rename so
a failed validation can never clobber a previously-working key.

A staff user is redirected to the wizard on any page if nothing's
configured yet; uploads are refused outright (with a distinct message
for staff vs. everyone else) if unconfigured, so a job never gets
queued only to fail confusingly at the OCR stage.

## Things Deliberately Decided Against (for now)

- ~~No authentication / user accounts.~~ **Superseded:** the web front end
  now supports multiple users with enforced 2FA — see "Authentication &
  Access Control" below. (This is separate from the local-network-only
  deployment assumption in "Purpose" above, which still holds.) The
  Samba/watcher/worker side has no auth concept and is unaffected.
- No Celery — Django-RQ is simpler for this scale.
- ~~No custom-built blank-page detection or rotation logic — Stirling PDF
  already solves this well; don't reinvent it.~~ **Superseded:** Stirling
  PDF has been removed from the pipeline; blank-page detection is now
  custom-built. See `PROJECT_SPEC.md` "Decisions Changed" for why.
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

1. Copy `.env.example` to `.env` and fill in your values (SMTP settings,
   folder paths, `DEFAULT_JOB_OWNER_USERNAME`) — `./setup.sh` will do this
   for you with placeholder values if you skip it, but real SMTP/Samba
   values are still needed before the pipeline can actually run.
2. GCP/Document AI setup (project ID, processor ID, service account key)
   can be done two ways:
   - **Manually**, in `.env`: fill in `GCP_PROJECT_ID`,
     `GCP_DOCAI_PROCESSOR_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, and drop
     the service account key JSON where that last path points.
   - **Or via the setup wizard** after logging in (step 4 below) — as a
     staff user, visit `/gcp-setup/` (or just follow the redirect there,
     since staff users get sent to it automatically until something's
     configured) for step-by-step console links and a form that takes
     the key as a file upload, validated live before saving. See "GCP /
     Document AI Configuration" above for how the two interact.
3. Run `./setup.sh`. It builds the image, starts `web` + `redis` (not
   `samba`/`watcher` yet — those need the real config from steps 1-2
   first), and waits for migrations to finish (these run automatically on
   every `web` startup — see `docker-compose.yml` — not a manual step).
   Then it checks whether a superuser account already exists; if not, it
   runs `manage.py createsuperuser` **interactively**, so you choose your
   own username and password on the spot. Nothing is pre-filled or baked
   into this repo — every fresh install gets its own credentials, not a
   shared default.
4. Log in at `http://localhost:8000/` with that account. **On first login
   you're redirected straight to 2FA setup** — scan the QR code with an
   authenticator app (Google Authenticator, Authy, etc.) and save the
   generated backup codes. This is required before you can reach uploads
   or job status; a password alone isn't enough (see "Authentication &
   Access Control" below).
5. Visit the web UI to upload a document, or drop a PDF into the Samba
   input share once it's running. Either way a `Job` row gets created and
   the file saved, then run through the full OCR/PDF-A pipeline — see
   "Status" above for what's actually been verified working end to end
   versus tested in isolation.
