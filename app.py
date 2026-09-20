import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests
from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = BASE_DIR / "data"
DATA_DIR = DATA_ROOT / "json"
TXT_DIR = DATA_ROOT / "txt"
RECORDS_DIR = DATA_ROOT / "reports"
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_ROOT.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
TXT_DIR.mkdir(exist_ok=True)
RECORDS_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

STATUS_TTL = 30
_cache_store = {}


def _cache_get(key):
    entry = _cache_store.get(key)
    if entry and time.time() - entry[0] < STATUS_TTL:
        return entry[1]
    return None


def _cache_set(key, value):
    _cache_store[key] = (time.time(), value)


def _cache_clear(*keys):
    for k in keys:
        _cache_store.pop(k, None)


# ---------------------------------------------------------------- SocialAPIs
FB_BASE = "https://api.socialapis.io"
FB_GROUP_POSTS = FB_BASE + "/facebook/groups/posts"
FB_POST_COMMENTS = FB_BASE + "/facebook/posts/comments"
FB_POST_REPLIES = FB_BASE + "/facebook/posts/comments/replies"

PAGE_LIMIT = 5
MAX_REPLY_DEPTH = 5
REQUEST_DELAY = 0.4


class SocialAPIFetchError(RuntimeError):
    pass


def get_headers(token):
    return {"x-api-token": token, "Content-Type": "application/json"}


def fb_call(token, url, params):
    resp = requests.get(url, headers=get_headers(token), params=params, timeout=120)
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return resp.status_code, body


