# syntax=docker/dockerfile:1
FROM debian:bookworm-slim AS rcodesign
ARG TARGETARCH
ARG RCODESIGN_VERSION=0.29.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) triple=x86_64-unknown-linux-musl ;; \
      arm64) triple=aarch64-unknown-linux-musl ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    asset="apple-codesign-${RCODESIGN_VERSION}-${triple}.tar.gz"; \
    url="https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign%2F${RCODESIGN_VERSION}/${asset}"; \
    curl -fsSL --retry 3 "$url" -o /tmp/tool.tar.gz; \
    curl -fsSL --retry 3 "${url}.sha256" -o /tmp/tool.sha256; \
    printf '%s  /tmp/tool.tar.gz\n' "$(cat /tmp/tool.sha256)" | sha256sum -c -; \
    mkdir /tmp/tool; tar -xzf /tmp/tool.tar.gz -C /tmp/tool --strip-components=1; \
    install -m 0755 /tmp/tool/rcodesign /usr/local/bin/rcodesign; \
    rcodesign --version

FROM --platform=$BUILDPLATFORM golang:1.26.10-alpine AS build
WORKDIR /src
COPY src/api/go.mod src/api/go.sum ./
RUN go mod download
COPY src/api/main.go ./
COPY src/api/internal/ ./internal/
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -mod=readonly -trimpath -o /out/apple-remote-sign .

FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 signer \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home signer \
    && mkdir -p /app /data && chown signer:signer /app /data
WORKDIR /app
COPY --from=build /out/apple-remote-sign ./apple-remote-sign
COPY --from=rcodesign /usr/local/bin/rcodesign /usr/local/bin/rcodesign
COPY --from=rcodesign /tmp/tool/COPYING /usr/local/share/licenses/rcodesign/COPYING
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/apple-remote-sign"]
CMD ["api", "--config", "/app/config.yaml"]
