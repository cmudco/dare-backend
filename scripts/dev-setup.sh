#!/usr/bin/env bash
# One-command local dev setup for the DARE backend stack.
# Usage: scripts/dev-setup.sh [--demo-user]
set -euo pipefail

cd "$(dirname "$0")/.."
DEMO_USER=false
[[ "${1:-}" == "--demo-user" ]] && DEMO_USER=true

WEB_PORT="${WEB_PORT:-8000}"
DEMO_EMAIL="demo@dare.local"
DEMO_PASSWORD="darelocal"

# --- Prerequisites -----------------------------------------------------------
command -v docker >/dev/null || { echo "ERROR: docker not found. Install Docker Desktop first."; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: docker daemon not running."; exit 1; }

# --- Submodule: quillmark-mcp ------------------------------------------------
git submodule update --init

# --- .env ---------------------------------------------------------------------
[[ -f .env ]] || cp .example.env .env
if ! grep -Eq '^DB_PASSWORD=.+' .env; then
  printf '\nDB_PASSWORD=darelocal\n' >> .env
  echo "Set DB_PASSWORD=darelocal in .env"
fi

# --- docker-compose.override.yml (local secrets, gitignored) ------------------
if [[ ! -f docker-compose.override.yml ]]; then
  SECRET_KEY="$(LC_ALL=C tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 50)"
  cat > docker-compose.override.yml <<EOF
# Local development overrides (loaded automatically by Docker Compose).
# Gitignored — safe place for local secrets.
x-local-env: &local-env
  DJANGO_DEBUG: "True"
  ENVIRONMENT: local
  PYTHONUNBUFFERED: "1"
  DJANGO_SECRET_KEY: ${SECRET_KEY}
  # Put your Anthropic API key here (sk-ant-...), then: docker compose up -d
  CLAUDE_API_KEY: ""

services:
  web:
    environment: *local-env
    volumes:
      - .:/app
  worker:
    environment: *local-env
    volumes:
      - .:/app
EOF
  echo "Created docker-compose.override.yml — edit it to set CLAUDE_API_KEY, then re-run 'docker compose up -d'."
fi

# --- Build + start (entrypoint runs migrations automatically) ------------------
docker compose up -d --build

# --- Wait for the API ----------------------------------------------------------
echo -n "Waiting for web to become healthy"
for _ in $(seq 1 60); do
  curl -fsS "http://localhost:${WEB_PORT}/api/health/" >/dev/null 2>&1 && break
  echo -n "."
  sleep 3
done
echo
curl -fsS "http://localhost:${WEB_PORT}/api/health/" >/dev/null \
  || { echo "ERROR: web never became healthy. Check: docker compose logs web"; exit 1; }

# --- Optional demo superuser ----------------------------------------------------
if $DEMO_USER; then
  if docker compose exec -T web python manage.py shell -c "
from django.contrib.auth import get_user_model
import sys
sys.exit(0 if get_user_model().objects.filter(email='${DEMO_EMAIL}').exists() else 1)
" >/dev/null 2>&1; then
    echo "Demo user ${DEMO_EMAIL} already exists — skipping."
  else
    docker compose exec -T \
      -e DJANGO_SUPERUSER_EMAIL="${DEMO_EMAIL}" \
      -e DJANGO_SUPERUSER_PASSWORD="${DEMO_PASSWORD}" \
      web python manage.py createsuperuser --noinput
    docker compose exec -T web python manage.py shell -c "
from decimal import Decimal
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from billing.services import WalletService
from feature_flags.models import FeatureFlag, UserFeatureOverride
u = get_user_model().objects.get(email='${DEMO_EMAIL}')
EmailAddress.objects.update_or_create(user=u, email=u.email, defaults={'verified': True, 'primary': True})
WalletService.add_topup(u, amount=Decimal('100.00'), message='dev-setup demo top-up')
flag = FeatureFlag.objects.filter(key='enable_artifacts').first()
if flag:
    UserFeatureOverride.objects.update_or_create(flag=flag, user=u, defaults={'enabled': True})
print('Demo user verified, topped up \$100, artifacts enabled.')
"
  fi
fi

# --- Done -----------------------------------------------------------------------
cat <<EOF

Setup complete.

  Backend API      http://localhost:${WEB_PORT}/api/docs/
  Health           http://localhost:${WEB_PORT}/api/health/
  Quillmark (dbg)  http://localhost:${QUILLMARK_DEBUG_PORT:-8090}/mcp

Frontend: cd ../dare-frontend && npm install && npm run dev  →  http://localhost:5173
EOF
if $DEMO_USER; then
  echo "Demo login: ${DEMO_EMAIL} / ${DEMO_PASSWORD}"
else
  echo "Create a demo login with: scripts/dev-setup.sh --demo-user"
fi
if ! grep -q 'CLAUDE_API_KEY: "sk-' docker-compose.override.yml 2>/dev/null; then
  echo "Reminder: set CLAUDE_API_KEY in docker-compose.override.yml and run 'docker compose up -d' for chat + document generation."
fi