def _to_iso(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(value).isoformat() + "Z"
        except (ValueError, OSError):
            return str(value)
    return str(value)


def _author_from(post):
    details = post.get("details", {}) or {}
    values = post.get("values", {}) or {}
    author = details.get("author") or (values.get("author") or {})
    if isinstance(author, dict):
        return {"name": author.get("name") or "", "url": author.get("url") or ""}
    if isinstance(author, str):
        return {"name": author, "url": ""}
    return {"name": "", "url": ""}


def _post_text(post):
    values = post.get("values", {}) or {}
    details = post.get("details", {}) or {}
    text = values.get("text") or details.get("text") or ""
    return (text or "").strip()


def _post_reactions(post, details):
    reactions = []
    values = post.get("values", {}) or {}
    field = values.get("reactions") or values.get("reaction_count") or details.get("reactions")
    if isinstance(field, dict):
        for k, v in field.items():
            reactions.append({"type": k, "count": v})
    elif isinstance(field, (int, float, str)) and field not in (None, ""):
        reactions.append({"type": "total", "count": field})
    return reactions


def _extract_comments(comment):
    values = comment.get("values", {}) or {}
    details = comment.get("details", {}) or {}

    def field(name, *aliases):
        for source in (values, details, comment):
            for key in (name,) + aliases:
                if source.get(key) not in (None, ""):
                    return source.get(key)
        return None

    author = field("author") or {}
    if isinstance(author, dict):
        a = {"name": author.get("name") or "", "url": author.get("url") or ""}
    elif isinstance(author, str):
        a = {"name": author, "url": ""}
    else:
        a = {"name": "", "url": ""}
    out = {
        "author": a,
        "text": (field("text", "comment_text", "preferred_body_text") or "").strip(),
        "created_time": _to_iso(field("created_time")),
        "replies": [],
    }
    return out, field("comment_feedback_id"), field("expansion_token")


def fetch_posts(token, link, limit, start=None, end=None):
    posts = []
    cursor = None
    seen_cursors = set()
    seen_posts = set()
    while len(posts) < limit:
        params = {"link": link, "limit": PAGE_LIMIT, "timezone": "UTC"}
        if cursor:
            params["end_cursor"] = cursor
        status, body = fb_call(token, FB_GROUP_POSTS, params)
        if status != 200:
            e = SocialAPIFetchError(
                "posts HTTP {0}: {1}".format(status, json.dumps(body, ensure_ascii=False)[:400])
            )
            e.partial_posts = posts
            raise e
        data = body.get("data", {}) or {}
        page = data.get("posts", []) or []
        for p in page:
            details = p.get("details", {}) or {}
            values = p.get("values", {}) or {}
            post_key = details.get("post_id") or values.get("post_id") or details.get("post_link")
            if post_key and post_key in seen_posts:
                continue
            if post_key:
                seen_posts.add(post_key)
            created = _to_iso(
                values.get("created_time") or details.get("created_time")
            )
            if start and created and created[:10] < start:
                continue
            if end and created and created[:10] > end:
                continue
            posts.append(p)
            if len(posts) >= limit:
                break
        page_info = data.get("page_info", {}) or {}
        next_cursor = page_info.get("end_cursor") or body.get("next_cursor")
        has_next = page_info.get("has_next")
        if not page or has_next is False or not next_cursor:
            break
        if next_cursor in seen_cursors:
            e = SocialAPIFetchError("تكرار المؤشر في ترقيم الصفحات؛ البيانات قد تكون ناقصة")
            e.partial_posts = posts
            raise e
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        time.sleep(REQUEST_DELAY)
    return posts[:limit]


def fetch_comments(token, post_link):
    comments = []
    cursor = None
    seen_cursors = set()
    seen_comments = set()
    while True:
        params = {"link": post_link, "include_reply_info": True}
        if cursor:
            params["end_cursor"] = cursor
        status, body = fb_call(token, FB_POST_COMMENTS, params)
        if status != 200:
            e = SocialAPIFetchError(
                "comments HTTP {0}: {1}".format(status, json.dumps(body, ensure_ascii=False)[:200])
            )
            e.partial_comments = comments
            raise e
        data = body.get("data", {}) or {}
        page = data.get("comments", []) or []
        for comment in page:
            values = comment.get("values", {}) or {}
            details = comment.get("details", {}) or {}
            key = (values.get("comment_feedback_id") or details.get("comment_feedback_id")
                   or values.get("id") or details.get("id"))
            if not key or key not in seen_comments:
                comments.append(comment)
                if key:
                    seen_comments.add(key)
        page_info = data.get("page_info", {}) or {}
        next_cursor = page_info.get("end_cursor") or data.get("next_cursor") or body.get("next_cursor")
        has_next = page_info.get("has_next")
        if not page or has_next is False or not next_cursor:
            break
        if next_cursor in seen_cursors:
            e = SocialAPIFetchError("تكرار المؤشر في ترقيم الصفحات؛ البيانات قد تكون ناقصة")
            e.partial_comments = comments
            raise e
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        time.sleep(REQUEST_DELAY)
    return comments


def fetch_comments_limited(token, post_link, max_comments):
    if max_comments <= 0:
        return []
    comments = []
    cursor = None
    seen_cursors = set()
    seen_comments = set()
    while len(comments) < max_comments:
        params = {"link": post_link, "include_reply_info": True}
        if cursor:
            params["end_cursor"] = cursor
        status, body = fb_call(token, FB_POST_COMMENTS, params)
        if status != 200:
            e = SocialAPIFetchError(
                "comments HTTP {0}: {1}".format(status, json.dumps(body, ensure_ascii=False)[:200])
            )
            e.partial_comments = comments
            raise e
        data = body.get("data", {}) or {}
        page = data.get("comments", []) or []
        for comment in page:
            if len(comments) >= max_comments:
                break
            values = comment.get("values", {}) or {}
            details = comment.get("details", {}) or {}
            key = (values.get("comment_feedback_id") or details.get("comment_feedback_id")
                   or values.get("id") or details.get("id"))
            if not key or key not in seen_comments:
                comments.append(comment)
                if key:
                    seen_comments.add(key)
        page_info = data.get("page_info", {}) or {}
        next_cursor = page_info.get("end_cursor") or data.get("next_cursor") or body.get("next_cursor")
        has_next = page_info.get("has_next")
        if not page or has_next is False or not next_cursor:
            break
        if next_cursor in seen_cursors:
            e = SocialAPIFetchError("تكرار المؤشر في ترقيم الصفحات؛ البيانات قد تكون ناقصة")
            e.partial_comments = comments
            raise e
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        time.sleep(REQUEST_DELAY)
    return comments[:max_comments]


def fetch_replies(token, comment):
    values = comment.get("values", {}) or {}
    comment_feedback_id = (
        values.get("comment_feedback_id") or comment.get("comment_feedback_id")
    )
    expansion_token = (
        values.get("expansion_token") or comment.get("expansion_token")
    )
    replies_count = ((comment.get("feedback") or {}).get("replies_count")) or 0
    if not comment_feedback_id or not replies_count:
        return []
    replies = []
    cursor = expansion_token
    seen_cursors = set()
    seen_replies = set()
    while True:
        params = {"comment_feedback_id": comment_feedback_id, "expansion_token": cursor}
        status, body = fb_call(token, FB_POST_REPLIES, params)
        if status != 200:
            e = SocialAPIFetchError(
                "replies HTTP {0}: {1}".format(status, json.dumps(body, ensure_ascii=False)[:200])
            )
            e.partial_replies = replies
            raise e
        data = body.get("data", {}) or {}
        page = data.get("comment_replies", []) or []
        for reply in page:
            values = reply.get("values", {}) or {}
            details = reply.get("details", {}) or {}
            key = (values.get("comment_feedback_id") or details.get("comment_feedback_id")
                   or values.get("id") or details.get("id"))
            if not key or key not in seen_replies:
                replies.append(reply)
                if key:
                    seen_replies.add(key)
        page_info = data.get("page_info", {}) or {}
        next_cursor = page_info.get("end_cursor")
        has_next = page_info.get("has_next")
        if not page or has_next is False or not next_cursor:
            break
        if next_cursor in seen_cursors:
            e = SocialAPIFetchError("تكرار مؤشر الردود في ترقيم الصفحات؛ البيانات قد تكون ناقصة")
            e.partial_replies = replies
            raise e
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        time.sleep(REQUEST_DELAY)
    return replies


def _normalize_comment(token, c, include_replies, depth=0):
    out, comment_feedback_id, _ = _extract_comments(c)
    out["id"] = comment_feedback_id or ""
    partial = False
    if include_replies and depth < MAX_REPLY_DEPTH:
        try:
            raw_replies = fetch_replies(token, c)
        except SocialAPIFetchError as e:
            if not hasattr(e, "partial_replies") or not e.partial_replies:
                e.partial_comment = out
                raise
            raw_replies = e.partial_replies
            partial = True
        for raw in raw_replies:
            r = _normalize_comment(token, raw, True, depth + 1)
            out["replies"].append(r)
    out["_partial"] = partial
    return out


def _strip_partial(posts):
    for p in posts:
        for c in p.get("comments", []):
            c.pop("_partial", None)
            for r in c.get("replies", []):
                r.pop("_partial", None)
    return posts


def _entry_from_post(raw):
    details = raw.get("details", {}) or {}
    values = raw.get("values", {}) or {}
    return {
        "id": details.get("post_id") or values.get("post_id") or "",
        "url": details.get("post_link") or values.get("post_link") or "",
        "author": _author_from(raw),
        "text": _post_text(raw),
        "created_time": _to_iso(values.get("created_time") or details.get("created_time")),
        "reactions": _post_reactions(raw, details),
        "comments": [],
    }


def scrape_group(token, group_url, start, end, post_limit, comments_per_post, include_replies, on_progress=None):
    """Her post/yorum/yanit eklendikce on_progress(posts) cagrilir.
    Sabit bir istek hatasinda kalan iscim durdurulur ve (posts, error) dondurulur."""
    posts = []
    error = None

    def commit():
        if on_progress:
            on_progress(list(posts))

    try:
        raw_posts = fetch_posts(token, group_url, post_limit, start or None, end or None)
    except SocialAPIFetchError as e:
        raw_posts = getattr(e, "partial_posts", []) or []
        if not raw_posts:
            return posts, str(e)
        error = str(e)

    for raw in raw_posts:
        entry = _entry_from_post(raw)
        posts.append(entry)
        commit()

        post_url = entry["url"]
        if post_url and comments_per_post > 0:
            try:
                comments_raw = fetch_comments_limited(token, post_url, comments_per_post)
            except SocialAPIFetchError as e:
                comments_raw = getattr(e, "partial_comments", []) or []
                if not comments_raw:
                    return posts, (error or str(e))
                if error is None:
                    error = str(e)
            for c in comments_raw:
                try:
                    norm = _normalize_comment(token, c, include_replies)
                except SocialAPIFetchError as e:
                    pc = getattr(e, "partial_comment", None)
                    if pc is not None:
                        entry["comments"].append(pc)
                        commit()
                    return posts, (error or str(e))
                entry["comments"].append(norm)
                commit()
                if norm.get("_partial"):
                    return posts, (error or "التوكن ربما نفد؛ الردود جُلبَت جزئيًا")
            time.sleep(REQUEST_DELAY)
    if include_replies:
        _strip_partial(posts)
    return posts, error


def social_token():
    return os.getenv("SOCIALAPIS_TOKEN", "").strip()


# ---------------------------------------------------------------- NotebookLM
def _run(coro):
    return asyncio.run(coro)


def _nlm_report(status, error=None):
    return {"authenticated": status, "error": error or ""}


def nlm_status():
    cached = _cache_get("nlm_status")
    if cached is not None:
        return cached

    try:
        reached = {"ok": False}

        async def _check():
            from notebooklm import NotebookLMClient

            async with await NotebookLMClient.from_storage() as client:
                _ = await client.notebooks.list()
            reached["ok"] = True

        _run(_check())
        report = _nlm_report(reached["ok"])
    except Exception as e:
        msg = str(e)
        hint = "يجب إعادة تسجيل الدخول " if ("login" in msg.lower() or "expired" in msg.lower()) else ""
        report = _nlm_report(False, ( hint).strip()[:500])

    _cache_set("nlm_status", report)
    return report


def _safe_str(value, default=""):
    if value is None:
        return default
    if isinstance(value, bool):
        return str(value).lower()
    try:
        return str(value)
    except Exception:
        return default


# ------------------------------------------------------------------- Routes
@app.get("/")
def page_index():
    return render_template("index.html")


@app.get("/scrape")
def page_scrape():
    return render_template("scrape.html")


@app.get("/fbread")
def page_fbread():
    return render_template("fbread.html")


@app.get("/upload")
def page_upload():
    return render_template("upload.html")


@app.get("/chat")
def page_chat():
    return render_template("chat.html")


@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


# --- Facebook ---
@app.get("/api/fb/config")
def api_fb_config():
    token = social_token()
    masked = (token[:6] + "..." + token[-4:]) if len(token) > 12 else ""
    return jsonify({"has_token": bool(token), "token_masked": masked})


@app.post("/api/fb/token")
def api_fb_token():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    if len(token) < 10:
        return jsonify({"ok": False, "error": "التوكن غير صالح"}), 400
    os.environ["SOCIALAPIS_TOKEN"] = token
    set_key(BASE_DIR / ".env", "SOCIALAPIS_TOKEN", token)
    masked = (token[:6] + "..." + token[-4:]) if len(token) > 12 else ""
    _cache_clear("nlm_status")
    return jsonify({"ok": True, "has_token": True, "token_masked": masked})


@app.get("/api/fb/data")
def api_fb_data_list():
    files = []
    for p in sorted(DATA_DIR.glob("*.json"), reverse=True):
        files.append({
            "name": p.name,
            "size": p.stat().st_size,
            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
        })
    return jsonify({"files": files})


@app.post("/api/fb/fetch")
def api_fb_fetch():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip() or social_token()
    if not token:
        return jsonify({"ok": False, "error": "لا يوجد توكن — حدّثه من القائمة الرئيسية"}), 400

    group_urls_in = body.get("group_urls") or []
    if isinstance(group_urls_in, str):
        group_urls_in = [l.strip() for l in group_urls_in.splitlines() if l.strip()]
    single = (body.get("group_url") or "").strip()
    if single:
        group_urls_in.append(single)
    seen = set()
    group_urls = []
    for g in group_urls_in:
        if g and g not in seen:
            seen.add(g)
            group_urls.append(g)
    if not group_urls:
        return jsonify({"ok": False, "error": "اختر مجموعة واحدة على الأقل"}), 400

    try:
        post_limit = max(1, int(body.get("post_limit", 2)))
        comments_per_post = max(0, int(body.get("comments_per_post", 3)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "post_limit / comments_per_post يجب أن تكون أرقامًا"}), 400

    include_replies = bool(body.get("include_replies", False))
    date_from = (body.get("date_from") or "").strip()[:10] or None
    date_to = (body.get("date_to") or "").strip()[:10] or None

    def _slug(url):
        seg = url.rstrip("/").split("/")[-1]
        m = re.search(r"(\d+)", seg)
        return m.group(1) if m else "g"

    def _build(group_url, posts, partial=False, error=""):
        return {
            "group_url": group_url,
            "date_from": date_from,
            "date_to": date_to,
            "filters": {"post_limit": post_limit, "comments_per_post": comments_per_post, "include_replies": include_replies},
            "fetched_at": datetime.now().isoformat(),
            "partial": partial,
            "error": error or None,
            "posts": posts,
            "stats": {"posts": len(posts), "comments": sum(len(p.get("comments", [])) for p in posts)},
        }

    results = []
    for group_url in group_urls:
        fname = "scrape_{0}_{1}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"), _slug(group_url))
        dest = DATA_DIR / fname

        def _save(posts, partial=False, error=""):
            dest.write_text(json.dumps(_build(group_url, posts, partial, error), ensure_ascii=False, indent=2), encoding="utf-8")

        posts = []
        error = None
        try:
            posts, error = scrape_group(
                token, group_url, date_from, date_to, post_limit,
                comments_per_post, include_replies,
                on_progress=lambda posts: _save(posts, partial=True),
            )
        except Exception as e:
            error = error or str(e)
            _save(posts, partial=True, error=error)

        partial_flag = bool(error) and len(posts) > 0
        fname = None
        if posts:
            _save(posts, partial=partial_flag, error=error)
            fname = dest.name
            _json_txt_for(Path(fname).stem)

        results.append({
            "ok": not error,
            "group_url": group_url,
            "file": fname,
            "rid": Path(fname).stem if fname else None,
            "txt": ((Path(fname).stem if fname else "") + ".txt"),
            "posts": len(posts),
            "stats": {"posts": len(posts), "comments": sum(len(p.get("comments", [])) for p in posts)},
            "partial": partial_flag,
            "error": error or None,
        })

    all_ok = all(r["ok"] and not r["error"] for r in results)
    return jsonify({"ok": all_ok, "results": results})


