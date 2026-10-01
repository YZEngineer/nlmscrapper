import re
import time
from urllib.parse import parse_qs, urlsplit

from scraper.post_comments_dom import _validate_post_url
from scraper.comments_filter import filter_comment_data


def _requested_identity(post_url):
    path = urlsplit(post_url).path
    match = re.search(r"/groups/([^/]+)/(?:posts|permalink)/([^/]+)/?$", path, re.IGNORECASE)
    if not match:
        raise ValueError("Get Comments requires a Facebook group post URL containing both group and post IDs.")
    return match.group(1), match.group(2)


_TARGET_POST_DOM_JS = r"""({groupId, postId, action}) => {
  const isVisible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && rect.width > 0 && rect.height > 0;
  };
  const isTargetPermalink = (href) => {
    try {
      const url = new URL(href, location.href);
      if (!/(^|\.)facebook\.com$/i.test(url.hostname)) return false;
      const path = url.pathname;
      const groupPost = path.match(/\/groups\/([^/]+)\/(?:posts|permalink)\/([^/?]+)/i);
      if (groupPost) {
        return groupPost[1] === groupId && groupPost[2] === postId;
      }
      const permalink = path.match(/\/permalink\/([^/?]+)/i);
      return !!permalink && permalink[1] === postId
        && (url.searchParams.get('id') === groupId
          || url.searchParams.get('group_id') === groupId);
    } catch (_) {
      return false;
    }
  };

  const targetLinks = Array.from(document.querySelectorAll('a[href]'))
    .filter((link) => isTargetPermalink(link.href) && isVisible(link));
  if (!targetLinks.length) {
    return {error: 'No visible permalink matching both the requested group ID and post ID was found.'};
  }

  const targetArticles = Array.from(new Set(targetLinks
    .map((link) => link.closest('[role="article"]'))
    .filter((article) => article && isVisible(article))));
  if (!targetArticles.length) {
    return {error: 'The requested group post link was found outside a visible post article.'};
  }

  const targetDialogs = Array.from(document.querySelectorAll('[role="dialog"]'))
    .filter((dialog) => isVisible(dialog) && targetLinks.some((link) => dialog.contains(link)));
  targetDialogs.sort((a, b) => a.querySelectorAll('[role="article"]').length
    - b.querySelectorAll('[role="article"]').length);

  let scope;
  if (targetDialogs.length) {
    const outermost = targetDialogs.filter((dialog) =>
      !targetDialogs.some((other) => other !== dialog && other.contains(dialog)));
    if (outermost.length > 1) {
      return {error: 'Multiple visible dialogs match the requested group post.'};
    }
    scope = targetDialogs[0];
  } else if (targetArticles.length === 1) {
    scope = targetArticles[0];
  } else {
    const withComments = targetArticles.filter((article) =>
      article.querySelectorAll('[role="article"]').length > 0);
    if (withComments.length === 1) {
      scope = withComments[0];
    } else {
      return {error: 'Multiple matching group post articles were found; refusing to mix their comments.'};
    }
  }

  if (action === 'sort') {
    const triggers = Array.from(scope.querySelectorAll('[role="button"][aria-haspopup="menu"]'));
    const trigger = triggers.find((button) => {
      const text = (button.innerText || '').replace(/\s+/g, ' ').trim();
      return text.length <= 60 && /الأكثر ملاءمة|الأحدث|كل التعليقات/.test(text);
    });
    if (!trigger) return {triggerClicked: false};
    trigger.click();
    return {triggerClicked: true};
  }

  if (action === 'select-all-comments') {
    const items = Array.from(document.querySelectorAll('[role^="menuitem"]'))
      .filter(isVisible);
    const item = items.find((el) => (el.innerText || '').replace(/\ufeff/g, '').trim()
      .startsWith('كل التعليقات'));
    if (!item) return {selected: false};
    item.click();
    return {selected: true};
  }

  if (action === 'scroll') {
    const ancestors = [];
    for (let node = scope; node && node !== document.body; node = node.parentElement) {
      ancestors.push(node);
      if (node.getAttribute('role') === 'dialog') break;
    }
    const candidates = [...ancestors, ...scope.querySelectorAll('*')]
      .filter((el) => {
        const style = getComputedStyle(el);
        return isVisible(el)
          && (style.overflowY === 'auto' || style.overflowY === 'scroll')
          && el.scrollHeight > el.clientHeight + 100
          && el.clientHeight > 120;
      });
    candidates.sort((a, b) => b.scrollHeight - a.scrollHeight);
    const target = candidates[0] || scope;
    target.scrollTop = target.scrollHeight;
    return {
      count: scope.querySelectorAll('[role="article"]').length + (scope.matches('[role="article"]') ? 1 : 0),
      chars: (scope.innerText || '').length,
      height: target.scrollHeight,
      top: target.scrollTop
    };
  }

  if (action === 'expand-replies') {
    const matches = [];
    let clicked = 0;
    for (const el of scope.querySelectorAll('button, [role="button"], a, span, div')) {
      if (!el.isConnected || typeof el.click !== 'function') continue;
      const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
      if (!text) continue;
      const compact = text.replace(/[\s\u200C\u200D]+/g, '');
      if (compact === 'رد' || /الأصدقاء|أصدقاء/i.test(text)) continue;
      const expandable = /(عرض\s*(?:رد|ردود|الرد|\d+\s*رد|\d+\s*ردود)|عرض\s*\d+\s*ردود|عرض\s*\d+\s*رد)/iu;
      const direct = /(ردود|الرد|رد\s*[\d\u0660-\u0669])/iu;
      if (!expandable.test(text) && !direct.test(text)) continue;
      const rect = el.getBoundingClientRect();
      if (!rect || rect.width < 12 || rect.height < 12) continue;
      if (el.dataset.commentReplyClicked === '1') continue;
      el.dataset.commentReplyClicked = '1';
      try {
        el.click();
        clicked += 1;
        matches.push(text.slice(0, 160));
        if (clicked >= 25) break;
      } catch (_) {
        continue;
      }
    }
    return {clicked, matches: matches.slice(0, 25)};
  }

  if (action === 'signature') {
    const articles = Array.from(scope.querySelectorAll('[role="article"]'));
    if (scope.matches('[role="article"]')) articles.unshift(scope);
    return {
      count: articles.length,
      chars: articles.reduce((total, el) => total + (el.innerText || '').length, 0)
    };
  }

  const articles = Array.from(scope.querySelectorAll('[role="article"]'));
  if (scope.matches('[role="article"]')) articles.unshift(scope);
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
    return {error: 'The verified group post scope did not contain any readable article blocks.'};
  }
  return {blocks};
}"""


