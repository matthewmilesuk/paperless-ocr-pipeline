# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is semantic-ish and appropriate to a pre-1.0, single-developer
scaffold — a MINOR bump per meaningful milestone, PATCH reserved for fixes
within one.

## [0.10.0] - 2026-07-30

`pipeline/ingest.py` is implemented — all 8 pipeline stages are now real,
not stubs, for the first time.

### Added
- `pipeline/ingest.py` — a small validation gate, not a transformation:
  confirms the input genuinely opens as a PDF via `pikepdf` (not a
  `.pdf`-extension/header sniff) and returns it unchanged. Three
  distinct, separately-logged failure types instead of a generic
  exception: `EmptyFileError` (zero-byte file), `UnreadablePdfError`
  (`pikepdf.PdfError` — corrupted, or not actually a PDF despite the
  extension), `EmptyPdfError` (opens fine but has zero pages — confirmed
  as a real, separately-constructible case before writing the check:
  `pikepdf.new()` with no pages added saves and reopens without error).
- `pipeline/tests.py` — 4 new tests: a valid PDF passes through
  unchanged; an empty file, a corrupted/non-PDF file, and a
  technically-valid zero-page PDF are each rejected with their own
  distinct exception type.

### Changed
- `pipeline/run.py`: `ingest.ingest()` now takes `job_id` too, matching
  `cleanup`/`orient`/`ocr`'s `(input_path, job_id)` signature, for the
  same `[stage] job=%s` log correlation the rest of the pipeline uses.

### Notes
This is a genuine milestone (all 8 `pipeline/*.py` stages implemented
and tested) but explicitly **not** a claim that the pipeline works:
`pipeline.run.run_pipeline()` has never been run end to end against a
real scan, and still has no failure handling between stages — an
exception from any stage before `validate.py` propagates uncaught rather
than routing to `output.deliver_failed()`; only the veraPDF
validation-failure path is wired up. `PROJECT_SPEC.md`/`AGENTS.md`/
`README.md` updated to state both things clearly side by side, so
"all stages implemented" doesn't get conflated with "the pipeline
works" — an actual end-to-end run against a real scan is the next
milestone, not this one.

## [0.9.0] - 2026-07-30

`pipeline/validate.py` is implemented and tested, plus the veraPDF
Dockerfile infrastructure it depends on.

### Added
- veraPDF added to the Dockerfile: `default-jre-headless` (JRE), `wget`
  (picked over `curl` for footprint: ~5 dependency packages vs ~20,
  confirmed via dry runs of each), and `unzip`, then a non-interactive
  install via a version-pinned URL
  (`software.verapdf.org/releases/1.30/verapdf-greenfield-1.30.2-installer.zip`)
  and a new checked-in `docker/verapdf-auto-install.xml` (IzPack's
  automated-install config, built and verified against the 1.30.2
  installer specifically — noted as needing re-verification, not just a
  version-number swap, if the pinned version is ever bumped, since
  IzPack panel IDs can change between installer versions).
- `pipeline/validate.py` — calls the real `verapdf` CLI (`--flavour 2b
  --format json`) and parses its JSON report, handling three outcomes
  confirmed against real files before writing this (a genuine
  ocrmypdf-produced PDF/A-2b, a deliberately non-compliant PDF, and a
  garbage/unparseable file):
  - Exit 0, `compliant: true` → pass.
  - Exit 1, `compliant: false` → genuine PDF/A-2b non-compliance, logged
    with the specific rule violations (ISO clause + description) from
    `details.ruleSummaries`, not just "failed".
  - Exit 7, `taskException` present instead of `validationResult` → a
    distinct, more alarming failure mode: veraPDF couldn't parse the
    file at all, meaning something upstream in the pipeline produced a
    broken file rather than a legitimate document merely failing a
    compliance check.
  - Returns `ValidationResult` (compliant, parse_failure, a
    human-readable summary, and the full parsed report) instead of a
    bare `bool`.
