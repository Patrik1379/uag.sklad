"""Ilustračné obrázky projektov – uloženie (zmenšené) a zosúladenie so Skladom dielov."""
import io
import os
import uuid

from flask import current_app

ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _folder():
    return current_app.config["IMAGE_FOLDER"]


def save(stream):
    """Uloží obrázok zmenšený na max. 900 px + miniatúru 160 px (t_<názov>). Vracia názov súboru."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(stream))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    name = uuid.uuid4().hex + ".jpg"
    big = img.copy()
    big.thumbnail((900, 900))
    big.save(os.path.join(_folder(), name), "JPEG", quality=85)
    small = img.copy()
    small.thumbnail((160, 160))
    small.save(os.path.join(_folder(), "t_" + name), "JPEG", quality=82)
    return name


def delete(name):
    if not name:
        return
    for n in (name, "t_" + name):
        try:
            os.remove(os.path.join(_folder(), n))
        except OSError:
            pass


def path(name):
    return os.path.join(_folder(), name)


def set_project_image(project_id, stream, sync_name=None):
    """Uloží nový obrázok k projektu a zmaže starý. sync_name = názov rovnakého obrázka v sklade."""
    from .db import execute, query

    name = save(stream)
    old = query("SELECT image FROM projects WHERE id = ?", (project_id,), one=True)
    execute("UPDATE projects SET image = ?, image_sync = ? WHERE id = ?", (name, sync_name, project_id))
    if old:
        delete(old["image"])
    return name


def push_to_sklad(project_id):
    """Pošle lokálny obrázok projektu do skladu (ak je projekt prepojený)."""
    from . import sklad
    from .db import execute, query

    p = query("SELECT image, sklad_id FROM projects WHERE id = ?", (project_id,), one=True)
    if not p or not p["image"] or not p["sklad_id"] or not sklad.enabled():
        return False
    with open(path(p["image"]), "rb") as fh:
        ok, data = sklad.upload_image(project_id, fh.read())
    if ok and data.get("image"):
        execute("UPDATE projects SET image_sync = ? WHERE id = ?", (data["image"], project_id))
        sklad.forget()
        return True
    return False


def pull_from_sklad(project_id, sklad_image):
    """Stiahne obrázok projektu zo skladu (zmenil sa tam)."""
    from . import sklad

    ok, data = sklad.download_image(project_id)
    if ok and data:
        set_project_image(project_id, io.BytesIO(data), sync_name=sklad_image)
        return True
    return False


def sync_all(projects, smap):
    """Zosúladí obrázky so skladom podľa stavu z dashboardu (beží na pozadí).

    - projekt má obrázok, ktorý ešte nebol odoslaný (image_sync prázdne) → pošle sa do skladu,
    - v sklade je iný obrázok, než aký máme → stiahne sa zo skladu (zmenil sa tam).
    """
    for pid, image, image_sync, sklad_image in projects:
        try:
            if image and not image_sync:
                push_to_sklad(pid)
            elif sklad_image and sklad_image != image_sync:
                pull_from_sklad(pid, sklad_image)
        except Exception as exc:  # obrázky nesmú pokaziť dashboard
            current_app.logger.warning("Obrázok projektu %s sa nepodarilo zosúladiť: %s", pid, exc)
