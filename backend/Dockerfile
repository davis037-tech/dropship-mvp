FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend backend
COPY migrations migrations
COPY frontend frontend
RUN useradd -r -u 10001 app && chown -R app /app
USER app
EXPOSE 8000
CMD ["gunicorn", "backend.wsgi:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]
