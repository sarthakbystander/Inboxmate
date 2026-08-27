# ---- Python image ----
FROM python:3.13-slim

# Non-root user for the runtime.
RUN addgroup --system --gid 1001 inboxmate \
    && adduser --system --uid 1001 --ingroup inboxmate inboxmate

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app
COPY pytest.ini ./

# Data + static live under /app; keep them writable by the app user.
RUN mkdir -p /app/data && chown -R inboxmate:inboxmate /app

USER inboxmate

EXPOSE 8080

# FastAPI + uvicorn (single worker to keep memory low and SQLite simple).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers"]