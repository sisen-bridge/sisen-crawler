# ── Base image ────────────────────────────────────────────────────────────────
# Use the official Python slim image. We install Chromium dependencies manually
# so the image stays as small as possible.
FROM python:3.12-slim

# ── System dependencies for Playwright / Chromium ─────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime libraries
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
    # Misc
    wget \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser for Playwright (stored in /root/.cache/ms-playwright)
RUN playwright install chromium

# ── Application code ───────────────────────────────────────────────────────────
COPY main.py webscrape.py ./

# ── Entrypoint ─────────────────────────────────────────────────────────────────
# Cloud Run Jobs runs the container to completion and exits.
CMD ["python", "main.py"]