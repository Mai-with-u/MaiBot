# Image layers are arranged by frequency of change from lowest to highest.
# Once Python or uv image changes all subsequent layers are rebuilt, therefore pin both.
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base

# Install third-party runtime dependencies in a separate stage.
FROM base AS deps
COPY --from=uv /uv /bin/
WORKDIR /MaiMBot
COPY pyproject.toml ./
# Place uv cache in the cache mount.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project --link-mode=copy \
        --no-install-package maibot-dashboard \
        --no-install-package maibot-plugin-sdk \
        --no-install-package maim-message
# Store the Playwright package separately so it can be mounted when the runtime image installs.
RUN mkdir /playwright && cp -a .venv/lib/python3*/site-packages/playwright /playwright/

# Runtime image
FROM base
COPY --from=uv /uv /uvx /bin/

# Working directory
WORKDIR /MaiMBot

ENV MAIBOT_LEGACY_0X_UPGRADE_CONFIRMED=1
ENV PATH="/MaiMBot/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install system libraries required by Playwright Chromium. The browser binary
# itself is downloaded lazily into the configured data directory at runtime.
# This layer is rebuilt only when the Playwright version changes.
RUN --mount=type=bind,from=deps,source=/playwright,target=/tmp/playwright \
    PYTHONPATH=/tmp/playwright python -m playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /MaiMBot/.venv /MaiMBot/.venv

# Reuse the lock generated in the dependency stage for this build.
COPY --from=deps /MaiMBot/uv.lock ./uv.lock
# Install the remaining dependencies (dashboard, plugin SDK, maim-message)
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project --link-mode=copy

# Copy project source
COPY . .

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT [ "./docker-entrypoint.sh" ]
