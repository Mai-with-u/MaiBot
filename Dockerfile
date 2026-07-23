# Runtime image
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Working directory
WORKDIR /MaiMBot

ENV MAIBOT_LEGACY_0X_UPGRADE_CONFIRMED=1
ENV PATH="/MaiMBot/.venv/bin:${PATH}"

# Copy dependency metadata
COPY pyproject.toml uv.lock ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies
RUN uv sync --no-dev --no-install-project

# Install system libraries required by Playwright Chromium. The browser binary
# itself is downloaded lazily into the configured data directory at runtime.
RUN python -m playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy project source
COPY . .

RUN git clone --depth 1 --branch main https://github.com/Mai-with-u/MaiBot-Napcat-Adapter.git plugin-templates/MaiBot-Napcat-Adapter
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT [ "./docker-entrypoint.sh" ]
