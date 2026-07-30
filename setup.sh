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
echo "Build complete."
echo
echo "Next steps:"
echo "  1. Drop your GCP service account key JSON file at:"
echo "       $(pwd)/gcp-credentials.json"
echo "     (or set GCP_CREDENTIALS_HOST_PATH in .env to point elsewhere)."
echo "  2. Review .env and fill in GCP project/processor IDs, SMTP creds,"
echo "     and Samba credentials."
echo "  3. Start the stack with: docker compose up -d"
echo "  4. Run initial migrations with: docker compose exec web python manage.py migrate"
