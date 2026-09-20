import re
import time
from urllib.parse import urlparse
from .config import COMMENT_MAX_SECONDS, COMMENT_SETTLE_ROUNDS
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

_COMMENT_SORT_LABELS = (
    "\u0627\u0644\u0623\u0643\u062b\u0631 \u0645\u0644\u0627\u0621\u0645\u0629",
    "\u0627\u0644\u0623\u0643\u062b\u0631 \u0635\u0644\u0629",
    "الأكثر relevancy",
    "Most relevant",
    "En alakalı",
    "En ilgili",
)

_ALL_COMMENTS_LABELS = (
    "\u0643\u0644 \u0627\u0644\u062a\u0639\u0644\u064a\u0642\u0627\u062a",
    "\u0639\u0631\u0636 \u0643\u0644 \u0627\u0644\u062a\u0639\u0644\u064a\u0642\u0627\u062a",
    "\u0639\u0631\u0636 \u062c\u0645\u064a\u0639 \u0627\u0644\u062a\u0639\u0644\u064a\u0642\u0627\u062a",
    "All comments",
    "Tüm yorumlar",
    "Tüm yorumları gör",
)

_NEWEST_LABELS = (
    "\u0627\u0644\u0623\u062d\u062f\u062b",
    "Newest",
    "Latest",
    "En yeni",
)


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


def _clean_label(label):
    return " ".join((label or "").replace("\ufeff", " ").split()).casefold()


def _click_labeled_control(page, labels, selector):
    wanted = [_clean_label(label) for label in labels]
    controls = page.locator(selector)
    for i in range(controls.count()):
        try:
            control = controls.nth(i)
            if not control.is_visible():
                continue
            text = _clean_label(control.inner_text(timeout=500))
            aria = _clean_label(control.get_attribute("aria-label"))
            title = _clean_label(control.get_attribute("title"))
            if any(label in value for label in wanted for value in (text, aria, title)):
                control.click(force=True, timeout=1500)
                return True
        except Exception:
            continue
    for label in labels:
        try:
            control = page.get_by_text(label, exact=False).first
            if control.is_visible():
                control.click(force=True, timeout=1500)
                return True
        except Exception:
            continue
    return False


def _click_visible_text(page, labels):
    """Click the smallest visible DOM node containing one of the labels."""
    marker = "data-scraper-click-target"
    try:
        page.locator("[" + marker + "]").evaluate_all(
            "(els) => els.forEach((el) => el.removeAttribute('" + marker + "'))"
        )
    except Exception:
        pass
    found = page.evaluate(
        r"""(labels) => {
          const normalize = (value) => (value || "")
            .replace(/[\u061C\u200B-\u200F\uFEFF]/g, "")
            .replace(/\s+/g, " ").trim().toLocaleLowerCase();
          const wanted = labels.map(normalize);
          const visible = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== "hidden" && style.display !== "none" &&
              rect.width > 0 && rect.height > 0;
          };
          const textOf = (el) => normalize(el.innerText || el.textContent || "");
          const candidates = [...document.querySelectorAll("body *")]
            .filter((el) => visible(el) && wanted.some((label) => textOf(el).includes(label)))
            .sort((a, b) => textOf(a).length - textOf(b).length);
          for (const candidate of candidates) {
            const target = candidate.closest(
              '[role="menuitemradio"], [role="menuitem"], [role="option"], [role="button"]'
            ) || candidate;
            if (visible(target)) {
              target.setAttribute("data-scraper-click-target", "1");
              return true;
            }
          }
          return false;
        }""",
        list(labels),
    )
    if not found:
        return False
    target = page.locator("[" + marker + "]").first
    try:
        target.scroll_into_view_if_needed(timeout=1500)
        target.click(force=True, timeout=2500)
        return True
    finally:
        try:
            target.evaluate("(el) => el.removeAttribute('" + marker + "')")
        except Exception:
            pass


def _selected_comment_sort(page):
    """Return the current comment-sort button label in the post dialog."""
    buttons = page.locator('[role="button"][aria-haspopup="menu"]')
    for i in range(buttons.count()):
        try:
            button = buttons.nth(i)
            if not button.is_visible():
                continue
            label = _clean_label(button.inner_text(timeout=400))
            if label and (
                any(_clean_label(value) in label for value in _COMMENT_SORT_LABELS)
                or any(_clean_label(value) in label for value in _ALL_COMMENTS_LABELS)
                or any(_clean_label(value) in label for value in _NEWEST_LABELS)
            ):
                return label
        except Exception:
            continue
    return ""


