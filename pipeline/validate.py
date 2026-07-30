"""
Step 7: Validate (veraPDF).

Confirms actual PDF/A-2b compliance of the final PDF before it's allowed
anywhere near settings.SCAN_OUTPUT_DIR. Calls the real veraPDF CLI (see
the Dockerfile / docker/verapdf-auto-install.xml for how it gets
installed -- it's a Java tool, not a Python package) and parses its JSON
report.

Three distinct outcomes, confirmed against real files (a genuine
ocrmypdf-produced PDF/A-2b, a deliberately non-compliant PDF, and a
garbage/unparseable file) before writing this -- not assumed from docs:

  - Exit 0, `compliant: true` -> PASS.
  - Exit 1, `compliant: false` -> genuine PDF/A-2b non-compliance.
    Logged with the specific rule violations (ISO clause + description)
    from `details.ruleSummaries`, not just "failed".
  - Exit 7, no `validationResult` at all (`taskException` instead, with
    `type: "PARSE"`) -> veraPDF couldn't even parse the file as a PDF.
    This is a different, more alarming failure mode than a compliance
    miss: it means something upstream in this pipeline (orient.py /
    ocr.py / reassemble.py / pdfa.py) produced a broken file, not that a
    legitimate document happened to violate a PDF/A rule. Logged louder
    for that reason.
"""
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Matches pipeline/pdfa.py's --output-type pdfa, which produces PDF/A-2b.
PDFA_FLAVOUR = "2b"


@dataclass
class ValidationResult:
    """
    Result of validate_pdfa(): whether `pdfa_path` is genuinely PDF/A-2b
    compliant, plus enough detail to log or act on a failure without
    re-parsing veraPDF's report.
    """

    compliant: bool
    parse_failure: bool  # True if veraPDF couldn't parse the file at all
    summary: str  # human-readable; suitable for output.deliver_failed()'s reason
    report: dict  # veraPDF's full parsed JSON report -- nothing discarded


def _rule_violation_summary(rule_summaries: List[dict]) -> str:
    parts = []
    for rule in rule_summaries:
        specification = rule.get("specification", "?")
        clause = rule.get("clause", "?")
        description = rule.get("description", "")
        parts.append(f"{specification} {clause}: {description}")
    return "; ".join(parts)


def validate_pdfa(pdfa_path: Path, job_id: int) -> ValidationResult:
    """
    Runs veraPDF against `pdfa_path` and returns a ValidationResult.

    A compliance failure or a parse failure are both expected, handled
    outcomes represented in the return value, not exceptions. This only
    raises if veraPDF itself can't be invoked at all (not on PATH) or
    produces output that isn't valid JSON -- genuinely unexpected states
    worth failing loudly on, not something to paper over.
    """
    try:
        process = subprocess.run(
            ["verapdf", "--flavour", PDFA_FLAVOUR, "--format", "json", str(pdfa_path)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        logger.error(
            "[validate] job=%s verapdf executable not found on PATH -- is "
            "it installed? (see Dockerfile / docker/verapdf-auto-install.xml): %s",
            job_id,
            exc,
        )
        raise

    try:
        report = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        logger.error(
            "[validate] job=%s veraPDF produced unparseable output (exit "
            "code %d): %s -- stdout: %s -- stderr: %s",
            job_id,
            process.returncode,
            exc,
            process.stdout[:500],
            process.stderr[:500],
        )
        raise

    job_report = report["report"]["jobs"][0]

    if "taskException" in job_report:
        exception_message = job_report["taskException"].get(
            "exceptionMessage", "unknown error"
        )
        logger.error(
            "[validate] job=%s ALARM: %s produced a file veraPDF can't even "
            "parse as a PDF -- this indicates a bug upstream in the "
            "pipeline (orient/ocr/reassemble/pdfa), not a routine PDF/A "
            "compliance issue with a legitimate document: %s",
            job_id,
            pdfa_path,
            exception_message,
        )
        return ValidationResult(
            compliant=False,
            parse_failure=True,
            summary=f"veraPDF could not parse the file as a PDF at all: {exception_message}",
            report=report,
        )

    validation_result = job_report["validationResult"][0]
    compliant = validation_result["compliant"]

    if compliant:
        logger.info("[validate] job=%s PDF/A-%s compliant", job_id, PDFA_FLAVOUR)
        return ValidationResult(
            compliant=True,
            parse_failure=False,
            summary=validation_result.get("statement", "PDF/A compliant"),
            report=report,
        )

    violations = _rule_violation_summary(
        validation_result.get("details", {}).get("ruleSummaries", [])
    )
    logger.error(
        "[validate] job=%s PDF/A-%s validation failed: %s",
        job_id,
        PDFA_FLAVOUR,
        violations or "(no rule details returned)",
    )
    return ValidationResult(
        compliant=False,
        parse_failure=False,
        summary=f"Not PDF/A-{PDFA_FLAVOUR} compliant: {violations}"
        if violations
        else f"Not PDF/A-{PDFA_FLAVOUR} compliant",
        report=report,
    )
