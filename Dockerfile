# Official Playwright Python image — Chromium and all system libraries
# are pre-installed.  No playwright install step needed.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies (Chromium already in base image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

EXPOSE 8000

# Python reads PORT from the environment — no shell expansion needed.
CMD ["python", "app.py"]
