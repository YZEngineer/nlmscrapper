import asyncio
import logging
import re
import sys
import threading
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from scraper.config import DATA_DIR


notebooklm_api = Blueprint("notebooklm_api", __name__)

_ALLOWED_UPLOAD_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".json",
    ".mp3",
    ".mp4",
    ".pdf",
    ".pptx",
    ".txt",
    ".xlsx",
}
_LOGIN_TIMEOUT_SECONDS = 900
_LOGIN_LOCK = threading.Lock()
_LOGIN_STATE = {"pending": False, "error": ""}
_LOGGER = logging.getLogger(__name__)
_TXT_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.txt", re.IGNORECASE)


def _run(coroutine):
    return asyncio.run(coroutine)


def _as_text(value, default=""):
    if value is None:
        return default
    return str(value)


def _log_error(action, error):
    current_app.logger.exception("NotebookLM %s failed: %s", action, error)


def _txt_directory():
    return Path(DATA_DIR, "txt").resolve()


def _safe_txt_path(filename):
    if (
        not isinstance(filename, str)
        or "/" in filename
        or "\\" in filename
        or not _TXT_NAME_PATTERN.fullmatch(filename)
    ):
        return None

    directory = _txt_directory()
    path = (directory / filename).resolve()
    if path.parent != directory or not path.is_file():
        return None
    return path


def _source_details(source):
    return {
        "id": _as_text(getattr(source, "id", "")),
        "title": _as_text(getattr(source, "title", None) or "Untitled"),
        "url": _as_text(getattr(source, "url", "")),
        "status": _as_text(getattr(source, "status", "")),
    }


@contextmanager
def _playwright_event_loop_policy():
    if sys.platform != "win32":
        yield
        return

    original_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(original_policy)


async def _validate_storage(path):
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage(path=str(path)) as client:
        await client.notebooks.list()


def _save_login_when_ready(app):
    candidate_path = None
    try:
        from notebooklm.paths import get_browser_profile_dir, get_storage_path
        from playwright.sync_api import sync_playwright

        storage_path = get_storage_path()
        browser_profile = get_browser_profile_dir()
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        browser_profile.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            prefix="notebooklm-auth-",
            suffix=".json",
            dir=storage_path.parent,
            delete=False,
        ) as candidate:
            candidate_path = Path(candidate.name)

        last_validation_error = None
        deadline = time.monotonic() + _LOGIN_TIMEOUT_SECONDS
        with _playwright_event_loop_policy():
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(browser_profile),
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--password-store=basic",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto("https://notebooklm.google.com", wait_until="domcontentloaded")
                    while time.monotonic() < deadline:
                        hostname = (urlsplit(page.url).hostname or "").lower()
                        if hostname == "notebooklm.google.com":
                            for url in (
                                "https://accounts.google.com",
                                "https://notebooklm.google.com",
                            ):
                                try:
                                    page.goto(url, wait_until="commit")
                                except Exception as error:
                                    if not any(
                                        marker in str(error).lower()
                                        for marker in (
                                            "navigation interrupted",
                                            "interrupted by another navigation",
                                        )
                                    ):
                                        raise
                            context.storage_state(path=str(candidate_path))
                            break
                        page.wait_for_timeout(2000)
                    else:
                        raise TimeoutError(
                            "NotebookLM login timed out before authentication completed."
                        )
                finally:
                    context.close()

        for _ in range(3):
            original_policy = None
            try:
                if sys.platform == "win32":
                    original_policy = asyncio.get_event_loop_policy()
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                asyncio.run(_validate_storage(candidate_path))
            except Exception as error:
                last_validation_error = error
                time.sleep(2)
            else:
                candidate_path.replace(storage_path)
                with _LOGIN_LOCK:
                    _LOGIN_STATE.update(pending=False, error="")
                _LOGGER.info("NotebookLM authentication saved to %s", storage_path)
                return
            finally:
                if original_policy is not None:
                    asyncio.set_event_loop_policy(original_policy)

        raise RuntimeError(
            f"NotebookLM authentication was not confirmed: {last_validation_error}"
        )
    except Exception as error:
        with _LOGIN_LOCK:
            _LOGIN_STATE.update(pending=False, error=str(error)[:500])
        app.logger.exception("NotebookLM automatic login failed: %s", error)
    finally:
        if candidate_path and candidate_path.exists():
            try:
                candidate_path.unlink()
            except OSError as error:
                app.logger.warning("Could not remove temporary NotebookLM auth file: %s", error)


