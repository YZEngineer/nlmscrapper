import re
import time
from urllib.parse import urlsplit


def _validate_post_url(post_url):
    value = str(post_url or "").strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host not in ("facebook.com", "www.facebook.com", "m.facebook.com"):
        raise ValueError("Only Facebook post URLs are accepted.")
    if "/posts/" not in path and "/permalink/" not in path:
        raise ValueError("The URL must identify a Facebook post, not a group or home page.")
    return value


def _post_identity(post_url):
    path = urlsplit(post_url).path.rstrip("/")
    match = re.search(r"/(?:posts|permalink)/([^/]+)$", path, re.IGNORECASE)
    if not match:
        raise ValueError("The Facebook URL does not contain a post identifier.")
    return match.group(1)


_SCROLL_JS = """() => {
  const candidates = Array.from(document.querySelectorAll('div, section, main'))
    .filter((el) => {
      const style = getComputedStyle(el);
      return (style.overflowY === 'auto' || style.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 100
        && el.clientHeight > 120;
    });
  candidates.sort((a, b) => b.scrollHeight - a.scrollHeight);
  const target = candidates[0] || null;
  if (target) {
    target.scrollTop = target.scrollHeight;
    return {height: target.scrollHeight, top: target.scrollTop, container: true};
  }
  return {height: 0, top: 0, container: false};
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


def _click_reply_buttons(page, max_clicks=25):
    try:
        return page.evaluate(r"""() => {
          const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, span, div'));
          const matches = [];
          let clicked = 0;
          for (const el of candidates) {
            if (!el || !el.isConnected || typeof el.click !== 'function') continue;
            const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
            if (!text) continue;
            const compact = text.replace(/[\s\u200C\u200D]+/g, '');
            if (compact === 'رد') continue;
            if (/الأصدقاء|أصدقاء/i.test(text)) continue;
            const replyExpand = /(عرض\s*(?:رد|ردود|الرد|\d+\s*رد|\d+\s*ردود)|عرض\s*\d+\s*ردود|عرض\s*\d+\s*رد)/iu;
            const replyDirect = /(ردود|الرد|رد\s*[\d\u0660-\u0669])/iu;
            const ok = replyExpand.test(text) || replyDirect.test(text);
            if (!ok) continue;
            const rect = el.getBoundingClientRect();
            if (!rect || rect.width < 12 || rect.height < 12) continue;
            if (el.dataset.commentReplyClicked === '1') continue;
            matches.push(text.slice(0, 160));
            try {
              el.dataset.commentReplyClicked = '1';
              el.click();
              clicked += 1;
              if (clicked >= max_clicks) break;
            } catch (e) {
              continue;
            }
          }
          return { clicked, matches: matches.slice(0, 25), total: matches.length };
        }""")
    except Exception:
        return {"clicked": 0, "matches": [], "total": 0}


def _ensure_all_comments_ar(page):
    triggers = page.locator('[role="button"][aria-haspopup="menu"]')
    for i in range(triggers.count()):
        try:
            btn = triggers.nth(i)
            if not btn.is_visible():
                continue
            label = " ".join((btn.inner_text(timeout=400) or "").split())
            if not label or len(label) > 60:
                continue
            if not any(token in label for token in ("الأكثر ملاءمة", "الأحدث", "كل التعليقات")):
                continue
            btn.click(force=True, timeout=1000)
            page.wait_for_timeout(600)
            break
        except Exception:
            continue

    items = page.locator('[role^="menuitem"]')
    for i in range(items.count()):
        try:
            item = items.nth(i)
            if not item.is_visible():
                continue
            label = " ".join((item.inner_text(timeout=400) or "").split())
            if not label:
                continue
            if label.replace("\ufeff", "").startswith("كل التعليقات"):
                item.click(force=True, timeout=1000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass
    return False


def _scroll_to_end(page, max_scrolls=1000, idle_seconds=5):
    last_signature = _content_signature(page)
    last_change = time.monotonic()
    reply_clicks = 0
    all_matches = []
    for scroll_number in range(1, max_scrolls + 1):
        page.evaluate(_SCROLL_JS)
        page.wait_for_timeout(250)
        metrics = _click_reply_buttons(page)
        reply_clicks += metrics.get("clicked", 0)
        if metrics.get("matches"):
            all_matches.extend(metrics["matches"])
        signature = _content_signature(page)
        if (
            signature["count"] > last_signature["count"]
            or signature["chars"] > last_signature["chars"]
        ):
            last_signature = signature
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= idle_seconds:
            return scroll_number, reply_clicks, all_matches
    return max_scrolls, reply_clicks, all_matches


_EXTRACT_TARGET_DOM_JS = r"""(postId) => {
  const isVisible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && rect.width > 0 && rect.height > 0;
  };
  const identifiesPost = (href) => {
    try {
      const url = new URL(href, location.href);
      if (!/(^|\.)facebook\.com$/i.test(url.hostname)) return false;
      const pathMatch = url.pathname.match(/\/(?:posts|permalink)\/([^/?]+)/i);
      return (pathMatch && pathMatch[1] === postId)
        || url.searchParams.get('story_fbid') === postId
        || url.searchParams.get('fbid') === postId;
    } catch (_) {
      return false;
    }
  };

  const matchingLinks = Array.from(document.querySelectorAll('a[href]'))
    .filter((link) => identifiesPost(link.href) && isVisible(link));
  const matchingArticle = matchingLinks
    .map((link) => link.closest('[role="article"]'))
    .find((article) => article && isVisible(article));

  const articleDialogs = Array.from(document.querySelectorAll('[role="dialog"]'))
    .filter((dialog) => isVisible(dialog) && dialog.querySelector('[role="article"]'));
  const matchingDialogs = articleDialogs
    .filter((dialog) => matchingLinks.some((link) => dialog.contains(link)));
  matchingDialogs.sort((a, b) => a.querySelectorAll('[role="article"]').length
    - b.querySelectorAll('[role="article"]').length);
  let scope = matchingDialogs[0] || null;

  if (!scope && articleDialogs.length) {
    const topLevelDialogs = articleDialogs.filter((dialog) =>
      !articleDialogs.some((other) => other !== dialog && other.contains(dialog)));
    if (topLevelDialogs.length !== 1) {
      return {
        error: 'A post popup was detected, but its target dialog is ambiguous; refusing to read background posts.'
      };
    }
    scope = topLevelDialogs[0];
  }

  if (!scope && !matchingArticle) {
    return {error: 'The requested post could not be identified in the visible page DOM.'};
  }

  const articles = scope
    ? Array.from(scope.querySelectorAll('[role="article"]'))
    : [matchingArticle, ...matchingArticle.querySelectorAll('[role="article"]')];
  const blocks = [];
  const seen = new Set();
  for (const article of articles) {
    if (!isVisible(article)) continue;
    const text = (article.innerText || '').trim();
    const normalized = text.replace(/\s+/g, ' ');
    if (normalized.length <= 5 || seen.has(normalized)) continue;
    seen.add(normalized);
    blocks.push(text);
  }
  if (!blocks.length) {
    return {error: 'The requested post was identified, but its article content was empty.'};
  }
  return {blocks};
}"""


def _extract_dom_blocks(page, post_id):
    result = page.evaluate(_EXTRACT_TARGET_DOM_JS, post_id)
    if not isinstance(result, dict):
        raise ValueError("Facebook returned an invalid result while reading the target post DOM.")
    if result.get("error"):
        raise ValueError(result["error"])
    blocks = result.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("No article blocks were found inside the requested post.")
    return blocks


def collect_post_comments_dom_data(page, post_url, max_scrolls=1000, idle_seconds=5):
    post_url = _validate_post_url(post_url)
    requested_post_id = _post_identity(post_url)
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    current_url = _validate_post_url(page.url)
    if _post_identity(current_url) != requested_post_id:
        raise ValueError(
            "Facebook redirected to a different post; scrolling was skipped to prevent unrelated data."
        )
    _ensure_all_comments_ar(page)
    scrolls, reply_clicks, matched_buttons = _scroll_to_end(page, max_scrolls=max_scrolls, idle_seconds=idle_seconds)
    blocks = _extract_dom_blocks(page, requested_post_id)
    return {
        "post_url": post_url,
        "blocks": blocks,
        "dom_based": True,
        "scrolls": scrolls,
        "reply_clicks": reply_clicks,
        "matched_buttons": matched_buttons[:50],
    }
