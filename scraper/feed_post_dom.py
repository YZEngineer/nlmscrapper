import time


_SCROLL_JS = """() => {
  let best = document.scrollingElement;
  let bestHeight = best ? best.scrollHeight : 0;
  for (const el of document.querySelectorAll('div')) {
    const style = getComputedStyle(el);
    const canScroll = (style.overflowY === 'auto' || style.overflowY === 'scroll')
      && el.scrollHeight > el.clientHeight + 100;
    if (canScroll && el.scrollHeight > bestHeight) {
      best = el;
      bestHeight = el.scrollHeight;
    }
  }
  if (best) best.scrollTop = best.scrollHeight;
  window.scrollTo(0, document.body.scrollHeight);
  return best ? {height: best.scrollHeight, top: best.scrollTop} : {height: 0, top: 0};
}"""


def _content_signature(page):
    try:
        return page.evaluate("""() => {
          const articles = Array.from(document.querySelectorAll('[role="article"]'));
          return {
            count: articles.length,
            chars: articles.reduce((total, el) => total + (el.innerText || '').length, 0)
          };
        }""")
    except Exception:
        return {"count": 0, "chars": 0}


def _scroll_to_end(page, max_scrolls=1000, idle_seconds=5):
    last_signature = _content_signature(page)
    last_change = time.monotonic()
    for scroll_number in range(1, max_scrolls + 1):
        page.evaluate(_SCROLL_JS)
        page.wait_for_timeout(250)
        signature = _content_signature(page)
        if (
            signature["count"] > last_signature["count"]
            or signature["chars"] > last_signature["chars"]
        ):
            last_signature = signature
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= idle_seconds:
            return scroll_number
    return max_scrolls


def _extract_dom_blocks(page):
    seen = set()
    blocks = []
    articles = page.locator('[role="article"]')
    for index in range(articles.count()):
        try:
            article = articles.nth(index)
            if not article.is_visible():
                continue
            text = article.inner_text(timeout=1000).strip()
            normalized = " ".join(text.split())
            if len(normalized) <= 5 or normalized in seen:
                continue
            seen.add(normalized)
            blocks.append(text)
        except Exception:
            continue
    return blocks


def collect_feed_post_dom_data(page, post_url, max_scrolls=1000, idle_seconds=5):
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    scrolls = _scroll_to_end(page, max_scrolls=max_scrolls, idle_seconds=idle_seconds)
    blocks = _extract_dom_blocks(page)
    return {
        "post_url": post_url,
        "blocks": blocks,
        "dom_based": True,
        "scrolls": scrolls,
    }