- `pipeline/tests.py` — 3 new tests, deliberately not mocking veraPDF
  (its own CLI/JSON behavior is what's under test): a real PDF/A-2b
  passes, a non-compliant PDF fails with rule details captured, and a
  garbage file hits the parse-failure path distinctly. Verified passing
  identically both locally (Homebrew `verapdf`) and in a rebuilt Docker
  container.

### Changed
- `pipeline/run.py`: uses `validation_result.compliant` for the
  pass/fail branch and threads `.summary` into
  `output.deliver_failed()`'s `reason=`, instead of the previous
  hardcoded `"veraPDF validation failed"` string.
- `AGENTS.md`: added a pinning note for veraPDF next to the pip-tools
  upgrade guidance (no lockfile mechanism exists for it — bumping the
  version means manually updating the Dockerfile URL and re-verifying
  the automated-install XML), plus a note on a known, already-verified
  version-string mismatch (local Homebrew `verapdf` reports itself as
  "1.30.0", the Dockerfile-pinned installer as "1.30.2" — all 29 tests
  pass identically in both, so treated as a labeling quirk between
  distributions, not investigated further).

## [0.8.0] - 2026-07-30

First pipeline stages touching real external tools (tesseract,
ghostscript, pngquant) rather than pure Python logic or mocked APIs:
`pipeline/orient.py` is added and `pipeline/pdfa.py` is implemented.

### Added
- `pipeline/orient.py` — new stage 3 (between `cleanup.py` and
  `ocr.py`): runs `ocrmypdf --deskew --rotate-pages` on the cleaned PDF
  *before* Document AI or `reassemble.py` add any text layer. Resolves
  the sequencing conflict investigated last session: run at the
  `pdfa.py` stage as originally planned, `--deskew`/`--rotate-pages`
  would have been a silent no-op on every page, every run — confirmed by
  reading ocrmypdf's actual source (`is_ocr_required()` returns `False`,
  logging "skipping all processing on this page", for any page with an
  existing text layer under `--skip-text`).
  - `--rotate-pages` needs a text-orientation signal, so ocrmypdf runs
    tesseract internally as a side effect and embeds a throwaway
    invisible text layer. Stripped before handing off to `ocr.py`.
  - Verified concretely that ocrmypdf's own `--mode strip` does NOT
    remove this: both of ocrmypdf's renderers wrap OCR text in a Form
    XObject that its built-in `strip_invisible_text()` doesn't recurse
    into, so it silently no-ops. `orient.py` reimplements that
    algorithm, generalized to handle nested XObjects — verified with an
    independent scan function, not the same code that did the
    stripping.
- `pipeline/pdfa.py` implemented: `--output-type pdfa --skip-text
  --optimize 3`, deliberately no `--rotate-pages`/`--deskew` (that's
  `orient.py`'s job now, and would be a no-op here regardless).
- `pipeline/tests.py` — 4 new tests, deliberately not mocking ocrmypdf
  (that's the whole thing under test): a 90°-rotated page comes out
  upright (checked by rendering), a 6°-skewed page is measurably
  straightened (via an independent projection-profile skew estimator,
  validated against known angles before use), no invisible text
  survives `orient()`'s strip step (independent scan, including nested
  XObjects), and `pdfa.py` produces PDF/A-2b-flagged output that still
  preserves the existing text layer.

### Fixed
- Dockerfile was missing `pngquant`, which `--optimize 3` hard-requires
  (confirmed: `MissingDependencyError`, not a soft warning) — found by
  actually running `pdfa.py` locally without it. Added alongside the
  existing `ghostscript`/`tesseract-ocr`/`qpdf`; rebuilt the image and
  re-ran the full test suite in the container (not just the local venv,
  where these tools were installed manually for testing) to confirm the
  fix holds there too.

### Changed
- `pipeline/run.py`: `orient()` wired in between `cleanup()` and
  `ocr_document()` — `ocr.ocr_document()` and `reassemble.reassemble()`
  now both operate on `orient()`'s output, not `cleanup()`'s directly.
- `PROJECT_SPEC.md`/`AGENTS.md`/`README.md` pipeline stage lists
  renumbered (1–8) to include the new `orient.py` stage.

## [0.7.0] - 2026-07-30

`pipeline/reassemble.py` is implemented and tested; `pipeline/split.py`
is removed.

### Removed
- `pipeline/split.py` — rasterized the cleaned PDF into per-page images
  for the *old* per-page-image OCR design. Investigated whether
  `reassemble.py` actually needed those images now that `ocr.py` sends
  the whole cleaned PDF to Document AI in one call: it doesn't.
  Document AI's `normalized_vertices` are resolution-independent
  fractions of the page, and `cleanup.py` only ever deletes pages (never
  rasterizes ones it keeps), so the cleaned PDF's own pages are already
  the original scan content at full quality — nothing needed the
  rasterized copies. `run.py`, `reassemble.py`'s signature, and the
  pipeline stage list in `AGENTS.md`/`PROJECT_SPEC.md`/`README.md`
  (renumbered) updated to match.

### Added
- `pipeline/reassemble.py` — overlays Document AI's recognized text as
  an invisible (Tr 3) layer directly onto the cleaned PDF's own pages
  via `pikepdf.Page.add_overlay()`, per token, positioned from
  `normalized_vertices` against each page's actual MediaBox size (read
  via `pikepdf`, not trusted from Document AI's own Dimension field).
  Handles page `/Rotate` explicitly via a derived coordinate transform
  (`_visual_to_raw` / `_text_draw_rotation`), since `/Rotate` is applied
  by the viewer on top of the raw content stream rather than baked into
  it. Verified three ways: fixed-point checks against the derived
  formulas, and real poppler rasterization confirming a token lands in
  the visually-correct image quadrant for all four rotation values —
  **not** verified against a real Document AI response for an actually-
  rotated scan, which would need a real, billable API call that hasn't
  been made (flagged in the module docstring and `AGENTS.md`). Document
  AI's per-element `orientation` field (text detected as sideways within
  an otherwise-upright page) is out of scope — only page-level `/Rotate`
  is handled.
