# Local venv realignment — 2026-07-30

## Background

Local development had been running Python 3.9 with Django 4.2, but
`requirements.txt` pins `Django>=5.0,<6.0`, and the Dockerfile (`python:3.12-slim`)
correctly resolves to Django 5.2.16 when built. The container is the source of
truth; the local venv was out of sync and has been rebuilt against Python 3.12
to match.

No `.venv` (or `venv`/`env`) existed in the project directory prior to this —
the earlier 3.9/4.2 testing was against some other interpreter, not a
project-local venv. Nothing needed to be backed up or removed.

## Versions confirmed

- Python: `3.12.13` (installed via `brew install python@3.12`, at
  `/opt/homebrew/bin/python3.12`)
- Django: `(5, 2, 16, 'final', 0)` — confirmed via
  `python -c "import django; print(django.VERSION)"`

This matches the version resolved inside the `web` Docker image built from
this same `requirements.txt` (verified separately against the container on
2026-07-30, see prior session).

## `python manage.py check`

```
System check identified no issues (0 silenced).
```

## `python manage.py makemigrations --check --dry-run`

```
No changes detected
```

## `python manage.py test`

```
Creating test database for alias 'default'...
....
----------------------------------------------------------------------
Ran 4 tests in 0.341s

OK
Destroying test database for alias 'default'...
Found 4 test(s).
System check identified no issues (0 silenced).
[watcher] created job 1 for /var/folders/v9/8m41x1gj1bq57pyhfx5n_jtm0000gn/T/tmporc1o7ir/dropped.pdf (owner=admin)
```

Note: the `Found 4 test(s)` / `System check` lines and the `[watcher]` log
line print after `OK` / `Destroying test database`, rather than before the
test run as Django normally orders them. This also occurred in the
container run and appears to be stdout/stderr buffering interleaving
inherent to the test/watcher code, not a Python 3.9-vs-3.12 or
4.2-vs-5.x discrepancy.

## Result

Local (Python 3.12 / Django 5.2.16) and the Docker container (`python:3.12-slim`,
same `requirements.txt`) now produce identical `check` / `makemigrations --check`
/ `test` output. No behavioral differences found between local and container
beyond the Python/Django version drift that this realignment fixes.
