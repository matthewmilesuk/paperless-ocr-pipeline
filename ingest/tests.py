import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_static.models import StaticDevice

from gcpconfig.models import Configuration

from .models import Job
from .views import upload

User = get_user_model()


def _verified_client(client, user, password):
    """
    Log `client` in as `user` with a confirmed backup-code device already
    marked verified for the session -- mirrors what two_factor's real
    login flow leaves behind, without driving the wizard UI in a test.
    """
    assert client.login(username=user.username, password=password)
    device = StaticDevice.objects.create(user=user, name="backup", confirmed=True)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


class UploadViewTests(TestCase):
    def setUp(self):
        self.input_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        override = override_settings(SCAN_INPUT_DIR=self.input_dir)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user("alice", password="pw12345")

    @mock.patch("ingest.views.django_rq.get_queue")
    def test_upload_creates_pending_job_with_owner_and_saves_file(self, get_queue):
        client = _verified_client(self.client, self.user, "pw12345")
        upload = SimpleUploadedFile(
            "scan.pdf", b"%PDF-1.4 fake scan content", content_type="application/pdf"
        )

        response = client.post("/", {"file": upload})

        job = Job.objects.get()
        self.assertEqual(job.uploaded_by, self.user)
        self.assertEqual(job.source, Job.Source.UPLOAD)
        self.assertEqual(job.status, Job.Status.PENDING)
        self.assertEqual(job.original_filename, "scan.pdf")

        saved_path = Path(job.input_path)
        self.assertTrue(saved_path.exists(), f"{saved_path} was never written")
        self.assertEqual(saved_path.read_bytes(), b"%PDF-1.4 fake scan content")
        self.assertTrue(str(saved_path).startswith(self.input_dir))

        self.assertRedirects(response, f"/jobs/{job.id}/")
        get_queue.return_value.enqueue.assert_called_once_with(
            "pipeline.run.run_pipeline", job.id
        )


class UploadConfiguredCheckTests(TestCase):
    """
    upload()'s own is_configured() pre-flight check (ingest/views.py) --
    refuses to queue a job that would just fail confusingly at the
    ocr.py stage. Exercised at the two different levels it actually
    matters at: non-staff via the normal Client (gcpconfig's
    RequireGcpConfigMiddleware is staff-scoped, so non-staff really do
    reach this code path in practice); staff via a direct view call
    that bypasses middleware entirely, since in normal navigation a
    staff user would already have been redirected to the setup wizard
    before ever reaching this view while unconfigured -- see
    gcpconfig/middleware.py and the docstring on upload() itself.
    """

    def setUp(self):
        self.input_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        override = override_settings(SCAN_INPUT_DIR=self.input_dir)
        override.enable()
        self.addCleanup(override.disable)

    def _upload_file(self):
        return SimpleUploadedFile(
            "scan.pdf", b"%PDF-1.4 fake scan content", content_type="application/pdf"
        )

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_non_staff_upload_blocked_with_message_when_unconfigured(self):
        user = User.objects.create_user("bob", password="pw12345", is_staff=False)
        client = _verified_client(self.client, user, "pw12345")

        response = client.post("/", {"file": self._upload_file()})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "administrator")
        self.assertFalse(Job.objects.exists())

    @mock.patch("ingest.views.django_rq.get_queue")
    def test_upload_allowed_when_configured_via_db(self, get_queue):
        Configuration.objects.create(
            gcp_project_id="db-project",
            gcp_docai_processor_id="db-processor",
            gcp_credentials_path="/data/secrets/gcp-credentials.json",
        )
        user = User.objects.create_user("carol", password="pw12345", is_staff=False)
        client = _verified_client(self.client, user, "pw12345")

        response = client.post("/", {"file": self._upload_file()})

        self.assertTrue(Job.objects.exists())
        self.assertEqual(response.status_code, 302)

    @override_settings(
        GCP_PROJECT_ID="", GCP_DOCAI_PROCESSOR_ID="", GOOGLE_APPLICATION_CREDENTIALS=""
    )
    def test_staff_upload_shows_setup_message_when_unconfigured(self):
        staff = User.objects.create_user("dave", password="pw12345", is_staff=True)
        request = RequestFactory().post("/", {"file": self._upload_file()})
        request.user = staff

        response = upload(request)

        self.assertContains(response, "complete GCP setup")
        self.assertFalse(Job.objects.exists())


class WatcherJobCreationTests(TestCase):
    def setUp(self):
        self.input_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        override = override_settings(
            SCAN_INPUT_DIR=self.input_dir,
            DEFAULT_JOB_OWNER_USERNAME="admin",
        )
        override.enable()
        self.addCleanup(override.disable)

    def test_watcher_creates_pending_job_with_default_owner(self):
        from watcher.watch import handle_new_scan

        admin = User.objects.create_user("admin", password="pw12345")
        scan_path = Path(self.input_dir) / "dropped.pdf"
        scan_path.write_bytes(b"%PDF-1.4 dropped by scanner")

        job = handle_new_scan(str(scan_path))

        self.assertEqual(job.uploaded_by, admin)
        self.assertEqual(job.source, Job.Source.WATCHER)
        self.assertEqual(job.status, Job.Status.PENDING)
        self.assertEqual(job.original_filename, "dropped.pdf")
        self.assertEqual(job.input_path, str(scan_path))

        # The watcher must not rewrite/move the file it found -- it's
        # already exactly where it needs to be (see ingest/services.py).
        self.assertEqual(scan_path.read_bytes(), b"%PDF-1.4 dropped by scanner")

    def test_watcher_fails_loudly_when_default_owner_account_missing(self):
        from watcher.watch import handle_new_scan

        scan_path = Path(self.input_dir) / "dropped.pdf"
        scan_path.write_bytes(b"data")

        with self.assertRaises(ImproperlyConfigured):
            handle_new_scan(str(scan_path))

        self.assertEqual(Job.objects.count(), 0)

    def test_watcher_fails_loudly_when_setting_unset(self):
        from watcher.watch import handle_new_scan

        with override_settings(DEFAULT_JOB_OWNER_USERNAME=""):
            scan_path = Path(self.input_dir) / "dropped.pdf"
            scan_path.write_bytes(b"data")

            with self.assertRaises(ImproperlyConfigured):
                handle_new_scan(str(scan_path))

        self.assertEqual(Job.objects.count(), 0)
