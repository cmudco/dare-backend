# ============================================
# DARE Backend — Multi-stage Docker Build
# ============================================

FROM python:3.13-slim AS builder


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app


COPY requirements/ requirements/

RUN pip install --no-cache-dir --prefix=/install -r requirements/prod.txt

# ============================================
# Stage 2: Runtime — the actual image that runs
# ============================================
FROM python:3.13-slim

# Install RUNTIME system libraries (shared .so files that Python packages link to):
#   libpq5              = PostgreSQL client lib — psycopg2 needs this to talk to Postgres
#   ffmpeg              = Video/audio processing — used by ffmpeg-python for frame extraction
#   libglib2.0-0        = GLib library — required by OpenCV (opencv-python) at runtime
#   libgl1              = OpenGL — required by OpenCV for image processing
#   libpango-1.0-0      = Text layout engine — required by WeasyPrint for PDF generation
#   libpangocairo-1.0-0 = Pango + Cairo bridge — WeasyPrint renders PDFs through Cairo
#   libharfbuzz0b       = Text shaping — WeasyPrint uses this for font rendering
#   libfontconfig1      = Font configuration — WeasyPrint needs this to find system fonts
#   libcairo2           = 2D graphics library — WeasyPrint's core rendering engine
#   curl                = Used in healthchecks and entrypoint service-wait logic
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    ffmpeg \
    libglib2.0-0 \
    libgl1 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    libcairo2 \
    curl \
    && rm -rf /var/lib/apt/lists/*


COPY --from=builder /install /usr/local

WORKDIR /app


COPY . .


RUN mkdir -p /app/static /app/media


RUN groupadd -r dare && useradd -r -g dare -d /app dare \
    && chown -R dare:dare /app

# Make the entrypoint script executable.
# This script handles: waiting for DB/Redis, running migrations, starting uvicorn.
RUN chmod +x /app/docker/entrypoint.sh

# Switch from root to the non-root "dare" user for all subsequent commands.
USER dare

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]


CMD ["web"]
