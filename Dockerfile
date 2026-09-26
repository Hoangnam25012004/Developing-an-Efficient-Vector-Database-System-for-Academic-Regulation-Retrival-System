# syntax=docker/dockerfile:1
# Production image: API + static UI + indexing worker, CPU only.
# Models are downloaded at first start (scripts/fetch_models.py) into /models.
FROM python:3.11-slim-bookworm

ENV PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

# OCR fallback for scanned PDFs (Vietnamese + English); a Unicode font for the tests.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-vie fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock ./
# The pip cache lives in a BuildKit cache mount, not in the image, so a
# retried build does not download everything again.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 --timeout 120 --index-url https://download.pytorch.org/whl/cpu torch==2.11.0
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 --timeout 120 -r requirements.lock

RUN useradd --create-home --uid 10001 arrs \
 && mkdir -p /models \
 && chown arrs:arrs /models /app
COPY --chown=arrs:arrs . .
RUN chmod +x docker/entrypoint.sh
USER arrs

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=900s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["docker/entrypoint.sh"]
