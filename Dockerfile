# syntax=docker/dockerfile:1

# renovate: datasource=docker depName=node versioning=docker
ARG NODE_VERSION=24
# renovate: datasource=docker depName=python versioning=docker
ARG PYTHON_VERSION=3.13
# renovate: datasource=docker depName=alpine
ARG ALPINE_VERSION=3.21

# ============================================================================
# Frontend builder — compiles the React SPA to web/dist
# ============================================================================
FROM node:${NODE_VERSION}-alpine${ALPINE_VERSION} AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
RUN npm run build

# ============================================================================
# Python dependency builder — apprise is the only runtime dependency
# ============================================================================
FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS py-builder
WORKDIR /build
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --no-warn-script-location -r requirements.txt

# ============================================================================
# Runtime
# ============================================================================
FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS runtime

ARG BUILD_DATE
ARG VCS_REF
ARG VERSION
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/sharkusmanch/poketokenweb" \
      org.opencontainers.image.url="https://github.com/sharkusmanch/poketokenweb" \
      org.opencontainers.image.title="poketokenweb" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="Self-hosted web companion and usage tracker for Claude Code and Codex tokens"

# tzdata is REQUIRED, not optional. Both providers bucket usage by LOCAL date,
# and the resulting local_day is cached in the scan DB. Without tzdata, TZ
# silently resolves to UTC and "today" rolls over at the wrong hour for most of
# the world.
RUN --mount=type=cache,target=/var/cache/apk apk add --no-cache tzdata

# UID/GID 1000 is the first-user UID on a typical Linux system, so a
# bind-mounted ~/.claude (mode 0700, owned by that user) is readable without
# any --user override. Consumers whose logs are owned by someone else pass
# PUID/PGID; see .env.example.
RUN addgroup -g 1000 appgroup && \
    adduser -u 1000 -G appgroup -s /bin/false -D appuser

# /data must exist and be owned before the volume is declared: Docker seeds a
# named volume's ownership from the image's directory at the mount point, and
# with no such directory the volume is created root:root — the app then cannot
# create its save/cache subdirectories and the very first `docker compose up`
# fails for every consumer.
# 0777 rather than chown 1000: the compose file and README document PUID/PGID
# for users whose logs are owned by someone else, and Docker seeds a named
# volume's ownership from this directory -- so a 1000-owned mountpoint made
# every documented PUID != 1000 crash with an unhandled PermissionError and a
# restart loop. This is a mount point, not a code path.
RUN mkdir -p /data && chmod 0777 /data
VOLUME /data

COPY --from=py-builder /install /usr/local
WORKDIR /app
COPY poketokenbar /app/poketokenbar
COPY poketokenweb /app/poketokenweb
COPY --from=web-builder /build/web/dist /app/web

# HOME is where the engine looks for .claude, .claude.json and .codex. Without
# it a plain `docker run` inherits HOME=/root, finds no logs, and reports zero
# tokens with no error -- the compose file set it, so nothing else caught this.
ENV HOME=/config \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POKETOKENWEB_DATA_DIR=/data \
    POKETOKENWEB_WEB_ROOT=/app/web \
    POKETOKENWEB_SPOOL_DIR=/tmp/poketokenbar/commands \
    PORT=8080

USER 1000:1000
EXPOSE 8080

# /healthz reports the DAEMON's heartbeat, not merely that the socket accepts,
# so a dead poll thread is visible here rather than serving a frozen page.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["/usr/local/bin/python3", "-c", \
       "import os,sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz',timeout=4).status==200 else 1)"]

ENTRYPOINT ["python3", "-m", "poketokenweb"]
