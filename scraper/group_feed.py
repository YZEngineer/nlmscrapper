import time

from .config import FEED_MAX_SCROLLS, FEED_STALL_ROUNDS, SCROLL_WAIT


POST_LINK_KEYWORDS = ("/permalink/", "/posts/")
MORE_BUTTON_TEXTS = [
    # English
    "View more comments",
    "View previous comments",
    "View more replies",
    "See more comments",
    "See previous comments",
    "View more",
    "View replies",
    "View all replies",
    "View previous replies",
    "View earlier comments",
    # Turkce (gercek Facebook metinleri)
    "Yorumları görüntüle",
    "Yorumları göster",
    "Daha fazla yorum görüntüle",
    "Önceki yorumları görüntüle",
    "Tüm yorumları görüntüle",
    "Daha fazla yanıt görüntüle",
    "Önceki yanıtları görüntüle",
    "Tüm yanıtları görüntüle",
    "Yanıtları görüntüle",
    "Yanıtı görüntüle",
    "Daha fazla yorum gör",
    "Diger yorumlari gör",
    "Önceki yorumlari gör",
    "Daha fazla yanit",
    "Diger yanitlari gör",
    "Tümünü gör",
    "Daha fazlasini gör",
    # Arapca (asil eksik olanlar)
    "عرض المزيد من التعليقات",
    "إظهار المزيد من التعليقات",
    "عرض التعليقات السابقة",
    "إظهار التعليقات السابقة",
    "عرض التعليقات الأقدم",
    "المزيد من التعليقات",
    "عرض المزيد من الردود",
    "إظهار المزيد من الردود",
    "عرض الردود السابقة",
    "عرض المزيد",
    "عرض الردود",
    "عرض ردين",
    "عرض رد واحد",
]

MORE_BUTTON_PATTERNS = [
    # Sayili yanit butonlari: "عرض ٥ ردود", "View 5 replies", "5 yanıtı görüntüle"
    r"عرض\s*[0-9٠-٩]+\s*ردود",
    r"عرض\s*[0-9٠-٩]+\s*رد",
    r"عرض\s*[0-9٠-٩]+\s*تعليق",
    r"view\s+[0-9٠-٩]+\s*(more\s+)?repl",
    r"[0-9٠-٩]+\s*(more\s+)?replies",
    r"[0-9٠-٩]+\s*yani(ti?|ti\s*goruntule)?",
    r"[0-9٠-٩]+\s*yorum(lari|un|u)?\s*(goruntule|gor)?"
]


def collect_post_links(page, group_id, limit):
    page.goto(f"https://www.facebook.com/groups/{group_id}", wait_until="domcontentloaded")
    try:
        page.wait_for_selector('[role="article"]', timeout=12000)
    except Exception:
        pass
    time.sleep(6)

    links = set()
    last_height = 0
    last_link_count = -1
    stall_count = 0

    for _ in range(FEED_MAX_SCROLLS):
        anchors = page.locator('a[href*="/groups/"]')
        count = anchors.count()
        for i in range(count):
            try:
                href = anchors.nth(i).get_attribute("href") or ""
            except Exception:
                continue
            if group_id not in href:
                continue
            if any(k in href for k in POST_LINK_KEYWORDS):
                clean = href.split("?")[0]
                if clean.endswith("/"):
                    links.add(clean)

        if len(links) >= limit:
            break

        _scroll_feed(page)
        time.sleep(SCROLL_WAIT)
        try:
            new_height = page.evaluate("document.body.scrollHeight")
        except Exception:
            new_height = last_height

        if len(links) == last_link_count and new_height == last_height:
            stall_count += 1
            if stall_count >= FEED_STALL_ROUNDS:
                break
        else:
            stall_count = 0
        last_link_count = len(links)
        last_height = new_height

    return list(links)[:limit]


def _article_post_link(art, group_id):
    links = art.locator("a")
    count = links.count()
    for i in range(count):
        try:
            href = links.nth(i).get_attribute("href") or ""
        except Exception:
            continue
        if group_id not in href:
            continue
        if any(k in href for k in POST_LINK_KEYWORDS):
            return href.split("?")[0].rstrip("/") + "/"
    return None


_FEED_TEXT_SELECTORS = (
    '[data-ad-preview="message"]',
    '[data-ad-comet-preview="message"]',
)


def _post_feed_text(art):
    for sel in _FEED_TEXT_SELECTORS:
        try:
            loc = art.locator(sel)
            if loc.count() and loc.first.is_visible():
                txt = " ".join(loc.first.inner_text(timeout=600).split())
                if len(txt) > 1:
                    return txt
        except Exception:
            continue
    return ""


def summarize_links(links):
    info = {
        "count": len(links),
    }
    if links:
        info["first_post"] = links[0]["url"]
        info["last_post"] = links[-1]["url"]
    else:
        info["first_post"] = None
        info["last_post"] = None
    return info


def _scroll_feed(page):
    """Grup akisini guvenilir sekilde asagi kaydir: once son article'a in."""
    try:
        arts = page.locator('[role="article"]')
        if arts.count():
            arts.last.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass


def collect_post_links_dated(page, group_id, max_post=None, max_scrolls=None,
                             max_duration=None, on_link=None, on_scroll=None,
                             stop_event=None):
    if max_scrolls is None and max_post is None and max_duration is None:
        max_scrolls = FEED_MAX_SCROLLS
    deadline = None if not max_duration else time.monotonic() + max_duration

    page.goto(f"https://www.facebook.com/groups/{group_id}", wait_until="domcontentloaded")
    try:
        page.wait_for_selector('[role="article"]', timeout=12000)
    except Exception:
        pass
    time.sleep(6)

    collected = {}
    last_height = 0
    last_link_count = -1
    last_article_count = -1
    stall_count = 0
    scroll_count = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if deadline and time.monotonic() > deadline:
            break

        articles = page.locator('[role="article"]')
        try:
            article_count = articles.count()
        except Exception:
            article_count = last_article_count
        for i in range(article_count):
            try:
                art = articles.nth(i)
                url = _article_post_link(art, group_id)
                if not url or url in collected:
                    continue
                collected[url] = {
                    "url": url,
                    "post_text": _post_feed_text(art),
                }
                if on_link is not None:
                    on_link(collected[url], len(collected))
            except Exception:
                continue

        if max_post and len(collected) >= max_post:
            break

        if max_scrolls is not None and scroll_count >= max_scrolls:
            break

        _scroll_feed(page)
        time.sleep(SCROLL_WAIT)
        scroll_count += 1

        try:
            new_height = page.evaluate("document.body.scrollHeight")
        except Exception:
            new_height = last_height

        changed = (len(collected) != last_link_count
                   or article_count != last_article_count
                   or new_height != last_height)
        if changed:
            stall_count = 0
        else:
            stall_count += 1
            if stall_count >= FEED_STALL_ROUNDS:
                break

        last_link_count = len(collected)
        last_article_count = article_count
        last_height = new_height

        if on_scroll is not None:
            on_scroll(scroll_count, list(collected.values()))

    result = list(collected.values())
    if max_post:
        result = result[:max_post]
    return result, scroll_count