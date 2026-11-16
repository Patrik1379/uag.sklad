"""Jednoduchý plánovač denného reportu.

Beží vo vlákne na pozadí a raz za minútu skontroluje, či už je čas na denný report.
Odoslanie sa "zarezervuje" v databáze (tabuľka settings), takže aj keď beží viac
procesov servera (napr. gunicorn s viacerými workermi), e-maily odídu len raz denne.
"""
import logging
import threading
import time
from datetime import date, datetime

from .db import connect

log = logging.getLogger(__name__)
_started = False


def claim_today(db_path, key="last_daily_report"):
    """Atomicky označí dnešný deň ako odoslaný. Vráti True, ak sa to podarilo práve teraz."""
    today = date.today().isoformat()
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE settings.value <> excluded.value",
            (key, today),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _is_due(app, now):
    if app.config["DAILY_REPORT_SKIP_WEEKEND"] and now.weekday() >= 5:
        return False
    hh, mm = (int(x) for x in app.config["DAILY_REPORT_TIME"].split(":"))
    return (now.hour, now.minute) >= (hh, mm)


def _loop(app):
    from .mailer import send_daily_reports
    from .settings import refresh

    while True:
        try:
            refresh(app)  # čas reportu a pod. sa dá zmeniť v Nastaveniach
            if _is_due(app, datetime.now()) and claim_today(app.config["DATABASE"]):
                with app.app_context():
                    send_daily_reports()
            if _is_due(app, datetime.now()) and claim_today(app.config["DATABASE"], "last_install_reminder"):
                from .mailer import send_install_reminders
                with app.app_context():
                    send_install_reminders(app.config["DAILY_REPORT_SKIP_WEEKEND"])
        except Exception:  # noqa: BLE001
            log.exception("Chyba v plánovači denného reportu")
        time.sleep(60)


def start_scheduler(app):
    global _started
    if _started or not app.config["SCHEDULER_ENABLED"]:
        return
    _started = True
    threading.Thread(target=_loop, args=(app,), daemon=True, name="daily-report").start()
    log.info("Plánovač spustený – denný report o %s", app.config["DAILY_REPORT_TIME"])
