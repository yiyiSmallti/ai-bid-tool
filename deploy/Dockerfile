FROM rust:1.90-slim-bookworm AS screenshot-renderer
WORKDIR /build
COPY stamp ./stamp
RUN cargo build --locked --release --manifest-path stamp/Cargo.toml

FROM python:3.12-slim-bookworm
WORKDIR /app
# PostgreSQL 16 client tools for `app.admin backup` and `restore --verify`; Debian's own are 15.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSo /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends tesseract-ocr-eng tesseract-ocr-chi-sim postgresql-client-16 \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock alembic.ini ./
COPY server ./server
COPY cli ./cli
COPY --from=screenshot-renderer --chown=0:0 /build/stamp/target/release/bid-screenshot-renderer /usr/local/libexec/bid-screenshot-renderer
RUN pip install --no-cache-dir uv==0.11.7 && uv sync --frozen --no-dev \
    && chown root:root /usr/local /usr/local/libexec \
    && chmod 0755 /usr/local /usr/local/libexec \
    && chmod 0555 /usr/local/libexec/bid-screenshot-renderer \
    && useradd --create-home --uid 10001 bid && mkdir /app/data /backups && chown bid:bid /app/data /backups
ENV PATH="/app/.venv/bin:$PATH"
ENV BID_PG_BIN="/usr/lib/postgresql/16/bin"
ENV BID_SCREENSHOT_RENDERER="/usr/local/libexec/bid-screenshot-renderer"
USER bid
