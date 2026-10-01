import re
import time
from .group_feed import MORE_BUTTON_TEXTS, MORE_BUTTON_PATTERNS

_TR_MAP = {
    "ç": "c", "ğ": "g", "i": "i", "ı": "i", "ö": "o",
    "ş": "s", "ü": "u", "Ç": "c", "Ğ": "g", "İ": "i",
    "Ö": "o", "Ş": "s", "Ü": "u",
}


def _norm(text):
    text = text.lower()
    for a, b in _TR_MAP.items():
        text = text.replace(a, b)
    return " ".join(text.split())


_KEYWORDS = [_norm(t) for t in MORE_BUTTON_TEXTS]
_PATTERNS = [re.compile(p) for p in MORE_BUTTON_PATTERNS]


def _is_more_button(label):
    norm = _norm(label)
    if not norm or len(norm) > 60:
        return False
    for kw in _KEYWORDS:
        if kw in norm:
            return True
    for rx in _PATTERNS:
        if rx.search(norm):
            return True
    return False


_ALL_COMMENTS_AR = "كل التعليقات"
_SORT_TRIGGERS_AR = ("الأكثر ملاءمة", "الأحدث", "كل التعليقات")

_SCROLL_MARKER = "data-fb-scroll"

_SCROLL_JS_TEMPLATE = """(marker) => {
  const c = document.querySelector('[' + marker + ']');
  if (c) {
    c.scrollTop = c.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
    return;
  }
  let best = null, bestSh = 0;
  for (const el of document.querySelectorAll('div')) {
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
}"""


def _scroll_comments_area(page):
    try:
        page.evaluate(_SCROLL_JS_TEMPLATE, _SCROLL_MARKER)
    except Exception:
        pass


def _button_scope(page):
    try:
        has = page.evaluate("(marker) => !!document.querySelector('[' + marker + ']')", _SCROLL_MARKER)
    except Exception:
        has = False
    if has:
        return page.locator('[%s] [role="button"]' % _SCROLL_MARKER)
    return page.get_by_role("button")


def _click_more_buttons(page):
    empty_streak = 0
    last_articles = -1
    idle = 0

    # Stop as soon as the comment list stops growing instead of waiting
    # through the full safety cap on every post.
    for _ in range(24):
        clicked = False
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
                    page.wait_for_timeout(250)
                    clicked = True
            except Exception:
                pass

        _scroll_comments_area(page)
        page.wait_for_timeout(300)

        if clicked:
            empty_streak = 0
        else:
            empty_streak += 1

        try:
            arts = page.locator('[role="article"]').count()
        except Exception:
            arts = last_articles

        if arts != last_articles:
            last_articles = arts
            idle = 0
        else:
            idle += 1

        if empty_streak >= 2 and idle >= 1:
            break


def _ensure_all_comments(page):
    triggers = page.locator('[role="button"][aria-haspopup="menu"]')
    count = triggers.count()
    for i in range(count):
        try:
            btn = triggers.nth(i)
            if not btn.is_visible():
                continue
            label = " ".join((btn.inner_text(timeout=400) or "").split())
        except Exception:
            continue
        if not label or len(label) > 40:
            continue
        if _ALL_COMMENTS_AR in label:
            return
        if not any(t in label for t in _SORT_TRIGGERS_AR):
            continue
        try:
            btn.click(force=True, timeout=1000)
            page.wait_for_timeout(600)
        except Exception:
            continue
        items = page.locator('[role^="menuitem"]')
        n = items.count()
        for j in range(n):
            try:
                item = items.nth(j)
                if not item.is_visible():
                    continue
                ilabel = " ".join((item.inner_text(timeout=400) or "").split())
                if ilabel.replace("\ufeff", "").startswith(_ALL_COMMENTS_AR):
                    item.click(force=True, timeout=1000)
                    page.wait_for_timeout(1500)
                    return
            except Exception:
                pass
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass
    return


def _extract_blocks(page):
    seen = set()
    blocks = []
    articles = page.locator('[role="article"]')
    count = articles.count()
    for i in range(count):
        try:
            article = articles.nth(i)
            if not article.is_visible():
                continue
            text = article.inner_text(timeout=1000).strip()
            if len(text) <= 5:
                continue
            norm = " ".join(text.split())
            if norm in seen:
                continue
            seen.add(norm)
            blocks.append(text)
        except Exception:
            pass
    return blocks


def collect_post_data(page, post_url):
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    _ensure_all_comments(page)
    _click_more_buttons(page)

    blocks = _extract_blocks(page)
    return {
        "post_url": post_url,
        "blocks": blocks,
    }