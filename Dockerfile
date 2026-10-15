# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable --no-cache

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="l4d2-qq-bot" \
      org.opencontainers.image.description="L4D2 queries and image cards via official QQ WebSocket" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    L4D2_CARD_FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    L4D2_DISCOVERY_PATH=/app/data/qq-discovery.json
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid bot --create-home bot
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY config.example.json README.md LICENSE NOTICE.md ./
RUN mkdir /app/artifacts /app/data && chown bot:bot /app/artifacts /app/data
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/l4d2-qq-bot"]
CMD ["run", "--config", "/app/config.json"]
