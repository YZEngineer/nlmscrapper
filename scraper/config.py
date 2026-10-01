import os
from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_BASE_DIR, ".env")

if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, "r", encoding="utf-8-sig") as _f:
        _lines = "\n".join(_f.read().splitlines())
    _tmp = _ENV_PATH + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as _f:
        _f.write(_lines)
    load_dotenv(_tmp)
    os.remove(_tmp)

GROUP_IDS = [v for k, v in os.environ.items() if k.startswith("GRUP_ID_") and v.strip()]
SESSION_FILE = "facebook_session.json"
DATA_DIR = "data"
POST_LIMIT = int(os.getenv("POST_LIMIT", "25"))
HEADLESS = os.getenv("HEADLESS", "0") == "1"
TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "60000"))
