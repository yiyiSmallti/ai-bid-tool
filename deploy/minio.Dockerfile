# Local development only: the upstream community repository is archived.
# Pin official source instead of relying on retired public binary images.
FROM golang:1.24.8-bookworm AS build
ENV CGO_ENABLED=0
RUN GOBIN=/out go install -p 2 github.com/minio/minio@v0.0.0-20260212201848-7aac2a2c5b7c

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /go/pkg/mod/github.com/minio/minio@v0.0.0-20260212201848-7aac2a2c5b7c/LICENSE /usr/share/doc/minio/LICENSE
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
