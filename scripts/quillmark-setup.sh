#!/usr/bin/env bash
# Start only Quillmark in Docker and configure a host-run DARE backend for it.
set -euo pipefail

cd "$(dirname "$0")/.."

QUILLMARK_PORT="${QUILLMARK_PORT:-8090}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

command -v docker >/dev/null || {
  echo "ERROR: Docker is required to run Quillmark."
  exit 1
}
[[ -x "${PYTHON_BIN}" ]] || {
  echo "ERROR: ${PYTHON_BIN} not found. Set up the backend virtual environment first."
  exit 1
}

git submodule update --init quillmark-mcp
QUILLMARK_BASE_URL="http://127.0.0.1:${QUILLMARK_PORT}/artifacts" \
  docker compose -f docker-compose.quillmark.yml up -d --build

echo -n "Waiting for Quillmark"
for _ in $(seq 1 40); do
  if curl -sS "http://127.0.0.1:${QUILLMARK_PORT}/mcp" >/dev/null 2>&1; then
    break
  fi
  echo -n "."
  sleep 2
done
echo

curl -sS "http://127.0.0.1:${QUILLMARK_PORT}/mcp" >/dev/null 2>&1 || {
  echo "ERROR: Quillmark did not become ready."
  echo "Check: docker compose -f docker-compose.quillmark.yml logs"
  exit 1
}

"${PYTHON_BIN}" manage.py configure_quillmark \
  --url "http://127.0.0.1:${QUILLMARK_PORT}/mcp"

echo "Quillmark is ready; continue running DARE with your normal Python commands."
