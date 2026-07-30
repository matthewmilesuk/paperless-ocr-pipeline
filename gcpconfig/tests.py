import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_static.models import StaticDevice
from google.api_core import exceptions as google_exceptions
from google.auth.exceptions import DefaultCredentialsError

from .models import Configuration, get_configuration, is_configured
from .validation import validate_configuration

User = get_user_model()


def _verified_client(client, user, password):
    """Same pattern as ingest/tests.py's helper: log in and mark a
    backup-code device verified for the session, matching what
    two_factor's real flow leaves behind."""
    assert client.login(username=user.username, password=password)
    device = StaticDevice.objects.create(user=user, name="backup", confirmed=True)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


def _key_file():
    content = json.dumps({"type": "service_account", "project_id": "x"}).encode()
    return SimpleUploadedFile("key.json", content, content_type="application/json")


class ConfigurationModelTests(TestCase):
    def test_get_configuration_none_when_unset(self):
        self.assertIsNone(get_configuration())

    def test_get_configuration_returns_the_row_once_set(self):
        Configuration.objects.create(
            gcp_project_id="proj",
            gcp_docai_processor_id="proc",
            gcp_credentials_path="/data/secrets/gcp-credentials.json",
        )
        self.assertEqual(get_configuration().gcp_project_id, "proj")

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_is_configured_false_when_nothing_set_anywhere(self):
        self.assertFalse(is_configured())

    @override_settings(
        GCP_PROJECT_ID="p", GCP_DOCAI_PROCESSOR_ID="pr", GOOGLE_APPLICATION_CREDENTIALS="/x.json"
    )
    def test_is_configured_true_via_settings_fallback_with_no_db_row(self):
        self.assertTrue(is_configured())

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_is_configured_true_via_db_row_even_when_settings_are_empty(self):
        Configuration.objects.create(
            gcp_project_id="p",
            gcp_docai_processor_id="pr",
            gcp_credentials_path="/data/secrets/gcp-credentials.json",
        )
        self.assertTrue(is_configured())


class ValidateConfigurationTests(TestCase):
    """
    validate_configuration()'s own logic, independent of the wizard view
    -- mocks pipeline.ocr's client-building, never calls the real
    Document AI API (matching pipeline/tests.py's OcrDocumentTests: no
    real, billable calls anywhere in the automatic test suite).
    """

    @mock.patch("gcpconfig.validation._build_client")
    def test_success(self, mock_build_client):
        mock_client = mock.MagicMock()
        mock_build_client.return_value = mock_client

        ok, message = validate_configuration("proj", "proc", "/tmp/creds.json")

        self.assertTrue(ok)
        self.assertEqual(message, "")
        mock_client.get_processor.assert_called_once()

    @mock.patch("gcpconfig.validation._build_client")
    def test_bad_credentials(self, mock_build_client):
        mock_build_client.side_effect = DefaultCredentialsError("no credentials found")

        ok, message = validate_configuration("proj", "proc", "/tmp/creds.json")

        self.assertFalse(ok)
        self.assertIn("credentials not found", message)

    @mock.patch("gcpconfig.validation._build_client")
    def test_wrong_processor_id(self, mock_build_client):
        mock_client = mock.MagicMock()
        mock_client.get_processor.side_effect = google_exceptions.NotFound("not found")
        mock_build_client.return_value = mock_client

        ok, message = validate_configuration("proj", "wrong-processor", "/tmp/creds.json")

        self.assertFalse(ok)
        self.assertIn("processor not found", message)

    @mock.patch("gcpconfig.validation._build_client")
    def test_missing_iam_role(self, mock_build_client):
        mock_client = mock.MagicMock()
        mock_client.get_processor.side_effect = google_exceptions.PermissionDenied("denied")
        mock_build_client.return_value = mock_client

        ok, message = validate_configuration("proj", "proc", "/tmp/creds.json")

        self.assertFalse(ok)
        self.assertIn("permission denied", message.lower())


