#!/bin/bash
set -e

# ============================================
# DARE Backend - Docker Entrypoint
# ============================================
# Usage:
#   web       - Run Django ASGI server (uvicorn + socket.io)
#   worker    - Run RQ background worker
#   migrate   - Run database migrations
#   shell     - Open Django shell
#   <command> - Run arbitrary command

# Wait for dependent services
wait_for_service() {
    local host="$1"
    local port="$2"
    local service="$3"
    local max_attempts="${4:-30}"
    local attempt=0

    echo "Waiting for $service ($host:$port)..."
    while ! python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('$host', $port)); s.close()" 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            echo "ERROR: $service not available after $max_attempts attempts"
            exit 1
        fi
        echo "  Attempt $attempt/$max_attempts - $service not ready, retrying..."
        sleep 2
    done
    echo "$service is ready."
}

# Wait for PostgreSQL
if [ -n "$DB_HOST" ] && [ -n "$DB_PORT" ]; then
    wait_for_service "$DB_HOST" "$DB_PORT" "PostgreSQL"
fi

# Wait for Redis
if [ -n "$REDIS_HOST" ] && [ -n "$REDIS_PORT" ]; then
    wait_for_service "$REDIS_HOST" "$REDIS_PORT" "Redis"
fi

case "$1" in
    web)
        echo "Collecting static files..."
        python manage.py collectstatic --noinput 2>/dev/null || true

        echo "Running migrations..."
        python manage.py migrate --noinput

        echo "Starting ASGI server (uvicorn)..."
        if [ "${DJANGO_DEBUG:-}" = "True" ]; then
            echo "Debug mode: hot reload enabled"
            exec uvicorn dare.asgi:application \
                --host 0.0.0.0 \
                --port 8000 \
                --reload \
                --log-level "${LOG_LEVEL:-info}"
        else
            exec uvicorn dare.asgi:application \
                --host 0.0.0.0 \
                --port 8000 \
                --workers "${UVICORN_WORKERS:-1}" \
                --log-level "${LOG_LEVEL:-info}"
        fi
        ;;
    worker)
        echo "Starting RQ worker..."
        exec python manage.py rqworker default simple_queue --verbosity 2
        ;;
    migrate)
        echo "Running migrations..."
        exec python manage.py migrate --noinput
        ;;
    shell)
        exec python manage.py shell
        ;;
    *)
        exec "$@"
        ;;
esac