def _extract_group_post_blocks(page, group_id, post_id):
    result = _target_action(page, group_id, post_id, "extract")
    if not isinstance(result, dict):
        raise ValueError("Facebook returned an invalid result while reading the requested group post.")
    if result.get("error"):
        raise ValueError(result["error"])
    blocks = result.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("No article blocks were found inside the requested group post.")
    return blocks


def _target_action(page, group_id, post_id, action):
    result = page.evaluate(
        _TARGET_POST_DOM_JS,
        {"groupId": group_id, "postId": post_id, "action": action},
    )
    if not isinstance(result, dict):
        raise ValueError("Facebook returned an invalid result while locating the requested group post.")
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def _ensure_target_comments_ar(page, group_id, post_id):
    trigger_result = _target_action(page, group_id, post_id, "sort")
    if not trigger_result.get("triggerClicked"):
        return False
    page.wait_for_timeout(600)
    selected = _target_action(page, group_id, post_id, "select-all-comments")
    if selected.get("selected"):
        page.wait_for_timeout(1500)
        return True
    page.keyboard.press("Escape")
    return False


def _scroll_to_target_end(page, group_id, post_id, max_scrolls=1000, idle_seconds=5):
    last_signature = _target_action(page, group_id, post_id, "signature")
    last_change = time.monotonic()
    reply_clicks = 0
    matched_buttons = []
    for scroll_number in range(1, max_scrolls + 1):
        page.evaluate(
            _TARGET_POST_DOM_JS,
            {"groupId": group_id, "postId": post_id, "action": "scroll"},
        )
        page.wait_for_timeout(250)
        metrics = _target_action(page, group_id, post_id, "expand-replies")
        reply_clicks += metrics.get("clicked", 0)
        matched_buttons.extend(metrics.get("matches", []))
        signature = _target_action(page, group_id, post_id, "signature")
        if (
            signature.get("count", 0) > last_signature.get("count", 0)
            or signature.get("chars", 0) > last_signature.get("chars", 0)
        ):
            last_signature = signature
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= idle_seconds:
            return scroll_number, reply_clicks, matched_buttons
    return max_scrolls, reply_clicks, matched_buttons


def collect_comments_data(page, post_url, max_scrolls=1000, idle_seconds=5):
    post_url = _validate_post_url(post_url)
    group_id, post_id = _requested_identity(post_url)

    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    current_url = urlsplit(page.url)
    current_host = (current_url.hostname or "").lower()
    if current_host not in ("facebook.com", "www.facebook.com", "m.facebook.com"):
        raise ValueError("Facebook redirected away from the requested post; scrolling was skipped.")
    current_path = current_url.path
    current_group = re.search(
        r"/groups/([^/]+)/(?:posts|permalink)/([^/]+)/?",
        current_path,
        re.IGNORECASE,
    )
    if current_group and (current_group.group(1) != group_id or current_group.group(2) != post_id):
        raise ValueError("Facebook redirected to a different group post; scrolling was skipped.")
    if not current_group:
        current_permalink = re.search(r"/permalink/([^/]+)/?", current_path, re.IGNORECASE)
        query = parse_qs(current_url.query)
        if current_permalink:
            if current_permalink.group(1) != post_id:
                raise ValueError("Facebook redirected to a different post; scrolling was skipped.")
        elif current_path.rstrip("/").lower() == "/permalink.php":
            if query.get("story_fbid", [None])[0] != post_id:
                raise ValueError("Facebook redirected to a different post; scrolling was skipped.")
            redirected_group = query.get("id", [None])[0]
            if redirected_group and redirected_group != group_id:
                raise ValueError("Facebook redirected to a different group post; scrolling was skipped.")
        else:
            raise ValueError("Facebook redirected to a non-post page; scrolling was skipped.")

    _ensure_target_comments_ar(page, group_id, post_id)
    scrolls, reply_clicks, matched_buttons = _scroll_to_target_end(
        page,
        group_id,
        post_id,
        max_scrolls=max_scrolls,
        idle_seconds=idle_seconds,
    )
    blocks = _extract_group_post_blocks(page, group_id, post_id)
    raw_data = {
        "post_url": post_url,
        "blocks": blocks,
        "dom_based": True,
        "scrolls": scrolls,
        "reply_clicks": reply_clicks,
        "matched_buttons": matched_buttons[:50],
    }
    return filter_comment_data(raw_data)
