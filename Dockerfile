FROM python:3.10-slim

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
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries
RUN playwright install chromium

# Copy application layers
COPY backend/ ./backend/
COPY ["Excel Timesheets", "./Excel Timesheets/"]

# Expose port (Render/Railway handles this via ENV vars, default to 8000)
EXPOSE 8000

# Run Uvicorn Fast API
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