@app.get("/api/fb/data/<path:fname>")
def api_fb_data_get(fname):
    p = (DATA_DIR / fname).resolve()
    if p.parent != DATA_DIR.resolve() or not p.exists():
        return jsonify({"error": "الملف غير موجود"}), 404
    return jsonify({"file": fname, "content": json.loads(p.read_text(encoding="utf-8"))})


# --- Facebook grup listesi ---
def _load_groups():
    f = DATA_ROOT / "groups.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_groups(groups):
    f = DATA_ROOT / "groups.json"
    f.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")


def _group_id_from_url(url):
    seg = url.rstrip("/").split("/")[-1]
    m = re.search(r"(\d+)", seg)
    return m.group(1) if m else seg


@app.get("/api/fb/groups")
def api_fb_groups_list():
    return jsonify({"groups": _load_groups()})


@app.post("/api/fb/groups")
def api_fb_groups_add():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    link = (body.get("link") or "").strip()
    if not name or not link:
        return jsonify({"ok": False, "error": "الاسم والرابط مطلوبان"}), 400
    groups = _load_groups()
    gid = _group_id_from_url(link)
    for g in groups:
        if (g.get("id") == gid) or ((g.get("link") or "").rstrip("/") == link.rstrip("/")):
            return jsonify({"ok": False, "error": "المجموعة موجودة بالفعل"}), 409
    groups.append({
        "id": gid,
        "name": name,
        "link": link,
        "createdAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    })
    _save_groups(groups)
    return jsonify({"ok": True, "groups": groups})


@app.delete("/api/fb/groups/<gid>")
def api_fb_groups_delete(gid):
    groups = _load_groups()
    kept = [g for g in groups if g.get("id") != gid]
    if len(kept) == len(groups):
        return jsonify({"ok": False, "error": "المجموعة غير موجودة"}), 404
    _save_groups(kept)
    return jsonify({"ok": True, "groups": kept})


# --- Raporlar: JSON -> TXT -> NLM ---
_SAFE_STEM_RE = re.compile(r"^[\w\-]+$")


def _json_txt_for(stem):
    """JSON karsiligi TXT kopyasini data/txt altinda saglar (yoksa uretir)."""
    src = DATA_DIR / (stem + ".json")
    dest = TXT_DIR / (stem + ".txt")
    if src.exists() and not dest.exists():
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


@app.get("/api/fb/reports")
def api_fb_reports():
    nb_id = (request.args.get("nb_id") or "").strip() or None
    nb_stems = None
    nb_titles = None
    if nb_id:
        try:
            sources = _run(_nlm_list_sources(nb_id))
            nb_stems = set()
            nb_titles = set()
            for s in sources:
                t = _safe_str(getattr(s, "title", "") or "").strip().lower()
                if not t:
                    continue
                nb_titles.add(t)
                stem = t.rsplit(".", 1)[0] if "." in t else t
                nb_stems.add(stem)
        except Exception:
            nb_stems = None
    out = []
    for fname in sorted(DATA_DIR.glob("*.json")):
        rid = fname.stem
        if not _SAFE_STEM_RE.match(rid):
            continue
        txt = _json_txt_for(rid)
        in_notebook = None
        if nb_stems is not None:
            rl = rid.lower()
            in_notebook = any(
                st == rl
                or st == rl + ".txt"
                or st == rl + ".json"
                or any(t == rl + ".txt" or t == rl for t in (nb_titles or set()))
                for st in nb_stems
            )
        partial = False
        try:
            meta = json.loads(fname.read_text(encoding="utf-8"))
            partial = bool(meta.get("partial")) if isinstance(meta, dict) else False
        except Exception:
            partial = False
        out.append({
            "id": rid,
            "name": fname.name,
            "size": fname.stat().st_size,
            "has_txt": txt.exists(),
            "txt_size": txt.stat().st_size if txt.exists() else 0,
            "in_notebook": in_notebook,
            "partial": partial,
        })
    return jsonify({"reports": out})


@app.post("/api/fb/rename")
def api_fb_report_rename():
    body = request.get_json(silent=True) or {}
    old = (body.get("name") or "").strip()
    new = (body.get("new") or "").strip()
    if not _SAFE_STEM_RE.match(old) or not _SAFE_STEM_RE.match(new):
        return jsonify({"ok": False, "error": "اسم ملف غير صالح"}), 400
    src = DATA_DIR / (old + ".json")
    if not src.exists():
        return jsonify({"ok": False, "error": "التقرير غير موجود"}), 404
    if new == old:
        return jsonify({"ok": True, "renamed": src.name})
    tgt = DATA_DIR / (new + ".json")
    if tgt.exists() or (TXT_DIR / (new + ".txt")).exists():
        return jsonify({"ok": False, "error": "الاسم موجود مسبقًا (تعارض)"}), 409
    otxt = TXT_DIR / (old + ".txt")
    src.rename(tgt)
    if otxt.exists():
        otxt.rename(TXT_DIR / (new + ".txt"))
    _json_txt_for(new)
    return jsonify({"ok": True, "renamed": new + ".json"})


@app.post("/api/fb/merge")
def api_fb_report_merge():
    body = request.get_json(silent=True) or {}
    names = body.get("names") or []
    if isinstance(names, str):
        names = [names]
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names:
        return jsonify({"ok": False, "error": "لم نحدّد أي تقرير"}), 400
    out = (body.get("out") or "").strip() or ("merged_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    if not _SAFE_STEM_RE.match(out):
        return jsonify({"ok": False, "error": "اسم الملف المدمج غير صالح"}), 400
    if (DATA_DIR / (out + ".json")).exists() or (TXT_DIR / (out + ".txt")).exists():
        return jsonify({"ok": False, "error": "الاسم موجود مسبقًا (تعارض)"}), 409
    merged = {}
    sources = []
    group_urls = []
    total_partial = False
    total_err = []
    for n in names:
        if not _SAFE_STEM_RE.match(n):
            continue
        path = DATA_DIR / (n + ".json")
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sources.append(path.name)
        if not isinstance(data, dict):
            continue
        if data.get("partial"):
            total_partial = True
        if data.get("error"):
            total_err.append(str(data["error"]))
        gu = str(data.get("group_url") or "").strip()
        if gu and gu not in group_urls:
            group_urls.append(gu)
        for p in (data.get("posts") or []):
            u = str(p.get("url") or "").strip()
            if u and u not in merged:
                merged[u] = p
    if not sources:
        return jsonify({"ok": False, "error": "لا توجد تقارير للدمج"}), 404
    if not merged:
        return jsonify({"ok": False, "error": "لا توجد منشورات للدمج"}), 400
    posts = list(merged.values())
    payload = {
        "group_url": group_urls[0] if group_urls else "",
        "group_name": "merged",
        "source": "merged",
        "fetched_at": datetime.now().isoformat(),
        "partial": total_partial,
        "error": (total_err[0] if total_err else None),
        "merged_from": sources,
        "posts": posts,
    }
    dest = DATA_DIR / (out + ".json")
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _json_txt_for(out)
    return jsonify({"ok": True, "name": dest.name, "posts": len(posts), "sources": len(sources)})


@app.get("/api/fb/reports/<rid>")
def api_fb_report_raw(rid):
    if not _SAFE_STEM_RE.match(rid):
        return jsonify({"ok": False, "error": "معرّف غير صالح"}), 400
    src = DATA_DIR / (rid + ".json")
    if not src.exists():
        return jsonify({"ok": False, "error": "التقرير غير موجود"}), 404
    try:
        content = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"ok": False, "error": "ملف التقرير تالف (JSON)"}), 500
    return jsonify({"ok": True, "id": rid, "name": src.name, "content": content})


@app.get("/fb/reports/<rid>/view")
def fb_report_view(rid):
    if not _SAFE_STEM_RE.match(rid):
        return render_template("report_viewer.html", title="معرّف غير صالح", group_id="", posts=[], post_count=0, comment_count=0), 400
    src = DATA_DIR / (rid + ".json")
    if not src.exists():
        return render_template("report_viewer.html", title="غير موجود", group_id=rid, posts=[], post_count=0, comment_count=0), 404
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return render_template("report_viewer.html", title="JSON غير صالح", group_id=rid,
                               posts=[], post_count=0, comment_count=0,
                               is_partial=False, error_txt="ملف التقرير تالف"), 500

    def _safe(v):
        return _safe_str(v).strip()

    def _field(d, *keys):
        for k in keys:
            v = d.get(k)
            if v not in (None, "", []):
                return v
        return ""

    def _author(d):
        a = d.get("author")
        if isinstance(a, dict):
            return _safe(a.get("name") or a.get("label") or a.get("username"))
        return _safe(a) if a not in (None, "") else _safe(_field(d, "name", "from_name"))

    def _name(d):
        return _field(d, "text", "comment_text", "message", "body", "post_text", "preferred_body_text")

    def _dated(d):
        return _safe(_field(d, "created_time", "created_at", "date"))

    def _reac(d):
        r = _field(d, "reactions", "feedback")
        return _safe(str(r)) if r not in (None, "") else ""

    def _replies(d):
        out = []
        for r in (d.get("replies") or [] if isinstance(d.get("replies"), list) else []):
            if not isinstance(r, dict):
                continue
            out.append({
                "author": _author(r),
                "text": _name(r),
                "date": _dated(r),
                "reactions": _reac(r),
            })
        return out

    post_count = 0
    comment_count = 0
    group_id = ""
    posts = []

    if isinstance(data, dict):
        if isinstance(data.get("posts"), list):
            group_id = _safe(_field(data, "group_url", "group_name")).rsplit("/", 1)[-1]
            raw_posts = data["posts"]
        elif isinstance(data.get("collection"), list):
            grp = data.get("group") or {}
            group_id = _safe(grp.get("name") if isinstance(grp, dict) else grp)
            raw_posts = data["collection"]
        else:
            raw_posts = []
    elif isinstance(data, list):
        if data:
            grp = (data[0].get("group") if isinstance(data[0], dict) and isinstance(data[0].get("group"), dict) else None)
            group_id = _safe(grp.get("name") if isinstance(grp, dict) else "") if grp is not None else ""
        raw_posts = data
    else:
        raw_posts = []

    for p in raw_posts:
        if not isinstance(p, dict):
            continue
        comments = []
        for c in (p.get("comments") or [] if isinstance(p.get("comments"), list) else []):
            if not isinstance(c, dict):
                continue
            replies = _replies(c)
            comments.append({
                "author": _author(c),
                "text": _name(c),
                "date": _dated(c),
                "reactions": _reac(c),
                "replies": replies,
            })
            comment_count += 1 + len(replies)
        posts.append({
            "id": _safe(_field(p, "id", "post_id")),
            "url": _safe(_field(p, "url", "link", "post_link", "permalink_url")),
            "text": _name(p),
            "date": _dated(p),
            "comment_count": len(comments),
            "comments": comments,
        })
        post_count += 1

    title = src.name
    error_txt = _safe(data.get("error")) if isinstance(data, dict) else ""
    is_partial = bool(data.get("partial")) if isinstance(data, dict) else False
    return render_template(
        "report_viewer.html",
        title=title,
        group_id=group_id or rid,
        posts=posts,
        post_count=post_count,
        comment_count=comment_count,
        is_partial=is_partial,
        error_txt=error_txt,
    )


@app.post("/api/fb/reports/<rid>/txt")
def api_fb_report_make_txt(rid):
    if not _SAFE_STEM_RE.match(rid):
        return jsonify({"ok": False, "error": "معرّف غير صالح"}), 400
    src = DATA_DIR / (rid + ".json")
    if not src.exists():
        return jsonify({"ok": False, "error": "التقرير غير موجود"}), 404
    dest = _json_txt_for(rid)
    return jsonify({"ok": True, "id": rid, "size": dest.stat().st_size})


@app.delete("/api/fb/reports/<rid>/txt")
def api_fb_report_delete_txt(rid):
    if not _SAFE_STEM_RE.match(rid):
        return jsonify({"ok": False, "error": "معرّف غير صالح"}), 400
    dest = TXT_DIR / (rid + ".txt")
    if dest.exists():
        dest.unlink()
    return jsonify({"ok": True, "id": rid, "deleted": True})


@app.post("/api/nlm/notebooks/<nb_id>/sources/from-report/<rid>")
def api_nlm_add_report_source(nb_id, rid):
    if not _SAFE_STEM_RE.match(rid):
        return jsonify({"ok": False, "error": "معرّف غير صالح"}), 400
    txt = _json_txt_for(rid)
    if not txt.exists():
        return jsonify({"ok": False, "error": "التقرير غير موجود"}), 404
    try:
        src = _run(_nlm_add_file(nb_id, txt, True))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500
    _cache_clear("nlm_notebooks")
    return jsonify({
        "ok": True,
        "source": {
            "id": _safe_str(getattr(src, "id", "")),
            "title": _safe_str(getattr(src, "title", "") or "بدون اسم"),
            "url": _safe_str(getattr(src, "url", "")),
            "status": _safe_str(getattr(src, "status", "")),
        },
    })


# --- NotebookLM hesap ---
@app.get("/api/nlm/status")
def api_nlm_status():
    return jsonify(nlm_status())


@app.post("/api/nlm/login")
def api_nlm_login():
    try:
        CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        subprocess.Popen(
            [sys.executable, "-m", "notebooklm", "login"],
            creationflags=CREATE_NEW_CONSOLE,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500
    _cache_clear("nlm_status", "nlm_notebooks")
    return jsonify({
        "ok": True,
        "pending": True,
        "message": "تم فتح نافذة تسجيل دخول منفصلة — أكمل الدخول في المتصفح ثم اضغط Enter في تلك النافذة",
    })


# --- NotebookLM defterler ---
@app.get("/api/nlm/notebooks")
def api_nlm_notebooks():
    cached = _cache_get("nlm_notebooks")
    if cached is not None:
        return jsonify(cached)
    try:
        notebooks = _run(_nlm_list_notebooks())
        out = []
        for nb in notebooks:
            out.append({
                "id": _safe_str(getattr(nb, "id", "")),
                "title": _safe_str(getattr(nb, "title", "") or getattr(nb, "name", "") or "بدون اسم"),
                "sources_count": getattr(nb, "sources_count", None) or 0,
                "is_owner": bool(getattr(nb, "is_owner", True)),
                "created_at": _safe_str(getattr(nb, "created_at", "")),
            })
        data = {"notebooks": out}
        _cache_set("nlm_notebooks", data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:500]}), 500


