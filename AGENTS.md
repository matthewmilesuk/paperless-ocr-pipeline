# AGENTS.md

Guidance for any coding agent (Claude Code or otherwise) working in this
repo. Read this before making changes. See `PROJECT_SPEC.md` for the full
architecture and reasoning — this file is about how to work in the repo,
not what the project does.

## What this project is

A local-network, multi-user document pipeline (enforced 2FA) that turns raw
scans from an HP N9120 FN2 (duplex, 400 DPI, color) into validated,
archival-quality PDF/A files with a searchable text layer, for ingestion
into Paperless-NGX.

**The goal is the best possible output PDF** — not a full document
management system, not a Paperless replacement. Classification, tagging,
and correspondent/title assignment stay Paperless-NGX's job. This repo stops
once a validated PDF/A lands in the output folder.

## Ground rules

- **Don't touch credentials or `.env`.** `.env.example` documents what's
  needed; `.env` itself is gitignored and never committed. If a task
  requires a new env var, add it to `.env.example` with a comment, not a
  real value.
- **Don't silently drop pages.** The blank-page-detection logic has a
  deliberate asymmetry: confidently-blank pages get dropped, borderline
  ones are kept in the output and logged instead. This is intentional —
  the source paper may be shredded, so a false-positive drop is
  unrecoverable. Don't "simplify" this to a single threshold without
  flagging it first.
- **Don't downscale the source scan to manage file size.** File size is
  controlled at the `ocrmypdf --optimize` step, after OCR, not by
  compressing the input. The 400 DPI color scan is the archival source of
  truth.
- **One PDF in, one PDF out.** Page order must be preserved (minus
  confidently-dropped blanks). No stage should produce multiple output
  files for a single input scan.
- **Stirling PDF handles cleanup (deskew, blank-page removal); Google
  Document AI handles OCR.** Don't reimplement either of these with custom
  logic — call the existing tools. If Stirling PDF's API can't do
  something we need, say so rather than quietly hand-rolling a
  replacement.

## Architecture at a glance

```
pipeline/
  ingest.py      - watcher/upload hands off a raw scan here
  cleanup.py     - calls Stirling PDF API: deskew, blank-page removal
  split.py       - splits into per-page images (in-memory/temp only)
  ocr.py         - calls Google Document AI, returns hOCR per page
  reassemble.py  - rebuilds one PDF, original order, invisible text overlay
  pdfa.py        - ocrmypdf: --output-type pdfa, --optimize
  validate.py    - veraPDF check; failure -> failed/ folder, not output/
  output.py      - writes to the folder Paperless-NGX watches
  run.py         - orchestrates the above, in this order
```

Each stage is a plain function that takes a file path (or path-like object)
and returns one. Keep them independently testable — no stage should require
the full pipeline to run in order to be unit tested.

## Running tests

Use `./scripts/test-all.sh` for the full suite. Individual pieces:

```
# Django unit tests
python manage.py test

# Full pipeline smoke test (once pipeline stages are implemented —
# currently stubbed, see below)
./scripts/test-all.sh --smoke
```

## Local development

- **Use Python 3.12 locally, matching the Dockerfile (`python:3.12-slim`).**
  `requirements.txt` pins `Django>=5.0,<6.0` without an upper patch bound,
  so a venv built with an older interpreter can silently resolve to an
  older Django major version (e.g. 4.2 on Python 3.9) with no error until
  something actually breaks. The container is the source of truth for what
  actually ships; local dev should match it, not the other way around.
- Rebuild the venv against 3.12 if you're not sure what it's running:
  ```
  brew install python@3.12   # if not already installed
  /opt/homebrew/bin/python3.12 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  python -c "import django; print(django.VERSION)"   # expect (5, x, x, ...)
  ```
- See `verification-logs/` for dated records of local-vs-container
  verification runs (`manage.py check` / `makemigrations --check --dry-run`
  / `test`), so drift like this can be diffed against a real prior run
  instead of relying on conversation history that doesn't persist between
  sessions.

## Current state (check before assuming something works)

As of the initial scaffold, `pipeline/*.py` stage functions raise
`NotImplementedError` — they're stubs tied to the spec, not working code.
Don't assume a stage works because the file exists and has the right
signature; check for `NotImplementedError` or a TODO before building on
top of it.

## Auth & job visibility

- **Custom user model.** `AUTH_USER_MODEL = "accounts.User"` (see
  `accounts/models.py`), not Django's default `auth.User`. Never assume
  the stock user model — import from `django.conf.settings.AUTH_USER_MODEL`
  or `django.contrib.auth.get_user_model()`, not
  `django.contrib.auth.models.User`.
- **2FA is enforced, not optional.** `accounts/middleware.py` redirects any
  authenticated user without a configured TOTP/backup-code device straight
  to setup, before they can reach uploads, job status, or `/admin/`
  (patched via `TWO_FACTOR_PATCH_ADMIN`). A password alone never grants
  access. This applies only to the Django web front end — the
  Samba/watcher/worker side has no auth concept and isn't affected.
- **Job visibility is per-user, unless staff.** `Job.uploaded_by` scopes
  everything: normal users only see/open their own jobs, `is_staff`
  accounts see all of them. Any new view or queryset over `Job` must go
  through this rule (see `ingest.views._jobs_visible_to`) rather than
  querying `Job.objects` directly — don't add an "unfiltered by default"
  code path.
- **Watcher-created jobs still need an owner.** Watcher-triggered jobs have
  no user session, so they're attributed to
  `settings.DEFAULT_JOB_OWNER_USERNAME` instead of being left unowned. If
  that username is unset or doesn't match a real account, fail loudly
  rather than silently leaving `uploaded_by` null.

## Style / conventions

- Python, Django conventions throughout (`web`, `worker`, `watcher` as
  separate entrypoints sharing the `pipeline/` module).
- Prefer explicit, readable pipeline code over cleverness — this runs
  unattended against irreplaceable scans, so failures need to be loud and
  traceable, not silently swallowed.
- Log liberally at each pipeline stage boundary (which file, which stage,
  pass/fail) — this is a small self-hosted tool with no dashboard beyond
  Django admin, the job list view, and logs, so logs are the primary way
  of finding out what happened to a given scan.
