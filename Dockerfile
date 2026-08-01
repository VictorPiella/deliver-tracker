FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# La base de datos vive en /data, montado como volumen persistente desde
# Unraid (o Docker Desktop en Windows) para que sobreviva reinicios del container.
ENV DB_PATH=/data/packages.db
ENV USE_MOCK_GMAIL=true
ENV ENABLE_BACKGROUND_WORKER=true
ENV FLASK_SECRET_KEY=change-me-in-production

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--chdir", "/srv", "app.web:app"]
