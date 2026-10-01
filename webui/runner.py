import json
import os
import sys
import threading
import traceback
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass

from scraper.config import SESSION_FILE, HEADLESS
from scraper.session import _wait_for_login
from scraper.group_feed import collect_post_links
from scraper.comments_collector import collect_comments_data
from playwright.sync_api import sync_playwright


class Job:
    def __init__(self, job_id, mode):
        self.id = job_id
        self.mode = mode
        self.status = "pending"
        self.logs = []
        self.result = None
        self.error = None
        self.started_at = time.monotonic()

    def log(self, msg):
        elapsed = int(time.monotonic() - self.started_at)
        line = f"[{elapsed // 60:02d}:{elapsed % 60:02d}] {msg}"
        self.logs.append(line)
        try:
            print(line, flush=True)
        except (UnicodeEncodeError, OSError):
            pass

    def as_dict(self):
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "logs": self.logs[-200:],
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    def __init__(self):
        self._job = None
        self._thread = None
        self._stop = threading.Event()
        self._active_page = None
        self._active_context = None
        self._active_lock = threading.Lock()

    @property
    def job(self):
        return self._job

    def is_running(self):
        return self._job is not None and self._job.status in (
            "pending", "running", "waiting_login", "stopping"
        )

    def start(self, mode, params):
        if mode not in ("group", "comments"):
            raise ValueError("Only Get URLs and Get Comments modes are available.")
        if self.is_running():
            raise RuntimeError("A job is already running. Wait for it to finish.")
        self._stop.clear()
        job = Job(self._new_id(), mode)
        self._job = job
        self._thread = threading.Thread(
            target=self._run,
            args=(job, params),
            daemon=True,
        )
        self._thread.start()
        return job

    def clear(self):
        self._job = None

    def stop_save(self):
        job = self._job
        if not job or not self.is_running():
            raise RuntimeError("There is no active job.")
        self._stop.set()
        job.status = "stopping"
        job.log("[stop] Stop requested; closing the active page.")
        with self._active_lock:
            page = self._active_page
        if page is not None:
            try:
                page.close()
                job.log("[stop] Active page closed.")
            except Exception as e:
                job.log(f"[stop] Page close warning: {e}")

    def _set_active_page(self, page):
        with self._active_lock:
            self._active_page = page

    def _clear_active_page(self, page=None):
        with self._active_lock:
            if page is None or self._active_page is page:
                self._active_page = None

    @staticmethod
    def _new_id():
        import uuid
        return uuid.uuid4().hex[:8]

    def _run(self, job, params):
        try:
            from scraper.session import _session_has_login

            playwright = sync_playwright().start()
            browser = None
            try:
                job.log("[start] Launching Chrome...")
                browser = playwright.chromium.launch(headless=HEADLESS)
                if os.path.exists(SESSION_FILE):
                    context = browser.new_context(storage_state=SESSION_FILE)
                else:
                    context = browser.new_context()
                page = context.new_page()
                self._active_context = context
                self._set_active_page(page)

                if not _session_has_login():
                    job.status = "waiting_login"
                    job.log("[login] No session found. Log in to Facebook in Chrome.")
                    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
                    if not _wait_for_login(page, context):
                        job.status = "error"
                        job.error = "Facebook login timed out."
                        job.log("[error] Login was not completed.")
                        return
                    context.storage_state(path=SESSION_FILE)
                    job.log("[ok] Session saved: " + SESSION_FILE)

                job.status = "running"
                if job.mode == "group":
                    self._run_group_urls(job, page, params)
                else:
                    self._run_comments(job, context, params)
                try:
                    context.storage_state(path=SESSION_FILE)
                except Exception:
                    pass

                if self._stop.is_set():
                    job.status = "done"
                    job.log("[save] Completed data from the stopped job was saved.")
                elif job.status != "error":
                    job.status = "done"
                    job.log("[done] Job finished.")
            finally:
                self._clear_active_page()
                self._active_context = None
                if browser:
                    browser.close()
                playwright.stop()
        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.log("[error] " + str(e))
            traceback.print_exc()

    def _run_comments(self, job, context, params):
        urls = params["urls"]
        job.log(
            f"[comments] Processing {len(urls)} post URLs; validating group and post IDs."
        )
        posts = []
        errors = []
        filename = params.get("output_name") or (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_comments.json"
        )
        path = os.path.join("data", filename)
        os.makedirs("data", exist_ok=True)

        for index, url in enumerate(urls, 1):
            if self._stop.is_set():
                job.log(f"[comments {index}/{len(urls)}] Stop requested; remaining URLs skipped.")
                break
            item_page = None
            job.log(f"[comments {index}/{len(urls)}] Starting: {url}")
            try:
                item_page = context.new_page()
                self._set_active_page(item_page)
                data = collect_comments_data(
                    item_page,
                    url,
                    max_scrolls=1000,
                    idle_seconds=5,
                )
                if self._stop.is_set():
                    job.log(
                        f"[comments {index}/{len(urls)}] Active post was not saved because stop was requested."
                    )
                    break
                posts.append(data)
                job.log(
                    f"[comments {index}/{len(urls)}] Verified target post; "
                    f"saved {len(data['comments'])} comment records."
                )
            except Exception as e:
                if self._stop.is_set():
                    job.log(f"[comments {index}/{len(urls)}] Active post was interrupted by stop.")
                    break
                errors.append({"url": url, "error": str(e)})
                job.log(f"[comments {index}/{len(urls)}] Error: {e}")
            finally:
                self._clear_active_page(item_page)
                if item_page:
                    try:
                        item_page.close()
                    except Exception as e:
                        job.log(f"[comments {index}/{len(urls)}] Page close error: {e}")
            self._write_result(path, urls, posts, errors, partial=self._stop.is_set())

        self._write_result(path, urls, posts, errors, partial=self._stop.is_set())
        job.result = {
            "path": path,
            "count": len(posts),
            "total": len(urls),
            "errors": len(errors),
            "stopped": self._stop.is_set(),
        }
        job.log(f"[comments] List finished: {len(posts)}/{len(urls)} successful.")
        job.log(f"[ok] Saved verified comments: {path}")
        job.log(f"[ok] Saved text copy: {os.path.join('data', 'txt', os.path.splitext(os.path.basename(path))[0] + '.txt')}")

    def _run_group_urls(self, job, page, params):
        group_id = params["group_id"]
        limit_enabled = params.get("limit_enabled", True)
        limit = int(params.get("limit") or 10) if limit_enabled else None
        if limit_enabled:
            job.log(f"[group] Collecting up to {limit} post URLs from group {group_id}.")
        else:
            job.log(f"[group] Collecting post URLs from group {group_id} until the feed ends or stopped.")
        urls = collect_post_links(
            page,
            group_id,
            limit=limit,
            log=job.log,
            should_stop=self._stop.is_set,
        )
        if self._stop.is_set():
            job.log("[group] Collection stopped by user.")
        filename = params.get("output_name") or (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_urls.json"
        )
        path = os.path.join("data", filename)
        os.makedirs("data", exist_ok=True)
        payload = {
            "schema_version": 1,
            "scraped_at": datetime.now().isoformat(),
            "group_id": group_id,
            "urls": urls,
            "partial": bool(self._stop.is_set()),
            "stopped": bool(self._stop.is_set()),
        }
        with open(path, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
        job.result = {
            "path": path,
            "count": len(urls),
            "total": limit if limit is not None else len(urls),
            "errors": 0,
            "stopped": self._stop.is_set(),
        }
        job.log(f"[group] Collected {len(urls)} post URLs.")
        job.log(f"[ok] Saved: {path}")

    @staticmethod
    def _write_result(path, urls, posts, errors, partial):
        payload = {
            "schema_version": 1,
            "scraped_at": datetime.now().isoformat(),
            "urls": urls,
            "posts": posts,
            "errors": errors,
            "partial": bool(partial),
            "stopped": bool(partial),
        }
        document = json.dumps(payload, ensure_ascii=False, indent=2)
        with open(path, "w", encoding="utf-8") as output:
            output.write(document)
        text_path = os.path.join(
            os.path.dirname(path), "txt", os.path.splitext(os.path.basename(path))[0] + ".txt"
        )
        os.makedirs(os.path.dirname(text_path), exist_ok=True)
        with open(text_path, "w", encoding="utf-8") as output:
            output.write(document)


_runner = JobRunner()


def get_runner():
    return _runner
