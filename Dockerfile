# Official Playwright Python image — Chromium and all system libraries
# are pre-installed.  No playwright install step needed.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies (Chromium already in base image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Railway injects PORT at runtime; default 8000 for local runs.
# The FastAPI application object is defined in app.py (not main.py).
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
