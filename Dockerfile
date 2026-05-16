# ── Base image ────────────────────────────────────────────────────────────────
# Official Python slim image; Chromium runtime libs installed manually so the
# image stays small.
FROM python:3.12-slim

# ── System dependencies for Playwright / Chromium ─────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    fonts-liberation \
    wget \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Chromium for Playwright (shared, world-readable location) ─────────────────
# Storing the browser at a known absolute path avoids HOME-dependent lookups
# when the container runs as a non-root user.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN playwright install chromium \
 && chmod -R a+rX /opt/playwright-browsers

# ── Application code ──────────────────────────────────────────────────────────
COPY main.py webscrape.py db.py ./

# ── Non-root user ─────────────────────────────────────────────────────────────
# Cloud Run allows root, but running as an unprivileged user is best practice.
RUN useradd --uid 1001 --create-home --shell /bin/bash app \
 && chown -R app:app /app
USER app
ENV HOME=/home/app

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Cloud Run Jobs runs the container to completion and exits.
CMD ["python", "main.py"]
