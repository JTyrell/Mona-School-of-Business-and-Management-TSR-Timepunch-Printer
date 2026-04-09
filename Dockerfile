FROM python:3.11-slim

# Install OS dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    cups-client \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Requirements
COPY backend/requirements.txt .

# Install dependencies (no-cache to save container size on Fly)
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries
RUN playwright install chromium && playwright install-deps

# Copy application backend content
COPY backend/ ./backend/
COPY ["Excel Timesheets", "./Excel Timesheets/"]

# Expose port (Fly.io handles port 8000 via fly.toml typically)
EXPOSE 8000

# Run Uvicorn Fast API from within the backend directory structure
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
