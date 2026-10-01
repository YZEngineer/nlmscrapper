import json
import os
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from scraper.session import open_browser
from scraper.post_comments import (
    _ensure_all_comments,
    _extract_blocks,
    _is_more_button,
    _button_scope,
)

SOURCE_FILE = os.path.join("data", "dx_posts.json")
OUT_PREFIX = os.path.join("data", "dx_posts_comments")

_SCROLL_MARKER = "data-fb-scroll"

_SCROLL_JS = """(marker) => {
  const before = window.scrollY;
  const bodyH = document.body.scrollHeight;
  const c = document.querySelector('[' + marker + ']');
  if (c) {
    c.scrollTop = c.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
    return {marker: true, bestSh: c.scrollHeight, scrollY: window.scrollY, sh: document.body.scrollHeight, before: before, bodyH: bodyH};
  }
  let best = null, bestSh = 0;
  const targets = [document.documentElement, document.body];
  for (const el of document.querySelectorAll('div, main, [role="feed"], [role="main"]')) {
    targets.push(el);
  }
  for (const el of targets) {
    if (!el) continue;
    const sh = el.scrollHeight, ch = el.clientHeight;
    const ov = getComputedStyle(el).overflowY;
    if (sh > ch + 100 && (ov === 'auto' || ov === 'scroll') && sh > bestSh) {
      bestSh = sh; best = el;
    }
  }
  if (best) {
    best.setAttribute(marker, '1');
    best.scrollTop = best.scrollHeight;
  }
  window.scrollTo(0, document.body.scrollHeight);
  window.scrollBy(0, 400);
  window.scrollTo(0, document.body.scrollHeight);
  return {marker: !!best, bestSh: bestSh, scrollY: window.scrollY, sh: document.body.scrollHeight, before: before, bodyH: bodyH};
}"""


def load_urls(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                raw.append(item)
            elif isinstance(item, dict) and item.get("url"):
                raw.append(item["url"])
    elif isinstance(data, dict):
        for key in ("links", "posts"):
            arr = data.get(key)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict) and item.get("url"):
                        raw.append(item["url"])
                if raw:
                    break
    return raw


def save_atomic(payload, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def do_scroll(page, last_scroll, source="main"):
    gap = time.time() - last_scroll
    info = {}
    try:
        info = page.evaluate(_SCROLL_JS, _SCROLL_MARKER) or {}
    except Exception:
        pass
    print(
        f"      scroll[{source}] elapsed={gap:.2f}s "
        f"marker={info.get('marker')} bestSh={info.get('bestSh')} "
        f"scrollY={info.get('scrollY', 0)} sh={info.get('sh', 0)}"
    )
    return info


def _loading_active(page):
    try:
        if page.locator('[role="progressbar"]').count() > 0:
            return True
        return page.locator('[aria-busy="true"]').count() > 0
    except Exception:
        return False


def collect_post(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    _ensure_all_comments(page)

    stall = 0
    last_articles = -1
    last_heights = None
    last_scroll = time.time()

    for turn in range(1000):
        clicked = False
        if time.time() - last_scroll >= 5:
            do_scroll(page, last_scroll, source="retry")
            last_scroll = time.time()
        buttons = _button_scope(page)
        count = buttons.count()
        for i in range(count):
            try:
                btn = buttons.nth(i)
                if not btn.is_visible():
                    continue
                label = btn.inner_text(timeout=400)
                if not label or len(label) > 60:
                    continue
                if _is_more_button(label):
                    btn.click(force=True, timeout=1000)
                    page.wait_for_timeout(500)
                    clicked = True
                    if time.time() - last_scroll >= 5:
                        do_scroll(page, last_scroll, source="button")
                        last_scroll = time.time()
            except Exception:
                pass

        info = do_scroll(page, last_scroll, source="main")
        last_scroll = time.time()

        page.wait_for_timeout(600)

        try:
            arts = page.locator('[role="article"]').count()
        except Exception:
            arts = last_articles

        heights = (info.get("scrollY", 0), info.get("sh", 0))

        progressed = clicked or arts != last_articles or (last_heights is not None and heights != last_heights)
        if not progressed and _loading_active(page):
            progressed = True
            print("      (Loading indicator detected; resetting stall counter)")
        if progressed:
            stall = 0
        else:
            stall += 1

        last_articles = arts
        last_heights = heights

        print(f"      pass {turn+1}: clicked={clicked} articles={arts} stall={stall} scrollY={heights[0]} sh={heights[1]}")

        if stall >= 3:
            do_scroll(page, last_scroll, source="final")
            last_scroll = time.time()
            page.wait_for_timeout(1200)
            try:
                arts2 = page.locator('[role="article"]').count()
            except Exception:
                arts2 = arts
            if arts2 != arts:
                last_articles = arts2
                stall = 0
                continue
            print(f"      >> post finished (at bottom, no new content) - pass {turn+1}")
            break

    blocks = _extract_blocks(page)
    return {"post_url": url, "blocks": blocks}


def main():
    if not os.path.exists(SOURCE_FILE):
        print(f"[ERROR] Source file not found: {SOURCE_FILE}")
        return

    urls = []
    for u in load_urls(SOURCE_FILE):
        u = u.strip().split("?")[0]
        if u and u not in urls:
            urls.append(u)

    if not urls:
        print("[ERROR] No URLs found in the source file.")
        return

    total = len(urls)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"{OUT_PREFIX}_{stamp}.json"
    print(f"[INFO] Processing {total} URLs")
    print(f"[INFO] Output: {out_path}")

    payload = {
        "source_file": SOURCE_FILE,
        "scraped_at": datetime.now().isoformat(),
        "status": "partial",
        "total": total,
        "processed": 0,
        "posts": [],
        "errors": [],
    }
    save_atomic(payload, out_path)

    p, browser, context, page = open_browser()
    try:
        for i, url in enumerate(urls, 1):
            print(f"  [{i}/{total}] {url}")
            tries = 0
            while True:
                tries += 1
                try:
                    data = collect_post(page, url)
                    payload["posts"].append(data)
                    print(f"    [OK] {len(data['blocks'])} blocks")
                    break
                except Exception as e:
                    closed = "closed" in str(e).lower()
                    if closed and tries < 4:
                        print(f"    [WARNING] Browser closed ({e}); restarting for retry {tries}...")
                        try:
                            browser.close()
                        except Exception:
                            pass
                        p.stop()
                        p, browser, context, page = open_browser()
                        continue
                    else:
                        payload["errors"].append({"url": url, "error": str(e)})
                        print(f"    [ERROR] {e}")
                        break

            payload["processed"] = i
            payload["scraped_at"] = datetime.now().isoformat()
            save_atomic(payload, out_path)
            print(f"    [saved] {out_path}")
    finally:
        try:
            context.storage_state(path="facebook_session.json")
        except Exception:
            pass
        browser.close()
        p.stop()

    payload["status"] = "done"
    payload["scraped_at"] = datetime.now().isoformat()
    save_atomic(payload, out_path)
    print(f"[OK] Saved: {out_path}")


if __name__ == "__main__":
    main()