def _is_all_comments_sort(label):
    normalized = _clean_label(label)
    return any(_clean_label(value) in normalized for value in _ALL_COMMENTS_LABELS)


def _select_all_comments(page):
    """Switch Facebook from the relevance-filtered comments to all comments."""
    sort_selector = '[role="button"], [role="combobox"], [role="option"]'
    try:
        dialogs = page.locator('[role="dialog"]')
        if dialogs.count():
            dialogs.last.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    menu_selector = '[role="menuitem"], [role="option"], [role="button"]'
    for _ in range(3):
        if _is_all_comments_sort(_selected_comment_sort(page)):
            return True

        sort_clicked = False
        for _ in range(16):
            sort_buttons = page.locator('[role="button"][aria-haspopup="menu"]')
            for i in range(sort_buttons.count()):
                try:
                    button = sort_buttons.nth(i)
                    if not button.is_visible():
                        continue
                    label = _clean_label(button.inner_text(timeout=400))
                    if label and (
                        any(_clean_label(value) in label for value in _COMMENT_SORT_LABELS)
                        or any(_clean_label(value) in label for value in _NEWEST_LABELS)
                    ):
                        button.click(force=True, timeout=2500)
                        sort_clicked = True
                        break
                except Exception:
                    continue
            if sort_clicked:
                break
            sort_clicked = (
                _click_visible_text(page, _COMMENT_SORT_LABELS + _NEWEST_LABELS)
                or _click_labeled_control(page, _COMMENT_SORT_LABELS + _NEWEST_LABELS, sort_selector)
            )
            if sort_clicked:
                break
            page.wait_for_timeout(500)
        if not sort_clicked:
            return False

        page.wait_for_timeout(700)
        selected = False
        for _ in range(16):
            selected = (
                _click_visible_text(page, _ALL_COMMENTS_LABELS)
                or _click_labeled_control(page, _ALL_COMMENTS_LABELS, menu_selector)
            )
            if selected:
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(1000)
        if _is_all_comments_sort(_selected_comment_sort(page)):
            return True

    return False


_SCROLL_MARKER = "data-fb-scroll"

