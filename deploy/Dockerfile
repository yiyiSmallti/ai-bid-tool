FROM rust:1.90-slim-bookworm AS screenshot-renderer
WORKDIR /build
COPY stamp ./stamp
RUN cargo build --locked --release --manifest-path stamp/Cargo.toml

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock alembic.ini ./
COPY server ./server
COPY cli ./cli
COPY --from=screenshot-renderer --chown=0:0 /build/stamp/target/release/bid-screenshot-renderer /usr/local/libexec/bid-screenshot-renderer
RUN pip install --no-cache-dir uv==0.11.7 && uv sync --frozen --no-dev \
    && chown root:root /usr/local /usr/local/libexec \
    && chmod 0755 /usr/local /usr/local/libexec \
    && chmod 0555 /usr/local/libexec/bid-screenshot-renderer \
    && useradd --create-home --uid 10001 bid && mkdir /app/data && chown bid:bid /app/data
ENV PATH="/app/.venv/bin:$PATH"
ENV BID_SCREENSHOT_RENDERER="/usr/local/libexec/bid-screenshot-renderer"
USER bid