- `pipeline/tests.py` — 7 new tests: fixed-point transform checks, the
  poppler-rendering rotation check (0/90/180/270, two corners each),
  end-to-end overlay producing genuinely extractable text, page order
  preserved across a multi-page document (verified per-page), and a
  page-count-mismatch safety check.
- `reportlab` — new pinned dependency, generates the text-overlay layer.

### Changed
- `README.md`'s Status section, which had gone stale across the
  `cleanup.py`/`ocr.py` sessions (still listed both as
  `NotImplementedError` stubs) — corrected alongside this pass since it
  directly overlapped with what was already being touched.

## [0.6.0] - 2026-07-30

First pipeline stage calling a live external API: `pipeline/ocr.py`

First pipeline stage calling a live external API: `pipeline/ocr.py`
(Google Document AI) is implemented and tested, no longer a stub.

### Added
- `pipeline/ocr.py` — sends the cleaned PDF to Document AI's
  *synchronous* process endpoint in one API call (Document AI segments
  pages itself; this is not a per-page call loop). Checks page count via
  `pikepdf` first and raises `DocumentTooLongForSyncOCR` without calling
  the API at all over the endpoint's 15-page cap — batch processing
  (Cloud Storage, up to 500 pages) would lift this but isn't built yet
  (documented as a known v1 limitation in `PROJECT_SPEC.md`, not an
  oversight). Client uses a location-specific endpoint and Application
  Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS`, no hardcoded
  path). Real API failures — missing/invalid credentials, rejected
  credentials, missing IAM role, quota/rate limit, wrong processor ID —
  each get a distinct, clear log message before the original exception
  re-raises. Returns `OcrResult`: the full Document AI response
  serialized via `Document.to_dict()` (text, per-page layout, bounding
  boxes — nothing discarded) plus a page count.
- `scripts/smoke-test-ocr.py` — deliberate, opt-in script making ONE
  real, billable Document AI call to confirm the actual GCP setup works
  end to end. Never run by `manage.py test` or `test-all.sh`.
- `tests/fixtures/sample_scan.pdf` — synthetic single-page fixture
  (placeholder text via PIL), used by the smoke test script and
  satisfying `test-all.sh --smoke`'s pre-existing (previously unmet)
  expectation of that file.
- `pipeline/tests.py` — 6 new tests, all against a mocked Document AI
  client (real proto-typed responses, not deep mocks): normal document
  processes correctly, over-limit document raises before any client call
  is constructed, and each of the four API error types logs
  distinguishably and re-raises its specific exception type. No real API
  calls in the automatic test suite, ever.

### Changed
- `pipeline/run.py` and `pipeline/reassemble.py` updated to match
  `ocr_document()`'s new contract (cleaned PDF path in, `OcrResult` out,
  instead of pre-split page images in, `List[str]` hOCR out).
  `reassemble.py` itself is still a stub — only its type hint changed to
  stay accurate.

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
