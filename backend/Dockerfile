# syntax=docker/dockerfile:1.6
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    AGAN_SKILLS_DIR=/app/skills

WORKDIR /app

# Install uv for fast, reproducible dependency installation
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Copy application source and bake filesystem skill packages directly into the image
COPY app/ /app/app/
COPY skills/ /app/skills/

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
