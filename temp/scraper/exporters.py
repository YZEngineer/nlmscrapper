import json
import os
from datetime import datetime
from .config import DATA_DIR


def export_group(data, group_id, out_name=None):
    os.makedirs(DATA_DIR, exist_ok=True)
    if out_name:
        path = os.path.join(DATA_DIR, out_name)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(DATA_DIR, f"{group_id}_{stamp}.json")
    payload = {
        "group_id": group_id,
        "scraped_at": datetime.now().isoformat(),
        "posts": data,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
