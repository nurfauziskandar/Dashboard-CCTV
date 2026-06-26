FROM python:3.11-slim

WORKDIR /app

# OpenCV headless system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

RUN mkdir -p instance logs

ENV FLASK_ENV=production

EXPOSE 5000

# -w 1: single worker keeps APScheduler running correctly
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5000", "--timeout", "120", "run:app"]
