FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt requirements-v2.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-v2.txt \
    && useradd -m -u 10001 bot \
    && mkdir -p /srv/var /srv/data \
    && chown -R bot:bot /srv/var /srv/data
COPY app ./app
COPY data/demo_catalog.json ./data/demo_catalog.json
USER bot
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
