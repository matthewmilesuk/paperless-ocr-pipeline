"""
Django settings for the paperless-ocr-pipeline project.

Single-user, local-network deployment — see PROJECT_SPEC.md for the
full architecture and rationale.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env holds the Docker-appropriate values (docker-compose's env_file
# already injects these directly into the container's environment before
# Python starts, so this load is a no-op there -- it only fills gaps when
# running outside Docker, e.g. the local venv). load_dotenv() never
# overrides a variable that's already set in os.environ.
load_dotenv(BASE_DIR / ".env")

# .env.local overrides individual keys that differ when running outside
# Docker (see AGENTS.md "Local development" -- currently just
# GOOGLE_APPLICATION_CREDENTIALS, which needs a real host path locally vs.
# the in-container /run/secrets/... path .env has). Guarded to skip inside
# a container: docker-compose.yml bind-mounts the whole project directory
# (`.:/app`), so .env.local is visible to the container too if it exists
# on the host -- without this guard its override=True would clobber the
# correct Docker value with a host path that doesn't exist in the
# container. /.dockerenv is created by the Docker runtime itself, not by
# anything in this repo.
if not Path("/.dockerenv").exists():
    load_dotenv(BASE_DIR / ".env.local", override=True)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_static",
    "django_otp.plugins.otp_totp",
    "two_factor",
    "django_rq",
    "accounts",
    "ingest",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "accounts.middleware.Enforce2FAMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Custom user model -- must be set before the first migration ever runs
# (see accounts/models.py). Do not change this once real data exists.
AUTH_USER_MODEL = "accounts.User"

# two_factor's login view replaces Django's default -- see config/urls.py
# and accounts/middleware.py for enforcement of "no bare-password access".
LOGIN_URL = "two_factor:login"
LOGIN_REDIRECT_URL = "job_list"

# Patches django.contrib.admin's login to also require 2FA, per explicit
# product decision that /admin/ is in-scope for enforcement, not exempt.
TWO_FACTOR_PATCH_ADMIN = True
OTP_TOTP_ISSUER = "Paperless OCR Pipeline"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Django-RQ ------------------------------------------------------------
RQ_QUEUES = {
    "default": {
        "HOST": os.environ.get("REDIS_HOST", "redis"),
        "PORT": int(os.environ.get("REDIS_PORT", 6379)),
        "DB": 0,
        "DEFAULT_TIMEOUT": 900,
    },
}

# --- Email (job-completion notifications) ---------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("SMTP_HOST", "")
EMAIL_PORT = int(os.environ.get("SMTP_PORT", 587))
EMAIL_HOST_USER = os.environ.get("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
DEFAULT_FROM_EMAIL = os.environ.get("SMTP_FROM", "paperless-ocr-pipeline@localhost")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")

# --- Pipeline-specific settings --------------------------------------------
SCAN_INPUT_DIR = os.environ.get("SCAN_INPUT_DIR", "/data/input")
SCAN_OUTPUT_DIR = os.environ.get("SCAN_OUTPUT_DIR", "/data/output")
SCAN_FAILED_DIR = os.environ.get("SCAN_FAILED_DIR", "/data/failed")

GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_DOCAI_LOCATION = os.environ.get("GCP_DOCAI_LOCATION", "us")
GCP_DOCAI_PROCESSOR_ID = os.environ.get("GCP_DOCAI_PROCESSOR_ID", "")

# Ink-coverage thresholds for blank-page detection (see PROJECT_SPEC.md).
BLANK_PAGE_DROP_THRESHOLD_PCT = float(
    os.environ.get("BLANK_PAGE_DROP_THRESHOLD_PCT", 0.5)
)
BLANK_PAGE_REVIEW_THRESHOLD_PCT = float(
    os.environ.get("BLANK_PAGE_REVIEW_THRESHOLD_PCT", 3.0)
)

# Synchronous vs async cutover (see PROJECT_SPEC.md "Processing Mode").
SYNCHRONOUS_BATCH_SIZE_LIMIT = int(os.environ.get("SYNCHRONOUS_BATCH_SIZE_LIMIT", 5))

# --- Multi-user / job ownership --------------------------------------------
# Username of the account that watcher-created jobs (Samba drops -- no user
# session involved) are attributed to. Must be an existing user (e.g. an
# admin created via `manage.py createsuperuser`). See AGENTS.md "Auth & job
# visibility" -- watcher jobs must not be left with a null owner.
DEFAULT_JOB_OWNER_USERNAME = os.environ.get("DEFAULT_JOB_OWNER_USERNAME", "")
