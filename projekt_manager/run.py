"""Spustenie aplikácie.

Vývoj / malá firma:   python run.py
Produkcia (Linux):    gunicorn -w 2 -b 0.0.0.0:8000 wsgi:app
Produkcia (Windows):  waitress-serve --listen=0.0.0.0:8000 wsgi:app
"""
import os

from app import create_app
from app.scheduler import start_scheduler

app = create_app()

if __name__ == "__main__":
    start_scheduler(app)
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "5000")),
            debug=os.getenv("FLASK_DEBUG") == "1", use_reloader=False)
