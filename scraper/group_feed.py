POST_LINK_KEYWORDS = ("/permalink/", "/posts/")

MORE_BUTTON_TEXTS = [
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
    r"عرض\s*[0-9٠-٩]+\s*ردود",
    r"عرض\s*[0-9٠-٩]+\s*رد",
    r"عرض\s*[0-9٠-٩]+\s*تعليق",
    r"view\s+[0-9٠-٩]+\s*(more\s+)?repl",
    r"[0-9٠-٩]+\s*(more\s+)?replies",
    r"[0-9٠-٩]+\s*yani(ti?|ti\s*goruntule)?",
    r"[0-9٠-٩]+\s*yorum(lari|un|u)?\s*(goruntule|gor)?",
]


def _normalize_facebook_post_url(raw):
    if not raw:
        return ""
    href = raw.strip()
    if not href or href.startswith("javascript:"):
        return ""
    if href.startswith("/"):
        href = "https://www.facebook.com" + href
    if "facebook.com" not in href:
        return ""
    try:
        parsed = __import__("urllib.parse").parse_qs(__import__("urllib.parse").urlsplit(href).query)
        if "__cft__" in parsed:
            del parsed["__cft__"]
    except Exception:
        parsed = None
    href = href.split("#", 1)[0]
    if "?" in href:
        href = href.split("?", 1)[0]
    if href.endswith("/"):
        href = href[:-1]
    if "/posts/" in href or "/permalink/" in href:
        return href
    return ""


def collect_post_links(page, group_id, limit=None, log=None, should_stop=None):
    links = []
    seen = set()
    last_height = 0
    stall_count = 0
    if should_stop and should_stop():
        return links
    try:
        page.goto(f"https://www.facebook.com/groups/{group_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
    except Exception:
        if should_stop and should_stop():
            return links
        raise

    step = 0
    while True:
        if should_stop and should_stop():
            break
        step += 1
        try:
            candidates = page.evaluate("""
                () => {
                    const out = [];
                    const anchors = document.querySelectorAll('a[href]');
                    for (const a of anchors) {
                        const href = ((a && a.getAttribute('href')) || '').trim();
                        if (!href || href.startsWith('javascript:')) continue;
                        const lower = href.toLowerCase();
                        const isPostLike = lower.includes('/posts/') || lower.includes('/permalink/');
                        const isGroupLike = lower.includes('/groups/') || lower.includes('facebook.com/groups/');
                        if (isPostLike && isGroupLike) {
                            out.push(href);
                        }
                    }
                    return out;
                }
            """)
        except Exception:
            if should_stop and should_stop():
                break
            raise

        for href in candidates:
            clean = _normalize_facebook_post_url(href)
            if not clean or clean in seen:
                continue
            if str(group_id) not in clean:
                continue
            seen.add(clean)
            links.append(clean)
            if log:
                log(f"[group-feed] Found post URL: {clean}")

        if limit is not None and len(links) >= limit:
            break

        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            if should_stop and should_stop():
                break
            page.wait_for_timeout(2200)
            if should_stop and should_stop():
                break
            new_height = page.evaluate("document.body.scrollHeight")
        except Exception:
            if should_stop and should_stop():
                break
            raise

        if new_height == last_height:
            stall_count += 1
            if stall_count >= 4:
                break
        else:
            stall_count = 0
        last_height = new_height

        if log:
            log(f"[group-feed] Scroll {step}: found {len(links)} URLs")

    return links[:limit] if limit is not None else links