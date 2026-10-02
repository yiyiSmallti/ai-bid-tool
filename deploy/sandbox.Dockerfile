# Build requires the operator-verified immutable digest of the Playwright Python
# v1.55.0 noble image for the accepted CPU architecture. There is no floating default.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN python -m pip install --no-cache-dir playwright==1.55.0 PyMuPDF==1.26.4 \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp sandbox
WORKDIR /opt/bid
COPY server/app/providers/sandbox_runtime.py /opt/bid/app/providers/sandbox_runtime.py
COPY server/app/sandbox/__init__.py /opt/bid/app/sandbox/__init__.py
COPY server/app/sandbox/runner.py /opt/bid/app/sandbox/runner.py
ENV PYTHONPATH=/opt/bid PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 10001:10001
# No ENTRYPOINT wrapper, runtime downloader, host mount or exposed debug port.
CMD ["python", "-m", "app.sandbox.runner", "render"]
