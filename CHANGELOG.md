# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is semantic-ish and appropriate to a pre-1.0, single-developer
scaffold — a MINOR bump per meaningful milestone, PATCH reserved for fixes
within one.

## [0.5.0] - 2026-07-30

First real pipeline stage: `pipeline/cleanup.py` is implemented and
tested, no longer a stub.

### Added
- `pipeline/cleanup.py` — custom blank-page detection. Rasterizes each
  page via `pdf2image`, measures the percentage of non-white ("ink")
  pixels, and applies the existing two-tier threshold
  (`BLANK_PAGE_DROP_THRESHOLD_PCT` / `BLANK_PAGE_REVIEW_THRESHOLD_PCT`):
  confidently-blank pages are dropped losslessly via `pikepdf`;
  borderline pages are kept and logged as `ingest.models.BorderlinePage`
  for manual review, using its existing fields unchanged. Measurement
  rasterizes at 100 DPI rather than the pipeline's 400 DPI archival
  resolution — coverage is a ratio, stable across render resolution, so
  this is a documented speed optimization with no accuracy trade-off.
- `pipeline/tests.py` — first tests for this stage: confidently-blank
  page dropped, non-blank page kept untouched, borderline page kept and
  logged with the correct job/page/coverage, and page order preserved
  across a drop in a multi-page document. Uses synthetic PDFs generated
  at the same DPI `cleanup()` measures at, so results are exact rather
  than fuzzed by resampling.

### Changed
- `cleanup()` now returns a `CleanupResult` (cleaned PDF path plus
  `pages_total`/`_dropped`/`_borderline`) instead of a bare `Path`, so
  `run_pipeline` can log what happened at this stage. `pipeline/run.py`'s
  one call site updated to match — the only stage function whose
  contract changed as a result of actually being implemented.
- Cleaned up the remaining loose threads from the Stirling PDF removal
  (0.4.1): `cleanup.py`'s docstring/comments no longer describe calling
  its API, the unused `STIRLING_PDF_URL` setting and its `.env.example`
  block are gone, and `scripts/test-all.sh`'s stale mention is fixed.

## [0.4.1] - 2026-07-30

Architecture/spec correction, no pipeline code changed — `pipeline/cleanup.py`
is still a stub (see 0.1.0 below); this only updates the docs/infra ahead
of implementing it.

