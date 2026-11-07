"""Firmware image storage and the signed, time-limited download links the Virtual Controller uses.

REST cannot upload a file to an AP: `/rest/os-upgrade` only takes URLs. So SIMS stores the image and serves it
at an unguessable URL that exists only for an applied upgrade request:

    /fw/<change id>/<token>/<file name>      token = HMAC(SIMS secret, change id + file name)

The link works only while that change is applied and within `SIMS_FIRMWARE_LINK_HOURS` of applying it, and only
for the images named in the change. Images are signed by Aruba and verified by the AP, so plain HTTP is acceptable.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import FirmwareImage, utcnow

# AP classes named in the Instant 8.10 REST API Guide for /rest/os-upgrade.
AP_CLASSES = ("Centaurus", "Lupus", "Gemini", "Hercules", "Vela", "Draco", "Ursa", "Aries", "Scorpio")
_NAME = re.compile(r"^ArubaInstant_([A-Za-z]+)_(\d+\.\d+\.\d+\.\d+)(?:_\d+)?(?:\.[A-Za-z0-9]{1,5})?$")
CHUNK = 1024 * 1024


class ImageError(ValueError):
    """Shown to the admin as-is."""


def parse_name(filename: str) -> tuple[str, str]:
    """('Gemini', '8.10.0.21') from 'ArubaInstant_Gemini_8.10.0.21_91450'. Refuses anything else: SIMS will not guess
    which AP class an unfamiliar file belongs to."""
    m = _NAME.match(filename or "")
    if not m or m.group(1) not in AP_CLASSES:
        raise ImageError("File name must be the original Aruba name, like ArubaInstant_Gemini_8.10.0.21_91450 "
                         f"(AP class one of: {', '.join(AP_CLASSES)}). Do not rename the file.")
    return m.group(1), m.group(2)


def image_dir() -> Path:
    d = Path(get_settings().data_dir) / "firmware"
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_image(db: Session, filename: str, stream: BinaryIO, username: str) -> FirmwareImage:
    ap_class, version = parse_name(filename)
    if db.scalar(select(FirmwareImage).where(FirmwareImage.filename == filename)):
        raise ImageError("An image with this file name is already stored. Delete it first to replace it.")
    limit = get_settings().max_firmware_mb * 1024 * 1024
    dest, tmp = image_dir() / filename, image_dir() / (filename + ".part")
    h, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise ImageError(f"File is larger than the {get_settings().max_firmware_mb} MB limit.")
                h.update(chunk)
                out.write(chunk)
        if size < 1024:
            raise ImageError("File is too small to be a firmware image.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    img = FirmwareImage(filename=filename, ap_class=ap_class, version=version, size_bytes=size,
                        sha256=h.hexdigest(), uploaded_by=username)
    db.add(img)
    db.flush()
    return img


def delete_image(db: Session, img: FirmwareImage) -> None:
    (image_dir() / img.filename).unlink(missing_ok=True)
    db.delete(img)


def image_path(filename: str) -> Path | None:
    """Resolved path of a stored image, or None. Never follows a name outside the image directory."""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    p = (image_dir() / filename).resolve()
    return p if p.parent == image_dir().resolve() and p.is_file() else None


# ---- download links ------------------------------------------------------------------------------
def token_for(change_id: int, filename: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), f"fw:{change_id}:{filename}".encode(), hashlib.sha256).hexdigest()


def link_valid(token: str, change_id: int, filename: str) -> bool:
    return hmac.compare_digest(token, token_for(change_id, filename))


def link_window_open(applied_at: datetime | None, now: datetime | None = None) -> bool:
    if applied_at is None:
        return False
    at = applied_at if applied_at.tzinfo else applied_at.replace(tzinfo=timezone.utc)
    return (now or utcnow()) <= at + timedelta(hours=get_settings().firmware_link_hours)


def public_base() -> str:
    """Validated base URL the VC will use to reach SIMS. Loopback is refused: the VC could never reach it."""
    base = get_settings().public_base_url.strip().rstrip("/")
    m = re.fullmatch(r"https?://([A-Za-z0-9.\-]+)(?::(\d{1,5}))?", base)
    if not m:
        raise ImageError("Set SIMS_PUBLIC_BASE_URL in .env to an address the APs can reach, e.g. http://10.1.2.3:8000, then restart.")
    if m.group(1).lower() in ("localhost", "127.0.0.1", "0.0.0.0") or m.group(1).startswith("127."):
        raise ImageError("SIMS_PUBLIC_BASE_URL points at this computer's loopback address; the Virtual Controller cannot reach "
                         "that. Use this computer's LAN IP, e.g. http://10.1.2.3:8000.")
    return base


def download_url(change_id: int, filename: str) -> str:
    return f"{public_base()}/fw/{change_id}/{token_for(change_id, filename)}/{filename}"
