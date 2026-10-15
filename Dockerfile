# Image layers are arranged by frequency of change from lowest to highest.
# Once Python or uv image changes all subsequent layers are rebuilt, therefore pin both.
FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base

# Install third-party runtime dependencies in a separate stage.
FROM base AS deps
COPY --from=uv /uv /bin/
WORKDIR /MaiMBot
COPY pyproject.toml uv.lock ./
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

# Install the remaining dependencies (dashboard, plugin SDK, maim-message)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project --link-mode=copy

# Copy project source
COPY . .

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT [ "./docker-entrypoint.sh" ]
