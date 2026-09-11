# Digest-pinned so a rebuild months from now produces the same base, not
# whatever :3.13-slim happens to point at that day.
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt itself changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hello_otel ./hello_otel

# Nothing here needs root, so don't hand it to anyone who gets in.
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

CMD ["uvicorn", "hello_otel.main:app", "--host", "0.0.0.0", "--port", "8000"]
