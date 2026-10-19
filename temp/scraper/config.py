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
SESSION_FILE = os.path.join(_BASE_DIR, "facebook_session.json")
DATA_DIR = os.path.join(_BASE_DIR, "data")
POST_LIMIT = int(os.getenv("PW_POST_LIMIT", os.getenv("POST_LIMIT", "25")))
HEADLESS = os.getenv("PW_HEADLESS", os.getenv("HEADLESS", "0")) == "1"
TIMEOUT = int(os.getenv("PW_PAGE_TIMEOUT", os.getenv("PAGE_TIMEOUT", "60000")))

# Yorum acma (comment expansion) ayarlari
# Bir post'taki tum yorum/yanit butonlarini acmak icin güvenlik tavani (saniye).
# 0 = sinirsiz (onermiyoruz). Varsayilan 300s
COMMENT_MAX_SECONDS = float(os.getenv("PW_COMMENT_MAX_SECONDS",
                                       os.getenv("COMMENT_MAX_SECONDS", "300")))
# Kac tur boyunca (buton yok + article sayisi degismedi) ise "tam acildi" sayilir.
COMMENT_SETTLE_ROUNDS = int(os.getenv("PW_COMMENT_SETTLE_ROUNDS", "5"))
# Scroll arasi bekleme (saniye) — grup akisi ve yorumlar icin
SCROLL_WAIT = float(os.getenv("PW_SCROLL_WAIT", "3"))
# Grup akisinda güvenlik scroll tavanı (tum limitler kapaliysa devreye girer)
FEED_MAX_SCROLLS = int(os.getenv("PW_FEED_MAX_SCROLLS", "200"))
# Grup akisinda kac tur durulursa "feed bitti" sayilir
FEED_STALL_ROUNDS = int(os.getenv("PW_FEED_STALL_ROUNDS", "5"))
