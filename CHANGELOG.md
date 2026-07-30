# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is semantic-ish and appropriate to a pre-1.0, single-developer
scaffold — a MINOR bump per meaningful milestone, PATCH reserved for fixes
within one.

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
