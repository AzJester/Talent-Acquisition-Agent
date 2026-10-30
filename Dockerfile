FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

COPY config ./config
RUN useradd --system --uid 10001 taa && mkdir -p /app/data && chown taa /app/data
USER taa

EXPOSE 8000
# serve refuses a non-local bind unless TAA_USERNAME/TAA_PASSWORD or TAA_TRUSTED_USER_HEADER is set
CMD ["taa", "serve", "--host", "0.0.0.0", "--port", "8000"]
