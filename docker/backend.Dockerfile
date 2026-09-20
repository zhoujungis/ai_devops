# Development / worker image for the AI DevOps Copilot backend.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies from the single source of truth. Override with
# --build-arg PIP_EXTRAS=. for a production image without the dev tooling.
ARG PIP_EXTRAS=.[dev]

COPY backend/ /app/

RUN pip install -e "${PIP_EXTRAS}"

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
