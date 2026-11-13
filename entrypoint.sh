#!/bin/sh
set -e
python -c "from app.db import init_db; init_db()"
exec gunicorn --workers "${WORKERS:-3}" --bind 0.0.0.0:8000 --access-logfile - wsgi:app
