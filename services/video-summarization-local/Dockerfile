FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/video-summarization-local/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY services/rtvi/rt-vlm-openai/src/rt_vlm_openai /opt/rt-vlm-openai/rt_vlm_openai
COPY services/video-summarization-local/app.py /app/app.py
ENV PYTHONPATH=/opt/rt-vlm-openai
WORKDIR /app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "38111"]
