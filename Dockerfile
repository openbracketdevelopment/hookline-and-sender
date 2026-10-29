FROM python:3.12.16-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

COPY pyproject.toml uv.lock README.md ./

ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        uv sync --frozen; \
    else \
        uv sync --frozen --no-dev; \
    fi

COPY backend ./backend
COPY scripts ./scripts
COPY tests ./tests

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