async def _list_notebooks():
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        return await client.notebooks.list()


@notebooklm_api.get("/api/nlm/status")
def status():
    with _LOGIN_LOCK:
        login_state = dict(_LOGIN_STATE)
    if login_state["pending"]:
        return jsonify({
            "authenticated": False,
            "pending": True,
            "error": "Complete NotebookLM login in the browser. Authentication will be saved automatically.",
        })
    if login_state["error"]:
        return jsonify({
            "authenticated": False,
            "pending": False,
            "error": login_state["error"],
        })
    try:
        _run(_list_notebooks())
    except Exception as error:
        _log_error("authentication check", error)
        return jsonify({"authenticated": False, "error": str(error)[:500]})
    return jsonify({"authenticated": True, "error": ""})


@notebooklm_api.post("/api/nlm/login")
def login():
    with _LOGIN_LOCK:
        if _LOGIN_STATE["pending"]:
            return jsonify({
                "ok": True,
                "pending": True,
                "message": "NotebookLM login is already open. Authentication will be saved automatically.",
            })
        _LOGIN_STATE.update(pending=True, error="")

    try:
        threading.Thread(
            target=_save_login_when_ready,
            args=(current_app._get_current_object(),),
            daemon=True,
            name="notebooklm-login",
        ).start()
    except RuntimeError as error:
        with _LOGIN_LOCK:
            _LOGIN_STATE.update(pending=False, error=str(error)[:500])
        _log_error("login launch", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500
    return jsonify({
        "ok": True,
        "pending": True,
        "message": "NotebookLM login opened in the browser. Authentication will be saved automatically after login.",
    })


@notebooklm_api.get("/api/nlm/notebooks")
def notebooks():
    try:
        result = _run(_list_notebooks())
    except Exception as error:
        _log_error("notebook listing", error)
        return jsonify({"error": str(error)[:500]}), 500

    return jsonify({
        "notebooks": [
            {
                "id": _as_text(getattr(notebook, "id", "")),
                "title": _as_text(
                    getattr(notebook, "title", None)
                    or getattr(notebook, "name", None)
                    or "Untitled"
                ),
                "sources_count": getattr(notebook, "sources_count", None) or 0,
            }
            for notebook in result
        ]
    })


@notebooklm_api.post("/api/nlm/notebooks")
def create_notebook():
    body = request.get_json(silent=True)
    title = body.get("title") if isinstance(body, dict) else None
    if not isinstance(title, str) or not title.strip():
        return jsonify({"ok": False, "error": "Notebook title is required."}), 400
    title = title.strip()

    async def create():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            notebook = await client.notebooks.create(title)
            return {
                "id": _as_text(getattr(notebook, "id", "")),
                "title": _as_text(getattr(notebook, "title", None) or title),
            }

    try:
        return jsonify({"ok": True, "notebook": _run(create())})
    except Exception as error:
        _log_error("notebook creation", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500


@notebooklm_api.get("/api/nlm/notebooks/<nb_id>/sources")
def sources(nb_id):
    async def list_sources():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await client.sources.list(nb_id)

    try:
        result = _run(list_sources())
    except Exception as error:
        _log_error("source listing", error)
        return jsonify({"error": str(error)[:500]}), 500

    return jsonify({
        "sources": [
            {
                "id": _as_text(getattr(source, "id", "")),
                "title": _as_text(getattr(source, "title", None) or "Untitled"),
                "url": _as_text(getattr(source, "url", "")),
                "status": _as_text(getattr(source, "status", "")),
            }
            for source in result
        ]
    })


@notebooklm_api.get("/api/nlm/text-files")
def text_files():
    directory = _txt_directory()
    if not directory.is_dir():
        return jsonify({"files": []})

    try:
        files = []
        for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if not _TXT_NAME_PATTERN.fullmatch(entry.name):
                continue
            path = _safe_txt_path(entry.name)
            if path is None:
                continue
            stat = path.stat()
            files.append({
                "name": path.name,
                "size": stat.st_size,
            })
    except OSError as error:
        _log_error("TXT file listing", error)
        return jsonify({"error": str(error)[:500]}), 500

    return jsonify({"files": files})


@notebooklm_api.post("/api/nlm/notebooks/<nb_id>/sources/from-txt/<filename>")
def add_txt_source(nb_id, filename):
    path = _safe_txt_path(filename)
    if path is None:
        return jsonify({"ok": False, "error": "TXT file not found."}), 404

    async def add():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await client.sources.add_file(
                nb_id, str(path), wait=True, wait_timeout=180
            )

    try:
        source = _run(add())
    except Exception as error:
        _log_error("TXT source upload", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500

    return jsonify({"ok": True, "source": _source_details(source)})


@notebooklm_api.post("/api/nlm/notebooks/<nb_id>/sources/from-txt")
def add_all_txt_sources(nb_id):
    directory = _txt_directory()
    if not directory.is_dir():
        return jsonify({"ok": True, "uploaded": [], "skipped": [], "errors": []})

    try:
        paths = [
            path
            for path in (
                _safe_txt_path(entry.name)
                for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold())
                if _TXT_NAME_PATTERN.fullmatch(entry.name)
            )
            if path is not None
        ]
    except OSError as error:
        _log_error("TXT file listing for bulk upload", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500

    async def add_all():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            existing = await client.sources.list(nb_id)
            existing_titles = {
                _as_text(getattr(source, "title", "")).strip().casefold()
                for source in existing
                if _as_text(getattr(source, "title", "")).strip()
            }
            uploaded = []
            skipped = []
            errors = []
            for path in paths:
                filename = path.name
                if filename.casefold() in existing_titles:
                    skipped.append({"name": filename, "reason": "Already in notebook."})
                    continue
                try:
                    source = await client.sources.add_file(
                        nb_id, str(path), wait=True, wait_timeout=180
                    )
                except Exception as error:
                    current_app.logger.exception(
                        "NotebookLM TXT upload failed for %s: %s", filename, error
                    )
                    errors.append({"name": filename, "error": str(error)[:500]})
                    continue

                details = _source_details(source)
                uploaded.append(details)
                existing_titles.add(filename.casefold())

            return {"uploaded": uploaded, "skipped": skipped, "errors": errors}

    try:
        return jsonify({"ok": True, **_run(add_all())})
    except Exception as error:
        _log_error("bulk TXT upload", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500


@notebooklm_api.post("/api/nlm/notebooks/<nb_id>/sources")
def add_source(nb_id):
    source_type = (request.form.get("type") or "file").strip()
    wait_value = request.form.get("wait", "1")
    if source_type not in {"file", "url", "text"}:
        return jsonify({"ok": False, "error": "Unsupported source type."}), 400
    if wait_value not in {"0", "1"}:
        return jsonify({"ok": False, "error": "Wait state must be 0 or 1."}), 400
    wait = wait_value == "1"

    async def add_url(url):
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await client.sources.add_url(nb_id, url, wait=wait, wait_timeout=180)

    async def add_text(title, content):
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await client.sources.add_text(
                nb_id, title, content, wait=wait, wait_timeout=180
            )

    async def add_file(path):
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await client.sources.add_file(
                nb_id, path, wait=wait, wait_timeout=180
            )

    try:
        if source_type == "url":
            url = (request.form.get("url") or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "URL is required."}), 400
            source = _run(add_url(url))
        elif source_type == "text":
            title = (request.form.get("title") or "Pasted text").strip()
            content = (request.form.get("text") or "").strip()
            if not content:
                return jsonify({"ok": False, "error": "Text content is required."}), 400
            source = _run(add_text(title, content))
        else:
            uploaded = request.files.get("file")
            if not uploaded or not uploaded.filename:
                return jsonify({"ok": False, "error": "Choose a file to upload."}), 400
            filename = secure_filename(uploaded.filename)
            extension = Path(filename).suffix.lower()
            if not filename or extension not in _ALLOWED_UPLOAD_EXTENSIONS:
                return jsonify({"ok": False, "error": "Unsupported file type."}), 400
            with tempfile.TemporaryDirectory(prefix="notebooklm-upload-") as directory:
                path = Path(directory) / filename
                uploaded.save(path)
                source = _run(add_file(path))
    except Exception as error:
        _log_error("source upload", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500

    return jsonify({
        "ok": True,
        "source": {
            "id": _as_text(getattr(source, "id", "")),
            "title": _as_text(getattr(source, "title", None) or "Untitled"),
            "url": _as_text(getattr(source, "url", "")),
            "status": _as_text(getattr(source, "status", "")),
        },
    })


@notebooklm_api.delete("/api/nlm/notebooks/<nb_id>/sources/<source_id>")
def delete_source(nb_id, source_id):
    async def delete():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            await client.sources.delete(nb_id, source_id)

    try:
        _run(delete())
    except Exception as error:
        _log_error("source deletion", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500
    return jsonify({"ok": True})


@notebooklm_api.post("/api/nlm/notebooks/<nb_id>/sources/dedup")
def deduplicate_sources(nb_id):
    async def deduplicate():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            sources = await client.sources.list(nb_id)
            grouped = {}
            for source in sources:
                title = _as_text(getattr(source, "title", None) or "Untitled").strip()
                grouped.setdefault(title, []).append(_as_text(getattr(source, "id", "")))

            removed = []
            kept = []
            for title, source_ids in grouped.items():
                for source_id in source_ids[1:]:
                    await client.sources.delete(nb_id, source_id)
                    removed.append({"id": source_id, "title": title})
                if len(source_ids) > 1:
                    kept.append({
                        "id": source_ids[0],
                        "title": title,
                        "duplicates": len(source_ids) - 1,
                    })
            return {"removed": removed, "kept": kept}

    try:
        return jsonify({"ok": True, **_run(deduplicate())})
    except Exception as error:
        _log_error("source deduplication", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500


@notebooklm_api.post("/api/chat/ask")
def ask():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "A JSON object is required."}), 400
    nb_id = body.get("nb_id")
    question = body.get("question")
    if not isinstance(nb_id, str) or not nb_id.strip():
        return jsonify({"ok": False, "error": "Notebook and question are required."}), 400
    if not isinstance(question, str) or not question.strip():
        return jsonify({"ok": False, "error": "Notebook and question are required."}), 400
    conversation_id = body.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        return jsonify({"ok": False, "error": "Conversation ID must be text."}), 400
    if conversation_id is not None:
        conversation_id = conversation_id.strip() or None

    async def send_question():
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            return await client.chat.ask(
                nb_id.strip(),
                question.strip(),
                conversation_id=conversation_id,
            )

    try:
        result = _run(send_question())
    except Exception as error:
        _log_error("chat request", error)
        return jsonify({"ok": False, "error": str(error)[:500]}), 500

    references = []
    for reference in getattr(result, "references", None) or []:
        source_id = _as_text(getattr(reference, "source_id", ""))
        references.append({
            "source_id": source_id,
            "title": _as_text(
                getattr(reference, "source_title", None)
                or getattr(reference, "title", None)
                or source_id
            ),
            "cited_text": _as_text(getattr(reference, "cited_text", "")),
        })
    return jsonify({
        "ok": True,
        "answer": _as_text(getattr(result, "answer", "")),
        "conversation_id": _as_text(getattr(result, "conversation_id", "")),
        "references": references,
    })
