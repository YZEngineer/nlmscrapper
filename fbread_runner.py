import json
import os
import re
import threading
import queue
import time
import traceback
import uuid
from datetime import datetime

from scraper.config import SESSION_FILE, HEADLESS
from scraper.session import _wait_for_login, _session_has_login
from scraper.group_feed import collect_post_links, collect_post_links_dated, summarize_links
from scraper.post_comments import collect_post_data, collect_post_data_retry
from playwright.sync_api import sync_playwright

_BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(_BASE, "data", "json")
os.makedirs(REPORT_DIR, exist_ok=True)


class Job:
    def __init__(self, job_id, mode):
        self.id = job_id
        self.mode = mode
        self.status = "pending"          # pending | running | waiting_login | done | error | stopping
        self.logs = []
        self.result = None
        self.error = None
        self.link_count = 0
        self.scroll_count = 0
        self.started_at = time.monotonic()

    def log(self, msg):
        elapsed = time.monotonic() - self.started_at
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        line = f"[l{self.link_count}][s{self.scroll_count}][{h:02d}:{m:02d}:{s:02d}] {msg}"
        self.logs.append(line)
        print(line, flush=True)

    def as_dict(self):
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "logs": self.logs[-200:],
            "result": self.result,
            "error": self.error,
        }


class FbreadRunner:
    def __init__(self):
        self._job = None
        self._thread = None
        self._log_queue = queue.Queue()
        self._stop_requested = threading.Event()

    @property
    def job(self):
        return self._job

    def is_running(self):
        return self._job is not None and self._job.status in (
            "pending", "running", "waiting_login", "stopping")

    def start(self, mode, params):
        if self.is_running():
            raise RuntimeError("Zaten aktif bir is var. Once tamamlanmasini bekle.")
        self._stop_requested.clear()
        job = Job(self._new_id(), mode)
        self._job = job
        self._thread = threading.Thread(target=self._run, args=(job, mode, params), daemon=True)
        self._thread.start()
        return job

    def stop_save(self):
        self._stop_requested.set()
        if self._job is not None and self._job.status not in ("done", "error"):
            self._job.status = "stopping"

    def clear(self):
        self._job = None

    def _new_id(self):
        return uuid.uuid4().hex[:8]

    # ----- internal -----

    def _run(self, job, mode, params):
        try:
            p = sync_playwright().start()
            browser = None
            try:
                job.log("[baslat] Chrome aciliyor...")
                browser = p.chromium.launch(headless=HEADLESS)
                if os.path.exists(SESSION_FILE):
                    context = browser.new_context(storage_state=SESSION_FILE)
                else:
                    context = browser.new_context()
                page = context.new_page()

                if not _session_has_login():
                    job.status = "waiting_login"
                    job.log("[login] Oturum yok. Chrome'da Facebook'a giris yap.")
                    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
                    ok = _wait_for_login(page, context, stop_event=self._stop_requested)
                    if self._stop_requested.is_set():
                        job.status = "done"
                        job.log("[kaydet] Kapatma istegi; giris beklemeden cikildi.")
                        return
                    if not ok:
                        job.status = "error"
                        job.error = "Facebook giris suresi asildi."
                        job.log("[hata] Giris yapilmadi.")
                        return
                    context.storage_state(path=SESSION_FILE)
                    job.log("[ok] Oturum kaydedildi: " + SESSION_FILE)

                job.status = "running"

                if mode == "login":
                    job.log("[ok] Facebook oturumu hazir.")
                elif mode == "post":
                    self._run_post(job, page, params)
                elif mode == "group":
                    self._run_group(job, page, params)
                elif mode == "links":
                    self._run_links(job, page, params)
                else:
                    job.status = "error"
                    job.error = f"Bilinmeyen mod: {mode}"

                try:
                    context.storage_state(path=SESSION_FILE)
                except Exception:
                    pass

                if job.status != "error":
                    job.status = "done"
                    job.log("[tamam] Is bitti.")
            finally:
                if browser:
                    browser.close()
                p.stop()
        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.log("[hata] " + str(e))
            traceback.print_exc()

    def _write_report(self, job, gid, merged, group_url="", partial=False, error=""):
        posts = []
        for url, d in (merged or {}).items():
            blocks = d.get("blocks") or []
            posts.append({
                "id": "",
                "url": url,
                "text": d.get("post_text") or "",
                "date": "",
                "comment_count": len(blocks),
                "comments": [
                    {"author": "", "text": b, "date": "", "reactions": "", "replies": []}
                    for b in blocks
                ],
            })
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        gi = re.sub(r'[\\/*?:"<>|]+', "", str(gid) or "grup").replace(" ", "_") or "grup"
        oname = f"scrape_{gi}_{stamp}.json"
        path = os.path.join(REPORT_DIR, oname)
        payload = {
            "group_url": group_url or f"https://www.facebook.com/groups/{gid}",
            "group_name": str(gid),
            "source": "browser",
            "fetched_at": datetime.now().isoformat(),
            "partial": bool(partial),
            "error": error or None,
            "posts": posts,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        job.log(f"[ok] Rapor: {path}")
        return path

    def _run_post(self, job, page, params):
        fname = params.get("file")
        if fname:
            self._run_post_file(job, page, fname,
                                skip_existing=bool(params.get("skip_existing", False)))
            return
        url = params["post_url"]
        job.log(f"[post] URL: {url}")
        try:
            data = collect_post_data(page, url, stop_event=self._stop_requested)
        except Exception as e:
            job.result = {"path": None, "blocks": 0, "error": str(e)}
            job.log(f"[hata] {e}")
            return
        n = len(data.get("blocks", []))
        job.log(f"[post] {n} blok toplandi.")
        if (data.get("expand") or {}).get("capped"):
            job.log("[uyari] Yorum acma güvenlik tavani asildi; bloklar eksik olabilir.")
        if data.get("error") == "unavailable" or n == 0:
            job.result = {"path": None, "blocks": n, "error": data.get("error") or "bos"}
            job.log("[uyari] Post mevcut degil ya da icerik yok.")
            return

        path = os.path.join(REPORT_DIR, f"post_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        blocks = data.get("blocks") or []
        payload = {
            "group_url": url,
            "group_name": "",
            "source": "browser_post",
            "fetched_at": datetime.now().isoformat(),
            "partial": False,
            "error": data.get("error") or None,
            "posts": [{
                "id": "",
                "url": url,
                "text": data.get("post_text") or "",
                "date": "",
                "comment_count": len(blocks),
                "comments": [
                    {"author": "", "text": b, "date": "", "reactions": "", "replies": []}
                    for b in blocks
                ],
            }],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        job.result = {"path": path, "blocks": n}
        job.log(f"[ok] Kaydedildi: {path}")

    def _run_post_file(self, job, page, fname, skip_existing=False):
        safe = os.path.basename(fname)
        path = os.path.join(REPORT_DIR, safe)
        if not os.path.exists(path):
            job.status = "error"
            job.error = f"Dosya bulunamadi: {safe}"
            job.log("[hata] Dosya bulunamadi: " + safe)
            return

        base_name = safe.lower()
        base_name = base_name.replace(".json", "").replace(".txt", "")
        base_name = re.sub(r"_\d{8}_\d{6}$", "", base_name)
        for suffix in ("_urls", "_links", "_posts", "_comments"):
            if base_name.endswith(suffix):
                base_name = base_name[:-len(suffix)]
        base_name = base_name or "post"
        cname = f"{base_name}_comments.json"

        group_url = ""
        if safe.endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                group_url = str(data.get("group_url") or "")
            urls = [l.get("url") for l in data.get("links", []) if l.get("url")]
            if not urls and isinstance(data, dict):
                for p in data.get("posts", []) or []:
                    if isinstance(p, dict) and p.get("url"):
                        urls.append(p["url"])
            if not urls and isinstance(data, dict):
                groups = data.get("groups")
                if isinstance(groups, dict):
                    for gid, lst in groups.items():
                        if isinstance(lst, list):
                            urls.extend([u for u in lst if isinstance(u, str) and u])
        else:
            urls = [ln.strip() for ln in open(path, encoding="utf-8").read().splitlines()
                    if ln.strip() and ln.strip().lower().startswith("http")]
        if not urls:
            job.status = "error"
            job.error = f"Dosyada URL yok: {safe}"
            job.log("[hata] Dosyada URL yok: " + safe)
            return
        m = re.search(r"groups/(\d+)", group_url or "")
        group_id = m.group(1) if m else (safe.split("_")[0] or "")
        cpath = os.path.join(REPORT_DIR, cname)
        merged = self._load_comment_map(cpath)
        start_i = None
        if skip_existing and merged:
            hits = [i for i, u in enumerate(urls) if u in merged]
            if hits:
                start_i = max(hits)
                job.log(f"[gec] {len(hits)} URL onceki cekimde var; "
                        f"son cekilen ({start_i + 1}. post) dahil devam edilecek.")
        job.log(f"[dosya] {len(urls)} URL yuklendi: {safe} -> {cname}")
        skipped = 0
        for i, url in enumerate(urls, 1):
            if self._stop_requested.is_set():
                job.log("[kaydet] Kapatma istegi; post cekme durduruldu.")
                break
            if start_i is not None and i - 1 < start_i:
                skipped += 1
                job.log(f"  [{i}/{len(urls)}] {url} [gecildi: yorum zaten var]")
                continue
            job.log(f"  [{i}/{len(urls)}] {url}")
            try:
                data = collect_post_data_retry(page, url, stop_event=self._stop_requested)
            except Exception as e:
                job.log(f"    [hata] {e}")
                continue
            n = len(data.get("blocks", []))
            if (data.get("expand") or {}).get("capped"):
                job.log("    [uyari] acma tavani asildi; yorumlar eksik olabilir")
            if data.get("error") == "unavailable" or n == 0:
                skipped += 1
                job.log("    [atladi] mevcut degil / 0 blok")
                continue
            merged[url] = {
                "post_text": data.get("post_text") or "",
                "blocks": data.get("blocks") or [],
            }
            job.log(f"    [ok] {n} blok")
            self._save_comments_report(cpath, group_id, merged, group_url)
        if not merged:
            job.log("[uyari] Yorum toplanamadi.")
            job.result = {"path": None, "count": 0, "urls": len(urls), "skipped": skipped}
            if self._stop_requested.is_set():
                job.result["stopped"] = True
            return
        stopped = self._stop_requested.is_set()
        self._save_comments_report(cpath, group_id or base_name, merged, group_url,
                                   partial=stopped)
        job.result = {"path": cpath, "comments_path": cpath,
                      "count": len(merged), "urls": len(urls), "skipped": skipped}
        if stopped:
            job.result["stopped"] = True
            job.log("[kaydet] Is kapatildi; kismi veri kaydedildi.")
        job.log(f"[ok] Kaydedildi: {cpath}")

    @staticmethod
    def _group_name_for_gid(gid):
        f = os.path.join(_BASE, "data", "groups.json")
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return ""
        if not isinstance(data, list):
            return ""
        target = str(gid or "")
        if not target:
            return ""
        for g in data:
            if str(g.get("id") or "") == target:
                return str(g.get("name") or "")
            m = re.search(r"(\d+)$", re.sub(r"/+$", "", str(g.get("link") or "")))
            if m and m.group(1) == target:
                return str(g.get("name") or "")
        return ""

    def _sanitize_name(self, raw):
        name = re.sub(r"[^\w-]+", "_", (raw or "").replace(" ", "_"))
        return name.strip("_")

    def _uniq_base(self, base, stamp):
        for suffix in ("_posts.json", "_comments.json", "_links.json", "_urls.txt"):
            if os.path.exists(os.path.join(REPORT_DIR, f"{base}{suffix}")):
                return f"{base}_{stamp}"
        return base

    def _run_links(self, job, page, params):
        gid = params["group_id"]
        group_url = params.get("group_url") or ""
        max_post = params.get("max_post")
        max_scrolls = params.get("max_scrolls")
        duration_min = params.get("max_duration_min")
        with_comments = bool(params.get("comments"))
        base_name = params.get("base_name") or None
        if base_name:
            base_name = self._sanitize_name(base_name) or None

        if max_post is None and max_scrolls is None and not duration_min:
            job.log("[uyari] Tum limitler kapali; max_scrolls=60 guvenlik cap'i uygulanacak.")
            max_scrolls = 60

        deadline = None
        if duration_min:
            deadline = time.monotonic() + duration_min * 60

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = None
        if base_name:
            base = self._uniq_base(base_name, stamp)
            pjson = f"{base}_posts.json"
        else:
            auto = self._sanitize_name(self._group_name_for_gid(gid)) or str(gid)
            base = self._uniq_base(auto, stamp)
            pjson = f"{base}_posts.json"

        job.log(f"[links] Grup: {gid} | max_post: {max_post}"
                f" | max_scrolls: {max_scrolls} | max_duration: {duration_min or 'yok'}dk"
                f" | yorum: {'acik' if with_comments else 'kapali'}")

        def on_link(item, n):
            job.link_count = n
            job.log(f"[link] {item['url']}")

        def on_scroll(scroll_count, current):
            job.scroll_count = scroll_count
            job.log(f"[scroll] toplam {len(current)} link")
            try:
                self._write_links_files(gid, pjson, current)
            except Exception as e:
                job.log(f"[uyari] Asamali kayit hatasi: {e}")

        rem = None if deadline is None else max(0.0, deadline - time.monotonic())
        dated, scroll_count = collect_post_links_dated(
            page, gid,
            max_post=max_post,
            max_scrolls=max_scrolls,
            max_duration=rem,
            on_link=on_link,
            on_scroll=on_scroll,
            stop_event=self._stop_requested,
        )
        if not dated:
            job.log("[uyari] Post linki bulunamadi.")
            job.result = {"path": None, "count": 0, "scrolls": scroll_count}
            return

        summary = summarize_links(dated)
        summary["scrolls"] = scroll_count
        path = self._write_links_files(gid, pjson, dated,
                                       group_url=group_url,
                                       partial=self._stop_requested.is_set())

        job.result = {"path": path, "group_id": gid, **summary}
        job.log(f"[ok] {len(dated)} link -> {path}")
        job.log(f"[ozet] sayi: {summary['count']} | scroll: {scroll_count}")
        job.log(f"       ilk: {summary['first_post']}")
        job.log(f"       son: {summary['last_post']}")

        if self._stop_requested.is_set():
            job.result["stopped"] = True
            job.log("[kaydet] Kapatma istegi; linkler kaydedildi. Yorum asamasina gecilmiyor.")
            return

        if with_comments:
            self._collect_comments(job, page, dated, gid, base or gid, group_url)

    def _write_links_files(self, gid, pjson, links, group_url="", partial=False):
        summary = summarize_links(links)
        path = os.path.join(REPORT_DIR, pjson)
        payload = {
            "group_id": gid,
            "group_url": group_url or f"https://www.facebook.com/groups/{gid}",
            "group_name": self._group_name_for_gid(gid) or str(gid),
            "source": "browser_links",
            "fetched_at": datetime.now().isoformat(),
            "partial": bool(partial),
            "error": None,
            "count": summary.get("count"),
            "first_post": summary.get("first_post"),
            "last_post": summary.get("last_post"),
            "scrolls": summary.get("scrolls"),
            "links": [
                {"url": l.get("url"), "date": l.get("date") or "", "post_text": l.get("post_text") or ""}
                for l in links if l.get("url")
            ],
            "posts": [{
                "id": "",
                "url": l.get("url"),
                "text": l.get("post_text") or "",
                "date": l.get("date") or "",
                "comment_count": 0,
                "comments": [],
            } for l in links if l.get("url")],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def _load_comment_map(cpath):
        if not os.path.exists(cpath):
            return {}
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}
        out = {}
        posts = data.get("posts") if isinstance(data, dict) else None
        if isinstance(posts, list):
            for p in posts:
                if not isinstance(p, dict):
                    continue
                url = p.get("url") or p.get("post_url")
                if not url:
                    continue
                comments = p.get("comments")
                if isinstance(comments, list):
                    blocks = [c.get("text") for c in comments if isinstance(c, dict) and c.get("text")]
                else:
                    blocks = p.get("blocks") or []
                out[url] = {
                    "post_text": p.get("text") or p.get("post_text") or "",
                    "blocks": blocks or [],
                }
        return out

    @staticmethod
    def _save_comments_report(cpath, gid, merged, group_url="", partial=False):
        posts = []
        for url, d in merged.items():
            blocks = d.get("blocks") or []
            posts.append({
                "id": "",
                "url": url,
                "text": d.get("post_text") or "",
                "date": "",
                "comment_count": len(blocks),
                "comments": [
                    {"author": "", "text": b, "date": "", "reactions": "", "replies": []}
                    for b in blocks
                ],
            })
        payload = {
            "group_id": gid,
            "group_url": group_url or f"https://www.facebook.com/groups/{gid}",
            "group_name": FbreadRunner._group_name_for_gid(gid) or str(gid),
            "source": "browser_comments",
            "fetched_at": datetime.now().isoformat(),
            "partial": bool(partial),
            "error": None,
            "posts": posts,
        }
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return cpath

    def _collect_comments(self, job, page, dated, gid, base, group_url):
        cname = f"{base}_comments.json"
        cpath = os.path.join(REPORT_DIR, cname)
        merged = self._load_comment_map(cpath)
        skipped = 0
        total = len(dated)
        stopper = self._stop_requested
        for i, item in enumerate(dated, 1):
            if stopper.is_set():
                job.log("[kaydet] Kapatma istegi; yorum toplama durduruldu.")
                break
            if item["url"] in merged:
                job.log(f"  [yorum {i}/{total}] {item['url']} [atlandi: zaten var]")
                continue
            job.log(f"  [yorum {i}/{total}] {item['url']}")
            try:
                data = collect_post_data_retry(page, item["url"], stop_event=self._stop_requested)
            except Exception as e:
                job.log(f"    [hata] {e}")
                continue
            n = len(data.get("blocks", []))
            if (data.get("expand") or {}).get("capped"):
                job.log("    [uyari] güvenlik tavani asildi; yorumlar eksik olabilir")
            if data.get("error") == "unavailable" or n == 0:
                skipped += 1
                job.log("    [atladi] mevcut degil / 0 blok")
                continue
            merged[item["url"]] = {
                "post_text": data.get("post_text") or "",
                "blocks": data.get("blocks") or [],
            }
            job.log(f"    [ok] {n} blok")
            self._save_comments_report(cpath, gid, merged, group_url)
        if not merged:
            job.log("[uyari] Yorum toplanamadi.")
            job.result["comments_path"] = None
            job.result["comments_count"] = 0
            job.result["comments_skipped"] = skipped
            if stopper.is_set():
                job.result["stopped"] = True
            return
        stopped = stopper.is_set()
        self._save_comments_report(cpath, gid, merged, group_url, partial=stopped)
        job.result["comments_path"] = cpath
        job.result["comments_count"] = len(merged)
        job.result["comments_skipped"] = skipped
        if stopped:
            job.result["stopped"] = True
            job.log("[kaydet] Is kapatildi; kismi yorum verisi kaydedildi.")
        job.log(f"[ok] Yorumlar ({len(merged)} post) -> {cpath}")

    def _run_group(self, job, page, params):
        gid = params["group_id"]
        limit = params["limit"]
        job.log(f"[grup] ID: {gid} | limit: {limit}")
        links = collect_post_links(page, gid, limit=limit)
        job.log(f"[grup] {len(links)} post linki bulundu.")
        if not links:
            job.log("[uyari] Post linki bulunamadi.")
            job.result = {"path": None, "count": 0, "links": links}
            return

        merged = {}
        for i, url in enumerate(links, 1):
            job.log(f"  [{i}/{len(links)}] {url}")
            try:
                data = collect_post_data(page, url, stop_event=self._stop_requested)
                blocks = data.get("blocks") or []
                merged[url] = {
                    "post_text": data.get("post_text") or "",
                    "blocks": blocks,
                }
                job.log(f"    [ok] {len(blocks)} blok")
            except Exception as e:
                job.log(f"    [hata] {e}")

        job.result = {"count": len(merged), "links": links}
        if not merged:
            job.result["path"] = None
            job.log("[uyari] Yorum toplanamadi.")
            return
        path = self._write_report(job, gid, merged,
                                  partial=self._stop_requested.is_set())
        job.result["path"] = path
        job.log(f"[ok] Kaydedildi: {path}")


_runner = FbreadRunner()


def get_runner():
    return _runner


def _migrate_pw():
    """Eski data/pw dosyalarini report-semasiyla data/json'a tasir, pw dizinini siler."""
    pw = os.path.join(_BASE, "data", "pw")
    if not os.path.isdir(pw):
        return
    migrated = 0
    for name in os.listdir(pw):
        p = os.path.join(pw, name)
        if not os.path.isfile(p) or not name.lower().endswith(".json"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        posts = data.get("posts")
        if not isinstance(posts, list):
            links = data.get("links")
            if isinstance(links, list):
                posts = [{
                    "id": "",
                    "url": l.get("url"),
                    "text": l.get("post_text") or "",
                    "date": l.get("date") or "",
                    "comment_count": 0,
                    "comments": [],
                } for l in links if l.get("url")]
        if not posts:
            continue
        out = os.path.join(REPORT_DIR, name)
        n = 0
        while os.path.exists(out):
            n += 1
            out = os.path.join(REPORT_DIR, name[:-5] + f"_migrated{n}.json")
        payload = {
            "group_id": data.get("group_id") or "",
            "group_url": data.get("group_url") or "",
            "group_name": str(data.get("group_id") or ""),
            "source": data.get("source") or "browser_links",
            "fetched_at": (data.get("fetched_at") or data.get("scraped_at")
                           or datetime.now().isoformat()),
            "partial": bool(data.get("partial")),
            "error": data.get("error"),
            "links": data.get("links"),
            "posts": posts,
        }
        try:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"[fbread] Migration: data/pw/{name} -> {os.path.relpath(out, _BASE)}")
            os.remove(p)
            migrated += 1
        except Exception as e:
            print(f"[fbread] Migration error ({name}): {e}")
    try:
        if not os.listdir(pw):
            os.rmdir(pw)
    except Exception:
        pass
    if migrated:
        print(f"[fbread] Migrated {migrated} file(s) to data/json.")


_migrate_pw()