async def _nlm_list_notebooks():
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        return await client.notebooks.list()


@app.post("/api/nlm/notebooks")
def api_nlm_create():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "العنوان مطلوب"}), 400

    async def _create():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            nb = await client.notebooks.create(title)
            return {"id": _safe_str(nb.id), "title": _safe_str(nb.title or title)}

    try:
        _cache_clear("nlm_notebooks")
        return jsonify({"ok": True, "notebook": _run(_create())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500


@app.delete("/api/nlm/notebooks/<nb_id>")
def api_nlm_delete(nb_id):
    async def _delete():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            await client.notebooks.delete(nb_id)

    try:
        _run(_delete())
        _cache_clear("nlm_notebooks")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500


# --- NotebookLM kaynaklar ---
@app.get("/api/nlm/notebooks/<nb_id>/sources")
def api_nlm_sources(nb_id):
    try:
        sources = _run(_nlm_list_sources(nb_id))
        out = []
        for s in sources:
            out.append({
                "id": _safe_str(getattr(s, "id", "")),
                "title": _safe_str(getattr(s, "title", "") or "بدون اسم"),
                "url": _safe_str(getattr(s, "url", "")),
                "status": _safe_str(getattr(s, "status", "")),
                "kind": _safe_str(getattr(s, "kind", "") or getattr(s, "type", "")),
                "created_at": _safe_str(getattr(s, "created_at", "")),
            })
        return jsonify({"sources": out})
    except Exception as e:
        return jsonify({"error": str(e)[:500]}), 500


async def _nlm_list_sources(nb_id):
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        return await client.sources.list(nb_id)


@app.post("/api/nlm/notebooks/<nb_id>/sources")
def api_nlm_add_source(nb_id):
    source_type = (request.form.get("type") or "file").strip()
    wait_ready = (request.form.get("wait") or "1") == "1"

    try:
        if source_type == "url":
            url = (request.form.get("url") or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "الرابط مطلوب"}), 400
            src = _run(_nlm_add_url(nb_id, url, wait_ready))
        elif source_type == "text":
            title = (request.form.get("title") or "نص لصق").strip()
            content = (request.form.get("text") or "").strip()
            if not content:
                return jsonify({"ok": False, "error": "النص مطلوب"}), 400
            src = _run(_nlm_add_text(nb_id, title, content, wait_ready))
        else:
            file = request.files.get("file")
            if not file or not file.filename:
                return jsonify({"ok": False, "error": "لم يتم اختيار ملف"}), 400
            safe_name = re.sub(r"[^\w.\-]+", "_", Path(file.filename).name)
            dest = UPLOAD_DIR / safe_name
            file.save(dest)
            src = _run(_nlm_add_file(nb_id, dest, wait_ready))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500

    _cache_clear("nlm_notebooks")
    return jsonify({
        "ok": True,
        "source": {
            "id": _safe_str(getattr(src, "id", "")),
            "title": _safe_str(getattr(src, "title", "") or "بدون اسم"),
            "url": _safe_str(getattr(src, "url", "")),
            "status": _safe_str(getattr(src, "status", "")),
        },
    })


async def _nlm_add_url(nb_id, url, wait):
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        return await client.sources.add_url(nb_id, url, wait=wait, wait_timeout=180)


async def _nlm_add_text(nb_id, title, content, wait):
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        return await client.sources.add_text(nb_id, title, content, wait=wait, wait_timeout=180)


async def _nlm_add_file(nb_id, path, wait):
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        return await client.sources.add_file(nb_id, path, wait=wait, wait_timeout=180)


@app.delete("/api/nlm/notebooks/<nb_id>/sources/<sid>")
def api_nlm_delete_source(nb_id, sid):
    async def _delete():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            await client.sources.delete(nb_id, sid)

    try:
        _run(_delete())
        _cache_clear("nlm_notebooks")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500


@app.post("/api/nlm/notebooks/<nb_id>/sources/upload-all")
def api_nlm_upload_all(nb_id):
    body = request.get_json(silent=True) or {}
    run_dedup = bool(body.get("dedup", True))
    files = []
    for fname in sorted(DATA_DIR.glob("*.json")):
        rid = fname.stem
        if _SAFE_STEM_RE.match(rid):
            files.append(rid)
    if not files:
        return jsonify({"ok": True, "uploaded": [], "skipped": 0, "removed": []})

    async def _upload_all():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            existing = await client.sources.list(nb_id)
            existing_titles = set()
            for s in existing:
                t = _safe_str(getattr(s, "title", "") or "").strip().lower()
                if t:
                    existing_titles.add(t)
            uploaded = []
            skipped = 0
            for rid in files:
                txt = _json_txt_for(rid)
                if not txt.exists():
                    continue
                title_low = txt.name.lower()
                stem_low = rid.lower()
                if (title_low in existing_titles) or (stem_low in existing_titles) or (stem_low + ".txt" in existing_titles):
                    skipped += 1
                    continue
                try:
                    src = await client.sources.add_file(nb_id, str(txt), wait=True, wait_timeout=180)
                    sid = _safe_str(getattr(src, "id", ""))
                    if sid:
                        uploaded.append({"id": sid, "title": txt.name})
                        existing_titles.add(title_low)
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1
            removed = []
            if run_dedup:
                removal = await _dedup_client(client, nb_id)
                removed = removal.get("removed", [])
            return {"uploaded": uploaded, "skipped": skipped, "removed": removed}

    try:
        _cache_clear("nlm_notebooks")
        return jsonify({"ok": True, **_run(_upload_all())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500


def _dedup_client(client, nb_id):
    async def _dedup():
        sources = await client.sources.list(nb_id)
        groups = {}
        order = []
        for s in sources:
            t = _safe_str(getattr(s, "title", "") or "بدون اسم").strip()
            if t not in groups:
                groups[t] = []
                order.append(t)
            groups[t].append(_safe_str(getattr(s, "id", "")))
        kept, removed = [], []
        for t in order:
            ids = groups[t]
            if len(ids) <= 1:
                continue
            for sid in ids[1:]:
                await client.sources.delete(nb_id, sid)
                removed.append({"id": sid, "title": t})
            kept.append({"id": ids[0], "title": t, "duplicates": len(ids) - 1})
        return {"removed": removed, "kept": kept}

    return _dedup()


@app.post("/api/nlm/notebooks/<nb_id>/sources/dedup")
def api_nlm_dedup_sources(nb_id):
    async def _dedup():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await _dedup_client(client, nb_id)

    try:
        _cache_clear("nlm_notebooks")
        return jsonify({"ok": True, **_run(_dedup())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500


# --- Sohbet ---
@app.get("/api/chat/notebooks")
def api_chat_notebooks():
    return api_nlm_notebooks()


@app.post("/api/chat/ask")
def api_chat_ask():
    body = request.get_json(silent=True) or {}
    nb_id = (body.get("nb_id") or "").strip()
    question = (body.get("question") or "").strip()
    conversation_id = (body.get("conversation_id") or "").strip() or None
    if not nb_id or not question:
        return jsonify({"ok": False, "error": "الدفتر والسؤال مطلوبان"}), 400

    async def _ask():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await client.chat.ask(nb_id, question, conversation_id=conversation_id)

    try:
        result = _run(_ask())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500

    refs = []
    for r in (getattr(result, "references", None) or []):
        refs.append({
            "source_id": _safe_str(getattr(r, "source_id", "")),
            "cited_text": _safe_str(getattr(r, "cited_text", "")),
            "title": _safe_str(getattr(r, "source_title", "") or getattr(r, "title", ""))
                     or _safe_str(getattr(r, "source_id", "")),
        })
    return jsonify({
        "ok": True,
        "answer": _safe_str(getattr(result, "answer", "")),
        "conversation_id": _safe_str(getattr(result, "conversation_id", "")),
        "references": refs,
    })


# --- fbread (browser-based Facebook scrape) ---
FB_SESSION_FILE = str(BASE_DIR / "facebook_session.json")


def _fbread_runner():
    from fbread_runner import get_runner
    return get_runner()


@app.get("/api/fbread/status")
def api_fbread_status():
    runner = _fbread_runner()
    job = runner.job
    return jsonify({
        "ok": True,
        "session_ok": os.path.exists(FB_SESSION_FILE),
        "job": job.as_dict() if job else None,
    })


@app.post("/api/fbread/run")
def api_fbread_run():
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "links").strip()
    params = dict(body.get("params") or {})

    if mode == "links":
        group_url = (params.get("group_url") or "").strip()
        gid = (params.get("group_id") or "").strip()
        if not gid:
            m = re.search(r"facebook\.com/groups/([^/?#]+)", group_url)
            if m:
                gid = m.group(1)
        if not gid:
            return jsonify({"ok": False, "error": "Grup ID zorunlu"}), 400
        params["group_id"] = gid
        params["group_url"] = group_url
        caps = [params.get("max_post"), params.get("max_scrolls"),
                params.get("max_duration_min")]
        if not any(c for c in caps):
            return jsonify({"ok": False, "error": "En az bir limit secilmeli"}), 400
    elif mode == "post":
        if not (params.get("post_url") or params.get("file")):
            return jsonify({"ok": False, "error": "URL veya dosya zorunlu"}), 400
    elif mode == "login":
        pass
    else:
        return jsonify({"ok": False, "error": f"Bilinmeyen mod: {mode}"}), 400

    try:
        job = _fbread_runner().start(mode, params)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "job": job.as_dict()})


@app.post("/api/fbread/stop")
def api_fbread_stop():
    runner = _fbread_runner()
    if runner.job is None:
        return jsonify({"ok": False, "error": "Aktif is yok"}), 409
    runner.stop_save()
    return jsonify({"ok": True, "job": runner.job.as_dict()})


@app.post("/api/fbread/clear")
def api_fbread_clear():
    _fbread_runner().clear()
    return jsonify({"ok": True})


@app.post("/api/fbread/login")
def api_fbread_login():
    try:
        job = _fbread_runner().start("login", {})
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "job": job.as_dict()})


@app.delete("/api/fbread/session")
def api_fbread_session_delete():
    try:
        if os.path.exists(FB_SESSION_FILE):
            os.remove(FB_SESSION_FILE)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/fbread/file/<fname>")
def api_fbread_file(fname):
    from fbread_runner import REPORT_DIR
    safe = os.path.basename(fname)
    p = os.path.join(REPORT_DIR, safe)
    if not os.path.exists(p):
        return jsonify({"ok": False, "error": "Dosya bulunamadi"}), 404
    if safe.endswith(".json"):
        with open(p, "r", encoding="utf-8") as f:
            return jsonify({"ok": True, "name": safe, "content": json.load(f)})
    return jsonify({"ok": True, "name": safe, "content": open(p, encoding="utf-8").read()})


if __name__ == "__main__":
    _port = int(os.getenv("SCNLM_PORT", "5000"))
    _debug = os.getenv("SCNLM_DEBUG", "1") == "1"
    print("scrapperNlm: http://127.0.0.1:{0} (debug={1})".format(_port, _debug))
    app.run(host="127.0.0.1", port=_port, debug=_debug)