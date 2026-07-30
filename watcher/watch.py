#!/usr/bin/env python
"""
Entrypoint for the `watcher` docker-compose service.

Watches SCAN_INPUT_DIR (the Samba input share) for new PDFs dropped by
the scanner and records a pending Job for each one via
ingest.services.create_job_from_watched_file() -- the same job-creation
logic the web upload view uses (see ingest/services.py), so there is
exactly one code path regardless of how a job started (see
PROJECT_SPEC.md "Ingest"). Doesn't trigger the pipeline itself yet; see
the TODO below.
"""
import os
import time


def resolve_default_owner():
    """
    Look up the account watcher-created jobs get attributed to (no user
    session exists for a watcher-triggered job -- see
    settings.DEFAULT_JOB_OWNER_USERNAME / AGENTS.md "Auth & job
    visibility"). Raises ImproperlyConfigured if it's unset or doesn't
    match a real user, rather than letting a misconfigured watcher
    silently create unattributed jobs.
    """
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ImproperlyConfigured

    username = settings.DEFAULT_JOB_OWNER_USERNAME
    if not username:
        raise ImproperlyConfigured(
            "DEFAULT_JOB_OWNER_USERNAME is not set -- cannot attribute "
            "watcher-created jobs. Set it in .env and restart."
        )

    User = get_user_model()
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        raise ImproperlyConfigured(
            f"DEFAULT_JOB_OWNER_USERNAME={username!r} does not match any "
            f"existing user -- cannot attribute watcher-created jobs. "
            f"Create that account (manage.py createsuperuser) or fix the "
            f"setting."
        )


def handle_new_scan(path):
    """
    Record a pending Job for a file that has appeared in SCAN_INPUT_DIR.
    Kept independent of the watchdog Observer loop so it's testable on
    its own (see ingest/tests.py).

    TODO: guard against partially-written files (e.g. wait for file size
    to stabilize) before calling this. TODO: enqueue
    pipeline.run.run_pipeline via django_rq once the pipeline stages are
    implemented -- see AGENTS.md "Current state". Not done yet since
    every stage still raises NotImplementedError.
    """
    from ingest.services import create_job_from_watched_file

    owner = resolve_default_owner()
    job = create_job_from_watched_file(path, owner=owner)
    print(f"[watcher] created job {job.id} for {path} (owner={owner.username})")
    return job


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()

    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class ScanHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory or not event.src_path.lower().endswith(".pdf"):
                return
            try:
                handle_new_scan(event.src_path)
            except ImproperlyConfigured as exc:
                print(f"[watcher] ERROR: {exc}")

    observer = Observer()
    observer.schedule(ScanHandler(), settings.SCAN_INPUT_DIR, recursive=False)
    observer.start()
    print(f"[watcher] watching {settings.SCAN_INPUT_DIR} for new scans...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
