from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("QUIZKID_DATA_DIR", str(BASE_DIR / "data")))
UPLOAD_DIR = Path(os.environ.get("QUIZKID_UPLOAD_DIR", str(DATA_DIR / "uploads")))
DB_PATH = Path(os.environ.get("QUIZKID_DB_PATH", str(DATA_DIR / "quizkid.sqlite3")))
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
COOKIE_SECURE = APP_ENV == "production"
SEED_DEMO_DATA = os.environ.get("QUIZKID_SEED_DEMO", "0") == "1"
MAX_UPLOAD_BYTES = int(os.environ.get("QUIZKID_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
