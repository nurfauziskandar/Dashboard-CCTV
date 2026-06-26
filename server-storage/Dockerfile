FROM python:3.11-slim

WORKDIR /app

# OpenCV headless + ffmpeg binary (for RTSP recording via subprocess)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p recordings logs .certs

EXPOSE 8080 8443 10161/udp

CMD ["python", "run.py"]
