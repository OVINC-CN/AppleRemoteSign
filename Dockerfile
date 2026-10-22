# syntax=docker/dockerfile:1
FROM python:3.14.9-slim AS rcodesign
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

FROM python:3.14.9-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 signer \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home signer \
    && mkdir -p /app /data \
    && chown -R signer:signer /app /data
WORKDIR /app
COPY --from=rcodesign /usr/local/bin/rcodesign /usr/local/bin/rcodesign
COPY --from=rcodesign /tmp/tool/COPYING /usr/local/share/licenses/rcodesign/COPYING
COPY pyproject.toml LICENSE ./
RUN python -c 'import pathlib,tomllib; p=tomllib.loads(pathlib.Path("pyproject.toml").read_text()); pathlib.Path("/tmp/requirements.txt").write_text("\n".join(p["project"]["dependencies"]))' \
    && pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY src ./src
RUN pip install --no-deps .
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "apple_remote_sign", "--host", "0.0.0.0", "--port", "8000"]
