#!/usr/bin/env python
"""
Entrypoint for the `worker` docker-compose service.

Same codebase as `web`, different process: this boots Django just far
enough to get settings/app registry available, then starts a Django-RQ
worker listening on the "default" queue (see RQ_QUEUES in
config/settings.py). Jobs are enqueued from ingest.views.upload() and
from watcher/watch.py via pipeline.run.run_pipeline.
"""
import os

import django


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from django.core.management import call_command

    call_command("rqworker", "default")


if __name__ == "__main__":
    main()
