"""Konfigurácia aplikácie – hodnoty sa načítajú zo súboru .env alebo z premenných prostredia."""
import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _bool(name, default="false"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "ano", "áno", "on")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "ZMEN-MA-v-.env")

    DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
    DATABASE = os.getenv("DATABASE", os.path.join(DATA_DIR, "projekty.db"))
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join(DATA_DIR, "dokumenty"))
    IMAGE_FOLDER = os.getenv("IMAGE_FOLDER", os.path.join(DATA_DIR, "obrazky"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024

    # Verejná adresa aplikácie – používa sa v odkazoch v e-mailoch
    APP_URL = os.getenv("APP_URL", "http://localhost:5000").rstrip("/")
    APP_NAME = os.getenv("APP_NAME", "Prehľad projektov")

    # Prepojenie so Skladom dielov (dá sa nastaviť aj v aplikácii → Nastavenia)
    SKLAD_URL = os.getenv("SKLAD_URL", "").rstrip("/")
    # úvodná stránka so všetkými firemnými aplikáciami (odkaz v hornej lište)
    PORTAL_URL = os.getenv("PORTAL_URL", "").strip()
    SKLAD_API_KEY = os.getenv("SKLAD_API_KEY", "")

    # SMTP
    MAIL_ENABLED = _bool("MAIL_ENABLED")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", "true")   # STARTTLS (port 587)
    MAIL_USE_SSL = _bool("MAIL_USE_SSL", "false")  # SMTPS (port 465)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_SENDER = os.getenv("MAIL_SENDER", os.getenv("MAIL_USERNAME", "noreply@example.com"))

    # Denný report
    SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", "true")
    DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "07:30")  # HH:MM, čas servera
    DAILY_REPORT_SKIP_WEEKEND = _bool("DAILY_REPORT_SKIP_WEEKEND", "true")
    DUE_SOON_DAYS = int(os.getenv("DUE_SOON_DAYS", "7"))
    # o koľko percentuálnych bodov môže plnenie zaostávať za uplynutým časom, kým je projekt „Ohrozený“
    RISK_MARGIN = int(os.getenv("RISK_MARGIN", "20"))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", "false")  # true, ak beží cez HTTPS
