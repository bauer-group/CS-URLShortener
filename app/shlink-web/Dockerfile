# =============================================================================
# URLShortener — Shlink Web Client (BAUER GROUP Build)
# =============================================================================
#
# Thin wrapper around shlinkio/shlink-web-client — no customizations yet.
# Re-paketiert das Upstream-Image unter BAUER GROUP-Brand und ermöglicht
# künftige Anpassungen (Branding, Custom-Init, statische Assets) ohne
# Compose-File-Änderung in Production.
#
# Usage:
#   docker build -t urlshortener-web:latest .
#   docker-compose up -d
#
# =============================================================================

ARG SHLINK_WEB_VERSION=stable
FROM shlinkio/shlink-web-client:${SHLINK_WEB_VERSION}

# Re-declare ARG after FROM to use it in labels
ARG SHLINK_WEB_VERSION

# =============================================================================
# Custom Labels
# =============================================================================

# Metadata
LABEL vendor="BAUER GROUP"
LABEL maintainer="Karl Bauer <karl.bauer@bauer-group.com>"

# Opencontainers Metadata
LABEL org.opencontainers.image.title="URLShortener Web"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.authors="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/CS-URLShortener"
LABEL org.opencontainers.image.url="https://github.com/bauer-group/CS-URLShortener"
LABEL org.opencontainers.image.documentation="https://github.com/bauer-group/CS-URLShortener#readme"
LABEL org.opencontainers.image.description="Shlink Web Client (Admin UI) - BAUER GROUP Edition"
LABEL org.opencontainers.image.version="2.1.4"

# Extended Labels
LABEL org.opencontainers.image.base.name="shlinkio/shlink-web-client:${SHLINK_WEB_VERSION}"
LABEL com.bauer-group.shlink-web.version="${SHLINK_WEB_VERSION}"
LABEL com.bauer-group.features="passthrough"