### Changed
- **Removed Stirling PDF from the pipeline architecture**, before any of
  its integration was implemented. A closer look at its actual API showed
  it doesn't cover what this project needs:
  - Its rotation endpoint only supports fixed 90-degree increments, not
    auto skew/orientation detection. `ocrmypdf`'s own `--deskew` and
    `--rotate-pages` flags do this properly and were always going to run
    at the PDF/A conversion stage anyway — that work simply moves there
    instead of being lost.
  - Its remove-blanks endpoint is binary (delete or don't), with no way
    to express the confidently-blank/borderline two-tier requirement, and
    no per-page report of what it removed. Blank-page detection will now
    be custom code in `cleanup.py` (not yet implemented — this entry
    covers the spec/infra correction only, ahead of that work).
  - Updated `PROJECT_SPEC.md` (new "Decisions Changed" section explaining
    the reasoning above), `README.md`, and `AGENTS.md` to match. Removed
    the `stirling-pdf` service, its `depends_on` references, and the
    `stirling-data` volume from `docker-compose.yml`. No dependency
    changes needed — nothing in `requirements.in` had been added
    specifically for Stirling.

## [0.4.0] - 2026-07-30

First version where an uploaded or watched scan actually results in a
real saved file and a tracked `Job` row, rather than a no-op/TODO.

### Added
- `ingest/services.py` — shared job-creation logic for both entry
  points: `create_job_from_upload()` saves a web-uploaded file into
  `SCAN_INPUT_DIR` and creates a pending `Job`; `create_job_from_watched_file()`
  does the equivalent for the watcher (the file is already in
  `SCAN_INPUT_DIR`, so it's recorded rather than re-saved). Both funnel
  through one internal `_create_job()`.
- `watcher/watch.py`: `resolve_default_owner()` and `handle_new_scan()`
  pulled out to module level (previously nested inside `main()`'s
  closure, and unused) so the Samba-drop path is actually wired up and
  testable. A missing/unset `DEFAULT_JOB_OWNER_USERNAME` now raises
  `ImproperlyConfigured` with a clear message instead of silently
  skipping attribution.
- `ingest/tests.py` — first tests in the repo: web upload creates a
  `Job` with the correct owner and a real file on disk; watcher-created
  jobs use the default owner; both land in `Job.Status.PENDING`; the
  watcher fails loudly (doesn't silently continue) when the default
  owner account is missing or unconfigured.

### Changed
- `ingest/views.py`'s `upload()` now calls `create_job_from_upload()`
  instead of computing a path and leaving the actual file write as a
  `# TODO`.
- README.md and PROJECT_SPEC.md: removed stale "single user... personal
  utility, not a hosted service" framing left over from before v0.3.0's
  multi-user/2FA work (the two docs had started contradicting each
  other). Kept "local-network only" where still accurate, worded more
  precisely against what `docker-compose.yml` actually restricts (or
  doesn't). Same fix applied to `AGENTS.md`. README's "Status" section
  updated to reflect that upload/watcher now actually save files and
  create jobs.

### Fixed
- `watcher/watch.py` previously detected new files and only `print()`d
  — no `Job` was ever created for a Samba-dropped scan. Now fixed.

## [0.3.0] - 2026-07-30

### Added
- `accounts` app with a custom `User` model (`AbstractUser` subclass, no
  extra fields yet), set as `AUTH_USER_MODEL` before any migrations exist —
  avoids a painful later swap per Django's own docs on custom user models.
- Enforced two-factor authentication (`django-otp` + `django-two-factor-auth`,
  TOTP + `otp_static` backup codes, no SMS/Twilio).
  `accounts.middleware.Enforce2FAMiddleware` redirects any authenticated
  user without a configured device straight to the setup flow before they
  can reach uploads, job status, or `/admin/` (patched via
  `TWO_FACTOR_PATCH_ADMIN`) — a password alone is never enough.
- `Job.uploaded_by` foreign key; web uploads now record the logged-in user.
- `DEFAULT_JOB_OWNER_USERNAME` setting, for attributing watcher-created
  jobs (no user session involved) to a configured account instead of
  leaving them unowned.
- Per-user job visibility: normal users only see/open their own jobs;
  staff/admin (`is_staff`) see everything (`ingest.views._jobs_visible_to`).
- `job_list` view/template (`/jobs/`) — didn't exist before this change;
  added so the per-user visibility rule had something to apply to.
- Initial migrations for the `accounts` and `ingest` apps.

### Changed
- `ingest` views (`upload`, `job_status`) now require login and are scoped
  through the new visibility rule rather than querying `Job.objects`
  directly.
- `README.md`, `AGENTS.md`, `requirements.txt`, `.env.example` updated for
  auth / 2FA / job-visibility.

## [0.2.0] - 2026-07-30

### Added
- `AGENTS.md` — guidance for coding agents working in the repo (ground
  rules, architecture overview, current-state caveats).
- `scripts/test-all.sh` — Django unit tests by default, `--smoke` flag for
  a full pipeline smoke test against a sample scan.
- Short "Working in this repo" section in `README.md` linking to
  `AGENTS.md`.

### Fixed
- `scripts/test-all.sh`'s smoke test assumed `pipeline.run` had a CLI
  entrypoint (`python -m pipeline.run <input.pdf> --output-dir <dir>`). It
  doesn't — `run_pipeline()` takes a `Job` id, not a file path, and writes
  to `settings.SCAN_OUTPUT_DIR` / `SCAN_FAILED_DIR` rather than a
  passed-in output directory, and has no CLI entrypoint at all. Adjusted
  the script to create a `Job` row and call `run_pipeline(job.id)`
  directly, overriding `SCAN_OUTPUT_DIR` / `SCAN_FAILED_DIR` via env for
  isolation, instead of guessing at a CLI that isn't there.

## [0.1.0] - 2026-07-30

### Added
- Initial repository setup: Python `.gitignore`, MIT `LICENSE`.
- Django project scaffold: `config` project module, `ingest` app (upload
  form, `Job` / `BorderlinePage` models, admin, views, templates),
  Django-RQ worker entrypoint (`worker/entrypoint.py`), watchdog-based
  watcher entrypoint (`watcher/watch.py`).
- Core `pipeline/` module, one file per stage — `ingest`, `cleanup`
  (Stirling PDF), `split`, `ocr` (Google Document AI), `reassemble`,
  `pdfa` (ocrmypdf), `validate` (veraPDF), `output` — plus `run.py`
  orchestrating them in order. All stage functions are stubs
  (`NotImplementedError`) at this point.
- `docker-compose.yml` with `web`, `worker`, `redis`, `samba`
  (`dperson/samba`), `watcher`, and `stirling-pdf` (`frooodle/s-pdf`)
  services, plus a `Dockerfile`.
- `requirements.txt`, `.env.example`, `setup.sh`.
- `README.md` built from `PROJECT_SPEC.md`, with a short intro added on
  top of the spec content.
