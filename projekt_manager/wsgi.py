"""Vstupný bod pre produkčný server (gunicorn / waitress)."""
from app import create_app
from app.scheduler import start_scheduler

app = create_app()
start_scheduler(app)  # denný report – odoslanie je chránené proti duplicite aj pri viacerých workeroch
