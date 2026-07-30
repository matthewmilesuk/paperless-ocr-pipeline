"""
Django settings for the paperless-ocr-pipeline project.

Single-user, local-network deployment — see PROJECT_SPEC.md for the
full architecture and rationale.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

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
    "django_rq",
    "ingest",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

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

STIRLING_PDF_URL = os.environ.get("STIRLING_PDF_URL", "http://stirling-pdf:8080")

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
