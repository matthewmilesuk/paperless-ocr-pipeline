#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Install Docker Desktop (macOS/Windows) or" >&2
    echo "Docker Engine (Linux) first: https://docs.docker.com/get-docker/" >&2
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "The 'docker compose' plugin is not available. Please update Docker." >&2
    exit 1
fi

if [ ! -f .env ]; then
    echo "No .env found -- copying .env.example to .env."
    echo "Edit .env with your real GCP/SMTP/Samba values before continuing."
    cp .env.example .env
fi

echo "Building the docker-compose stack..."
docker compose build

echo
echo "Starting web + redis (samba/watcher need real config first -- see"
echo "README.md -- so they're left out of this initial setup)..."
docker compose up -d web redis

echo "Waiting for migrations to finish (they run automatically as part of"
echo "web's startup, before gunicorn -- see docker-compose.yml)..."
MIGRATED=false
for _ in $(seq 1 30); do
    if docker compose exec -T web python manage.py migrate --check &> /dev/null; then
        MIGRATED=true
        break
    fi
    sleep 1
done

if [ "$MIGRATED" != "true" ]; then
    echo "Migrations did not complete within 30s -- check 'docker compose logs web'." >&2
    exit 1
fi

echo
echo "Checking for an existing superuser account..."
HAS_SUPERUSER="$(docker compose exec -T web python manage.py shell -c '
from django.contrib.auth import get_user_model
print(get_user_model().objects.filter(is_superuser=True).exists())
' 2>/dev/null | tail -1)"

if [ "$HAS_SUPERUSER" = "True" ]; then
    echo "A superuser account already exists -- skipping account creation."
else
    echo "No superuser account exists yet."
    echo "Create one now -- you'll choose your own username and password;"
    echo "nothing is pre-filled or baked into this repo:"
    echo
    docker compose exec web python manage.py createsuperuser
fi

echo
echo "Setup complete."
echo
echo "Next steps:"
echo "  1. Drop your GCP service account key JSON file at:"
echo "       $(pwd)/gcp-credentials.json"
echo "     (or set GCP_CREDENTIALS_HOST_PATH in .env to point elsewhere)."
echo "  2. Review .env and fill in GCP project/processor IDs, SMTP creds,"
echo "     and Samba credentials."
echo "  3. Visit http://localhost:8000/ and log in with the account above."
echo "     On first login you'll be redirected straight to 2FA setup (TOTP"
echo "     + backup codes) -- this is mandatory, not optional; see"
echo "     README.md 'Authentication & Access Control'."
