FROM python:3.12-slim

# Install system dependencies (FFmpeg, fonts, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PATH="/home/myuser/.local/bin:${PATH}"

# Setup storage and user permissions
RUN mkdir -p storage/audio storage/shorts storage/backgrounds \
    && useradd -m myuser \
    && chown -R myuser:myuser /app

USER myuser

EXPOSE 8000

# Run uvicorn directly behind a reverse proxy
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
