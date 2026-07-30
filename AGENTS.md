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
- **Google Document AI handles OCR; `ocrmypdf` handles deskew/auto-rotate
  and PDF/A conversion.** Don't reimplement either of these with custom
  logic — call the existing tools.
- **Blank-page detection in `cleanup.py` is custom code, on purpose.**
  Stirling PDF was evaluated for this and removed before implementation —
  its remove-blanks endpoint is binary (delete or don't), with no way to
  express the confidently-blank/borderline two-tier behavior required
  here. See `PROJECT_SPEC.md` "Decisions Changed" for the full reasoning.
  Don't reach for an external tool here without checking that note first.

## Architecture at a glance

```
pipeline/
  ingest.py      - watcher/upload hands off a raw scan here
  cleanup.py     - custom blank-page detection (rasterize + ink coverage)
  ocr.py         - calls Google Document AI (sync, <=15 pages), returns OcrResult
  reassemble.py  - invisible text overlay onto cleaned_path's own pages (no split step)
  pdfa.py        - ocrmypdf: --output-type pdfa, --rotate-pages, --deskew, --optimize
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

**`scripts/smoke-test-ocr.py` is different from the above and is never
run automatically — not by `manage.py test`, not by `test-all.sh`, not
by `--smoke`.** It makes one real, billable call to Google Document AI
(a fraction of a cent, but real money and a real API call) using
whatever's configured in `.env`/`.env.local`, to confirm the actual GCP
setup (credentials, project ID, processor ID, IAM role) works end to
end — not something a mocked test suite can verify. Run it deliberately:
`python scripts/smoke-test-ocr.py [path/to/scan.pdf]` (defaults to the
synthetic `tests/fixtures/sample_scan.pdf` if no path given). See its
own docstring for details.

## Local development

- **Use Python 3.12 locally, matching the Dockerfile (`python:3.12-slim`).**
  A venv built with an older interpreter can resolve dependencies
  differently than the container does (this happened once already: Django
  4.2 locally on Python 3.9 vs. the 5.x the Dockerfile actually ships).
  The container is the source of truth for what actually ships; local dev
  should match it, not the other way around.
- Rebuild the venv against 3.12 if you're not sure what it's running:
  ```
  brew install python@3.12   # if not already installed
  /opt/homebrew/bin/python3.12 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  python -c "import django; print(django.VERSION)"   # expect (5, 2, 16, ...)
  ```
- **Dependencies are pinned exactly, via pip-tools.** `requirements.in` is
  the source of intent (what version range we want); `requirements.txt` is
  autogenerated from it via `pip-compile` and is what both the Dockerfile
  and local venv actually install from — it's a full, transitively-pinned
  lock file. **Never hand-edit `requirements.txt` or `pip install` a
  package version directly** — either goes stale the moment someone else
  runs `pip-compile` again, silently reverting the change.
  - To deliberately upgrade a version: edit the constraint in
    `requirements.in`, then run `pip-compile --upgrade requirements.in`
    (or `pip-compile --upgrade-package <name> requirements.in` for just
    one package) to regenerate `requirements.txt`. Re-run the check /
    makemigrations / test trio locally and in the container afterward,
    same as any other dependency change.
  - `pip-tools` (specifically `pip-compile`) can break under newer `pip`
    releases — it relies on pip's internal (non-public) API. If
    `pip-compile` fails with a `TypeError` about
    `make_requirement_preparer`, downgrade `pip` in the venv running the
    compile (e.g. `pip install "pip==24.3.1"`) rather than assuming
    `requirements.in` itself is broken. See
    `verification-logs/2026-07-30-pin-dependencies.md` for the exact
    failure this produced.
- See `verification-logs/` for dated records of local-vs-container
  verification runs (`manage.py check` / `makemigrations --check --dry-run`
  / `test`), so drift like this can be diffed against a real prior run
  instead of relying on conversation history that doesn't persist between
  sessions.
- **`.env` vs `.env.local`.** `config/settings.py` loads `.env` first via
  `python-dotenv`, then `.env.local` on top (if present), overriding any
  keys in both. `.env` holds the Docker-appropriate values (docker-compose
  already injects these into the container directly via `env_file`, so
  this load is a no-op there) — `.env.local` overrides just the handful of
  keys that need a different value outside Docker, currently
  `GOOGLE_APPLICATION_CREDENTIALS` (a real host path locally vs. the
  in-container `/run/secrets/...` path `.env` has). `.env.local` is
  gitignored and genuinely personal — no `.env.local.example` template,
  unlike `.env`/`.env.example`.
  - The `.env.local` load is skipped when `/.dockerenv` exists. This
    matters because `docker-compose.yml` bind-mounts the whole project
    directory (`.:/app`), so `.env.local` is visible inside the container
    too if it exists on the host — without the guard, its override would
    clobber the correct in-container credentials path with a host path
    that doesn't exist in the container. Don't remove this guard when
    touching the env-loading code in `settings.py`.

## Current state (check before assuming something works)

Most `pipeline/*.py` stage functions still raise `NotImplementedError` —
stubs tied to the spec, not working code. Don't assume a stage works
because the file exists and has the right signature; check for
`NotImplementedError` or a TODO before building on top of it.

`pipeline/cleanup.py`, `pipeline/ocr.py`, and `pipeline/reassemble.py` are
the exceptions: all three are implemented and tested (`pipeline/tests.py`).
`pipeline/split.py` no longer exists — see below.

- `cleanup.py` rasterizes each page via `pdf2image`, measures ink
  coverage, drops confidently-blank pages, and logs borderline ones as
  `ingest.models.BorderlinePage`. Returns `CleanupResult` (cleaned PDF
  path + per-stage counts), not a bare `Path`.
- `ocr.py` sends the cleaned PDF straight to Document AI's *synchronous*
  process endpoint in one call (not one call per page — Document AI
  handles page segmentation itself). Raises
  `ocr.DocumentTooLongForSyncOCR` without calling the API at all for
  documents over the 15-page sync limit (see PROJECT_SPEC.md "OCR -
  Google Document AI"). Returns `OcrResult` (the full serialized Document
  AI response + page count), not `List[str]` of hOCR. **Tests mock the
  Document AI client entirely — no real API calls in the automatic test
  suite, ever** (see "Running tests" above for the one deliberate,
  billable exception: `scripts/smoke-test-ocr.py`).
- `reassemble.py` overlays `OcrResult`'s text as an invisible (Tr 3)
  layer directly onto `cleaned_path`'s own pages via
  `pikepdf.Page.add_overlay()` — no rasterized page images, no
  `split.py`. Positioning uses Document AI's `normalized_vertices`
  (resolution-independent) against each page's real MediaBox size and
  `/Rotate`, read via pikepdf rather than trusted from Document AI's own
  Dimension field. **The `/Rotate` handling is geometrically derived and
  tested three ways** (fixed-point checks, and real poppler rasterization
  confirming a token lands in the visually-correct image quadrant for
  all four rotation values) **but not verified against a real Document
  AI response for an actually-rotated scan** — that would need a real,
  billable API call that hasn't been made. See `reassemble.py`'s module
  docstring for the full caveat before trusting this blindly on a
  rotated real-world document.

`pipeline/split.py` was removed (see `PROJECT_SPEC.md` pipeline stage
list) — it rasterized pages for the *old* per-page-image OCR design.
Since `ocr.py` now sends the whole cleaned PDF in one call and
Document AI's bounding boxes are resolution-independent, nothing needed
those rasterized images anymore. `pipeline/run.py` and
`pipeline/reassemble.py` were updated accordingly (`cleaned_path` in,
`OcrResult` out — no `page_images` parameter), since leaving call sites
referencing a signature or file that no longer exists isn't an option.

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

## Versioning & releases

- **Tag and update `CHANGELOG.md` together, at the end of each meaningful
  chunk of work — not as a follow-up requested later.** This slipped
  once already: the 0.4.1 (Stirling PDF removal) and 0.5.0 (blank-page
  detection) changelog entries and tags were both added after the fact,
  in a separate session, because tagging wasn't done when the work
  actually landed. Reconciling it after the fact means the tag can end
  up pointing at a different commit than the one that added the
  changelog text for it (check `git log` for the actual commit that
  finalized a given entry — don't assume it's the most recent one), and
  is just an easy step to forget once it's decoupled from the work
  itself.
- Use annotated tags (`git tag -a vX.Y.Z <commit> -m "vX.Y.Z - <summary>"`),
  matching the existing tags (`git tag -n99` to see the convention).