class WizardViewTests(TestCase):
    def setUp(self):
        self.secrets_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.secrets_dir, ignore_errors=True)
        override = override_settings(GCP_CREDENTIALS_UPLOAD_DIR=self.secrets_dir)
        override.enable()
        self.addCleanup(override.disable)

        self.staff = User.objects.create_user("staffuser", password="pw12345", is_staff=True)
        self.regular = User.objects.create_user("regularuser", password="pw12345", is_staff=False)

    def test_non_staff_cannot_reach_wizard(self):
        # user_passes_test (no raise_exception) redirects to LOGIN_URL on
        # failure rather than returning 403 -- same convention Django's
        # own admin uses for staff-only pages. The regular user is
        # already authenticated, so two_factor's login view bounces them
        # straight on to LOGIN_REDIRECT_URL rather than showing a form;
        # either way, they never see the wizard.
        client = _verified_client(self.client, self.regular, "pw12345")
        response = client.get(reverse("gcpconfig_wizard"), follow=True)
        self.assertNotContains(response, "credentials_file")

    def test_staff_get_shows_form(self):
        client = _verified_client(self.client, self.staff, "pw12345")
        response = client.get(reverse("gcpconfig_wizard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Document AI")

    @mock.patch("gcpconfig.views.validate_configuration")
    def test_successful_submission_saves_configuration_and_writes_file(self, mock_validate):
        mock_validate.return_value = (True, "")
        client = _verified_client(self.client, self.staff, "pw12345")

        response = client.post(
            reverse("gcpconfig_wizard"),
            {
                "gcp_project_id": "my-project",
                "gcp_docai_processor_id": "abc123",
                "credentials_file": _key_file(),
            },
        )

        self.assertRedirects(response, reverse("gcpconfig_wizard"))
        config = Configuration.objects.get()
        self.assertEqual(config.gcp_project_id, "my-project")
        self.assertEqual(config.gcp_docai_processor_id, "abc123")
        self.assertEqual(config.updated_by, self.staff)

        credentials_path = Path(config.gcp_credentials_path)
        self.assertTrue(credentials_path.exists())
        self.assertEqual(credentials_path.parent, Path(self.secrets_dir))
        # 0600 -- readable/writable by owner only.
        self.assertEqual(oct(credentials_path.stat().st_mode)[-3:], "600")

    @mock.patch("gcpconfig.views.validate_configuration")
    def test_failed_validation_does_not_save_configuration_or_leave_temp_file(
        self, mock_validate
    ):
        mock_validate.return_value = (
            False,
            "Document AI processor not found -- check the project ID, location, and processor ID: boom",
        )
        client = _verified_client(self.client, self.staff, "pw12345")

        response = client.post(
            reverse("gcpconfig_wizard"),
            {
                "gcp_project_id": "my-project",
                "gcp_docai_processor_id": "wrong-id",
                "credentials_file": _key_file(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "processor not found")
        self.assertFalse(Configuration.objects.exists())
        self.assertEqual(list(Path(self.secrets_dir).glob("*")), [])

    @mock.patch("gcpconfig.views.validate_configuration")
    def test_failed_validation_does_not_clobber_existing_working_configuration(
        self, mock_validate
    ):
        existing_path = Path(self.secrets_dir) / "gcp-credentials.json"
        existing_path.write_bytes(b'{"type": "service_account", "marker": "original"}')
        Configuration.objects.create(
            gcp_project_id="original-project",
            gcp_docai_processor_id="original-processor",
            gcp_credentials_path=str(existing_path),
        )
        mock_validate.return_value = (False, "Document AI quota/rate limit exceeded: boom")
        client = _verified_client(self.client, self.staff, "pw12345")

        client.post(
            reverse("gcpconfig_wizard"),
            {
                "gcp_project_id": "new-project",
                "gcp_docai_processor_id": "new-processor",
                "credentials_file": _key_file(),
            },
        )

        config = Configuration.objects.get()
        self.assertEqual(config.gcp_project_id, "original-project")
        self.assertIn(b"original", existing_path.read_bytes())

    def test_non_json_file_rejected_before_any_validation_call(self):
        client = _verified_client(self.client, self.staff, "pw12345")
        bad_file = SimpleUploadedFile("key.json", b"not json at all", content_type="application/json")

        with mock.patch("gcpconfig.views.validate_configuration") as mock_validate:
            response = client.post(
                reverse("gcpconfig_wizard"),
                {
                    "gcp_project_id": "my-project",
                    "gcp_docai_processor_id": "abc123",
                    "credentials_file": bad_file,
                },
            )

        mock_validate.assert_not_called()
        self.assertFalse(Configuration.objects.exists())
        self.assertContains(response, "valid JSON")


class MiddlewareTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staffperson", password="pw12345", is_staff=True)
        self.regular = User.objects.create_user("regularperson", password="pw12345", is_staff=False)

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_unconfigured_staff_redirected_to_wizard(self):
        client = _verified_client(self.client, self.staff, "pw12345")
        response = client.get("/jobs/")
        self.assertRedirects(response, reverse("gcpconfig_wizard"))

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_unconfigured_non_staff_not_redirected(self):
        client = _verified_client(self.client, self.regular, "pw12345")
        response = client.get("/jobs/")
        self.assertEqual(response.status_code, 200)

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_configured_via_db_staff_not_redirected(self):
        Configuration.objects.create(
            gcp_project_id="p",
            gcp_docai_processor_id="pr",
            gcp_credentials_path="/data/secrets/gcp-credentials.json",
        )
        client = _verified_client(self.client, self.staff, "pw12345")
        response = client.get("/jobs/")
        self.assertEqual(response.status_code, 200)

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_wizard_url_itself_never_redirect_loops(self):
        client = _verified_client(self.client, self.staff, "pw12345")
        response = client.get(reverse("gcpconfig_wizard"))
        self.assertEqual(response.status_code, 200)
