FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends arp-scan smartmontools iputils-ping tzdata alsa-utils mpg123 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY led_notify ./led_notify
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data PORT=8787 PYTHONUNBUFFERED=1
EXPOSE 8787
CMD ["python", "-m", "led_notify.app"]
