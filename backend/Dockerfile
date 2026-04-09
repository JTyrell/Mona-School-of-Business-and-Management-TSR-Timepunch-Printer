FROM python:3.11-slim

# Install system dependencies for Playwright, CUPS, and clean up
RUN apt-get update && apt-get install -y \
    cups-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries and their system dependencies
# We only need chromium for the PDF generation pipeline
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy backend code
COPY . /app

# The internal port Uvicorn will listen on
ENV PORT=8000

# Start Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
