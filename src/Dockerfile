# =============================================================================
# URLShortener — Custom Shlink Build
# Extended with RoadRunner supervisor configuration
# =============================================================================
#
# This Dockerfile extends the official Shlink image with a custom RoadRunner
# configuration that adds worker supervision (exec_ttl, watch_tick) to protect
# against hung workers.
#
# Usage:
#   docker build -t shlink-custom:latest .
#   docker-compose up -d
#
# =============================================================================

ARG SHLINK_VERSION=stable
FROM shlinkio/shlink:${SHLINK_VERSION}

# Re-declare ARG after FROM to use it in labels
ARG SHLINK_VERSION

# =============================================================================
# Custom Labels
# =============================================================================

# Metadata
LABEL vendor="BAUER GROUP"
LABEL maintainer="Karl Bauer <karl.bauer@bauer-group.com>"

# Opencontainers Metadata
LABEL org.opencontainers.image.title="URLShortener"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.authors="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/CS-URLShortener"
LABEL org.opencontainers.image.url="https://github.com/bauer-group/CS-URLShortener"
LABEL org.opencontainers.image.documentation="https://github.com/bauer-group/CS-URLShortener#readme"
LABEL org.opencontainers.image.description="Shlink URL Shortener - BAUER GROUP Edition"

# Extended Labels
LABEL org.opencontainers.image.base.name="shlinkio/shlink:${SHLINK_VERSION}"
LABEL com.bauer-group.shlink.version="${SHLINK_VERSION}"
LABEL com.bauer-group.features="roadrunner-supervisor"

# =============================================================================
# Copy Custom RoadRunner Configuration
# =============================================================================
# Adds worker supervisor with exec_ttl (30s kill timeout) and watch_tick (5s)
# to protect against hung PHP workers during title resolution or slow requests.

COPY config/roadrunner/.rr.yml /etc/shlink/config/roadrunner/.rr.yml
