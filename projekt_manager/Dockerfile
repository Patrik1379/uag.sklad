FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Bratislava \
    DATA_DIR=/app/data

# tzdata = správny lokálny čas pre denný report a dátumy
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN sed -i "s/\r$//" docker-entrypoint.sh && chmod +x docker-entrypoint.sh && mkdir -p /app/data && chown -R app:app /app/data

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=4)" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--timeout", "120", "--access-logfile", "-", "wsgi:app"]
