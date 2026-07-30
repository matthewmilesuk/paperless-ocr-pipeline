#!/usr/bin/env python
"""
Entrypoint for the `watcher` docker-compose service.

Watches SCAN_INPUT_DIR (the Samba input share) for new PDFs dropped by
the scanner and kicks off the same pipeline entrypoint the web upload
view uses, so there is exactly one code path regardless of how a job
started (see PROJECT_SPEC.md "Ingest").
"""
import os
import time

import django


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from django.conf import settings
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class ScanHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory or not event.src_path.lower().endswith(".pdf"):
                return
            self._handle_new_file(event.src_path)

        def _handle_new_file(self, path):
            # TODO: create ingest.models.Job(source=Job.Source.WATCHER, ...)
            # and enqueue pipeline.run.run_pipeline via django_rq, same as
            # ingest.views.upload(). Guard against partially-written files
            # (e.g. wait for file size to stabilize) before enqueuing.
            print(f"[watcher] new file detected: {path}")

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
