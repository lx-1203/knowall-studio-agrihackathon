# Build from repository root (the directory containing KnowAll_Studio and kaf_format).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 libgl1 ffmpeg tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/knowall/backend
COPY KnowAll_Studio/backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt 'websockets>=15,<17'
COPY kaf_format/pyproject.toml /opt/knowall/kaf_format/pyproject.toml
COPY kaf_format/kaf /opt/knowall/kaf_format/kaf
RUN pip install /opt/knowall/kaf_format
# Explicit source-only copies: no checkout .env, data, keys, tests or upload archives.
COPY KnowAll_Studio/backend/app ./app
COPY KnowAll_Studio/backend/third_party ./third_party
COPY KnowAll_Studio/backend/alembic ./alembic
COPY KnowAll_Studio/backend/alembic.ini ./alembic.ini
COPY KnowAll_Studio/backend/cloud_entry.py KnowAll_Studio/backend/cloud_worker.py ./
RUN useradd --create-home --uid 10001 knowall && mkdir -p /srv/knowall && chown knowall:knowall /srv/knowall
USER knowall
ENV KNOWALL_CLOUD_DATA_ROOT=/srv/knowall KNOWALL_IGNORE_ENV_FILE=true DEBUG=false LOCAL_AUTO_LOGIN=false
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "cloud_entry:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