_SCROLL_JS_TEMPLATE = """(marker) => {
  const scrollable = (el) => {
    const sh = el.scrollHeight, ch = el.clientHeight;
    const ov = getComputedStyle(el).overflowY;
    return sh > ch + 100 && (ov === 'auto' || ov === 'scroll');
  };
  const prev = document.querySelector('[' + marker + ']');
  if (prev) {
    prev.scrollTop = prev.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
    return;
  }
  let best = null, bestScore = -1;
  for (const el of document.querySelectorAll('div')) {
    if (!scrollable(el)) continue;
    let score = el.scrollHeight;
    if (el.querySelector('[role="article"]')) score += 100000;
    if (el.querySelector('[data-ad-comet-preview="message"], [data-ad-preview="message"]')) score += 50000;
    if (score > bestScore) { bestScore = score; best = el; }
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
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
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


_NA_MARKERS = (
    "bu içerik şu anda mevcut değil",
    "bu içerik kullanılamıyor",
    "bu içerik artık mevcut değil",
    "bu yazı silinmiş olabilir",
    "içerik şu anda mevcut değil",
    "this content isn't available",
    "this content is no longer available",
    "sorry, this content isn't available",
    "this post is unavailable",
    "غير متاح",
    "غير متوفر",
)


def _is_post_page_url(current_url):
    try:
        u = urlparse(current_url or "")
    except Exception:
        return False
    if not u.scheme or not u.hostname:
        return False
    host = u.hostname.lower()
    if "facebook.com" not in host:
        return False
    path = (u.path or "").rstrip("/")
    if not path or path == "/index.php":
        return False
    if path.startswith("/login") or path.startswith("/checkpoint"):
        return False
    return True


def _is_post_page(page):
    try:
        return _is_post_page_url(page.url)
    except Exception:
        return True


def _check_unavailable(page):
    if not _is_post_page(page):
        return True
    try:
        text = page.locator("body").inner_text(timeout=4000)
    except Exception:
        return False
    low = " ".join(text.lower().split())
    for m in _NA_MARKERS:
        if m in low:
            return True
    return False


def _click_more_buttons(page, max_seconds=None, stop_event=None):
    """Tum 'daha fazla yorum/yanit' butonlarini ac.

    Sabit sure siniri yerine adaptif durma kullanir: pekive (buton yok + article
    sayisi stabil) oldugunda tamamlanmis sayilir. Yalnizca max_seconds güvenlik
    tavani (varsayilan: COMMENT_MAX_SECONDS) veya stop_event kesebilir.
    Sonuc bilgisi dondurur: {"clicks", "capped", "stopped"}.
    """
    if max_seconds is None:
        max_seconds = COMMENT_MAX_SECONDS
    start = time.monotonic()
    clicks_total = 0
    idle = 0
    last_articles = -1
    max_iterations = 600
    sort_selected = _select_all_comments(page)

    for _ in range(max_iterations):
        if max_seconds and time.monotonic() - start > max_seconds:
            return {
                "clicks": clicks_total,
                "capped": True,
                "stopped": False,
                "sort_selected": sort_selected,
            }
        if stop_event is not None and stop_event.is_set():
            return {
                "clicks": clicks_total,
                "capped": False,
                "stopped": True,
                "sort_selected": sort_selected,
            }
        if not _is_post_page(page):
            return {
                "clicks": clicks_total,
                "capped": False,
                "stopped": False,
                "sort_selected": sort_selected,
            }

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
                    page.wait_for_timeout(500)
                    clicks_total += 1
                    clicked = True
            except Exception:
                pass

        _scroll_comments_area(page)
        page.wait_for_timeout(600)

        try:
            arts = page.locator('[role="article"]').count()
        except Exception:
            arts = last_articles

        if clicked or arts != last_articles:
            idle = 0
        else:
            idle += 1
        last_articles = arts

        if not clicked and idle >= COMMENT_SETTLE_ROUNDS:
            return {
                "clicks": clicks_total,
                "capped": False,
                "stopped": False,
                "sort_selected": sort_selected,
            }

    return {
        "clicks": clicks_total,
        "capped": True,
        "stopped": False,
        "sort_selected": sort_selected,
    }


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


_POST_TEXT_SELECTORS = (
    '[data-ad-comet-preview="message"]',
    '[data-ad-preview="message"]',
)


def _extract_post_text(page):
    candidates = []
    for sel in _POST_TEXT_SELECTORS:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                try:
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    txt = " ".join(el.inner_text(timeout=800).split())
                    if len(txt) > 1:
                        candidates.append(txt)
                except Exception:
                    continue
        except Exception:
            continue
    if candidates:
        return max(candidates, key=len)
    return ""


def _wait_for_comment_area(page, timeout=15000):
    """Post/yorum alani yuklenene kadar bekle; olmazsa yine de devam et."""
    try:
        page.wait_for_selector('[role="article"]', timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(1000)


def _post_paths(url):
    try:
        u = urlparse(url or "")
        return (u.scheme, (u.netloc or "").lower(), (u.path or "").rstrip("/"))
    except Exception:
        return ("", "", "")


def _is_same_post(page, post_url):
    return _post_paths(page.url) == _post_paths(post_url)


def _unavailable(post_url):
    return {
        "post_url": post_url,
        "post_text": "",
        "blocks": [],
        "error": "unavailable",
    }


def collect_post_data(page, post_url, max_seconds=None, stop_event=None):
    if stop_event is not None and stop_event.is_set():
        return _unavailable(post_url)
    attempt = 0
    while True:
        page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
        _wait_for_comment_area(page)
        if _is_same_post(page, post_url):
            break
        attempt += 1
        if attempt >= 2 or (stop_event is not None and stop_event.is_set()):
            break
        page.wait_for_timeout(2000)

    if not _is_same_post(page, post_url) or _check_unavailable(page):
        return _unavailable(post_url)

    expand = _click_more_buttons(page, max_seconds=max_seconds, stop_event=stop_event)

    if not _is_post_page(page):
        return _unavailable(post_url)

    blocks = _extract_blocks(page)
    return {
        "post_url": post_url,
        "post_text": _extract_post_text(page),
        "blocks": blocks,
        "expand": expand,
    }


def collect_post_data_retry(page, post_url, attempts=2, max_seconds=None, stop_event=None):
    last = None
    for i in range(attempts):
        try:
            return collect_post_data(page, post_url, max_seconds=max_seconds, stop_event=stop_event)
        except Exception as e:
            last = e
            if "closed" not in str(e):
                raise
            time.sleep(1)
    raise last