FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY app.py /app/app.py
WORKDIR /app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
