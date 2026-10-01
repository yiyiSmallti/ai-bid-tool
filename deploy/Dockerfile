FROM python:3.12-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock alembic.ini ./
COPY server ./server
COPY cli ./cli
RUN pip install --no-cache-dir uv==0.11.7 && uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 bid && mkdir /app/data && chown bid:bid /app/data
ENV PATH="/app/.venv/bin:$PATH"
USER bid
