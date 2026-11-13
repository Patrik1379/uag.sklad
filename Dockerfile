FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Bratislava

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh && mkdir -p /data/uploads

EXPOSE 8000
CMD ["./entrypoint.sh"]
