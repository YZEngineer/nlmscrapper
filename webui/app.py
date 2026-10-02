import json
import os
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, render_template, request, send_file, abort

from scraper.config import DATA_DIR, SESSION_FILE
from scraper.session import _session_has_login
from webui.runner import get_runner

app = Flask(__name__)


def _safe_data_path(filename):
    safe = os.path.basename(str(filename or ""))
    if not safe or not safe.lower().endswith(".json") or safe in (".", ".."):
        return None
    data_root = os.path.realpath(DATA_DIR)
    path = os.path.realpath(os.path.join(data_root, safe))
    if os.path.dirname(path) != data_root:
        return None
    return path


def _is_group_post_url(value):
    parsed = urlsplit(str(value or "").strip())
    if (parsed.hostname or "").lower() not in ("facebook.com", "www.facebook.com", "m.facebook.com"):
        return False
    return bool(
        re.fullmatch(
            r"/groups/[^/]+/(?:posts|permalink)/[^/]+/?",
            parsed.path,
            re.IGNORECASE,
        )
    )


def _normalize_group_input(value):
    value = str(value or "").strip()
    if not value:
        return None
    if "://" in value or value.lower().startswith(("www.", "facebook.com", "m.facebook.com")):
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.hostname or "").lower() not in (
            "facebook.com", "www.facebook.com", "m.facebook.com"
        ):
            return None
        match = re.match(r"^/groups/([^/?#]+)(?:/|$)", parsed.path, re.IGNORECASE)
        return match.group(1) if match else None
    if re.fullmatch(r"[A-Za-z0-9._-]+", value):
        return value
    return None


def _normal_output_name(value):
    raw = os.path.basename(str(value or "").strip())
    if not raw:
        return None
    if not raw.lower().endswith(".json"):
        raw += ".json"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json", raw):
        return None
    return raw


def _append_output_suffix(filename, suffix):
    stem, extension = os.path.splitext(filename)
    if not stem.lower().endswith(suffix):
        stem += suffix
    return stem + extension


def _urls_from_json(value):
    urls = []
    seen = set()

    def add(url):
        if not isinstance(url, str):
            return
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    if isinstance(value, dict):
        for url in value.get("urls", []) or []:
            add(url)
        for key in ("links", "posts"):
            for item in value.get(key, []) or []:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict):
                    add(item.get("url") or item.get("post_url"))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                add(item.get("url") or item.get("post_url"))
    return urls


def _list_results():
    if not os.path.isdir(DATA_DIR):
        return []
    files = []
    for name in sorted(os.listdir(DATA_DIR)):
        if name.endswith(".json"):
            path = os.path.join(DATA_DIR, name)
            files.append({
                "name": name,
                "size": os.path.getsize(path),
                "url": "/api/result/" + name,
                "view_url": "/view/" + name,
                "rename_url": "/api/files/" + name,
                "delete_url": "/api/files/" + name,
            })
    return files


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    return jsonify({
        "session": _session_has_login(),
        "files": _list_results(),
        "job": get_runner().job.as_dict() if get_runner().job else None,
    })


@app.route("/api/run", methods=["POST"])
def run():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("mode") not in ("group", "comments"):
        return jsonify({"error": "Only Get URLs and Get Comments modes are available."}), 400

    mode = body["mode"]
    params = body.get("params")
    if not isinstance(params, dict):
        return jsonify({"error": "Job parameters must be an object."}), 400

    if mode == "group":
        group_value = params.get("group_input")
        if group_value is None:
            group_value = params.get("group_id") or params.get("group_url")
        group_id = _normalize_group_input(group_value)
        if not group_id:
            return jsonify({"error": "Enter a valid Facebook group ID or group URL."}), 400
        params["group_id"] = group_id
        limit_enabled = params.get("limit_enabled", True)
        if not isinstance(limit_enabled, bool):
            return jsonify({"error": "Post limit active state must be true or false."}), 400
        params["limit_enabled"] = limit_enabled
        try:
            params["limit"] = int(params.get("limit") or 10) if limit_enabled else None
        except (TypeError, ValueError):
            return jsonify({"error": "Post limit must be a number."}), 400
        if limit_enabled and (params["limit"] < 1 or params["limit"] > 100000):
            return jsonify({"error": "Post limit must be between 1 and 100000."}), 400
    else:
        url = str(params.get("post_url") or "").strip()
        urls = params.get("urls") if isinstance(params.get("urls"), list) else []
        source_file = params.get("source_file")
        if source_file:
            source_path = _safe_data_path(source_file)
            if not source_path or not os.path.isfile(source_path):
                return jsonify({"error": "Source JSON file not found"}), 400
            try:
                with open(source_path, "r", encoding="utf-8") as source:
                    urls = _urls_from_json(json.load(source))
            except (OSError, ValueError):
                return jsonify({"error": "Source JSON could not be read"}), 400
        if url:
            urls = [url]
        if not urls or not any(str(item).strip() for item in urls):
            return jsonify({"error": "At least one post URL or a JSON source file is required."}), 400
        params["urls"] = [str(item).strip() for item in urls if str(item).strip()]
        invalid = [item for item in params["urls"] if not _is_group_post_url(item)]
        if invalid:
            return jsonify({
                "error": "Get Comments requires Facebook group post URLs containing both group and post IDs."
            }), 400

    output_name = params.get("output_name")
    if output_name:
        normalized = _normal_output_name(output_name)
        if not normalized:
            return jsonify({"error": "Invalid output filename"}), 400
        normalized = _append_output_suffix(
            normalized,
            "_urls" if mode == "group" else "_comments",
        )
        existing_output = os.path.exists(os.path.join(DATA_DIR, normalized))
        if existing_output:
            return jsonify({"error": "Destination file already exists"}), 409
        params["output_name"] = normalized

    runner = get_runner()
    if runner.is_running():
        return jsonify({"error": "A job is already running."}), 409

    job = runner.start(mode, params)
    return jsonify({"ok": True, "job": job.as_dict()})


@app.route("/api/job")
def job():
    runner = get_runner()
    return jsonify(runner.job.as_dict() if runner.job else None)


@app.route("/api/clear", methods=["POST"])
def clear():
    get_runner().clear()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def stop():
    try:
        get_runner().stop_save()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"ok": True, "job": get_runner().job.as_dict()})


@app.route("/api/files/<path:filename>", methods=["PATCH"])
def rename_file(filename):
    source = _safe_data_path(filename)
    body = request.get_json(silent=True) or {}
    target_name = _normal_output_name(body.get("name"))
    if not source or not os.path.isfile(source):
        return jsonify({"error": "File not found"}), 404
    if not target_name:
        return jsonify({"error": "Invalid new filename"}), 400
    target = _safe_data_path(target_name)
    if not target:
        return jsonify({"error": "Invalid destination file"}), 400
    if os.path.exists(target):
        return jsonify({"error": "Destination file already exists"}), 409
    os.replace(source, target)
    return jsonify({"ok": True, "name": target_name})


@app.route("/api/files/<path:filename>", methods=["DELETE"])
def delete_file(filename):
    path = _safe_data_path(filename)
    if not path or not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404
    os.remove(path)
    return jsonify({"ok": True})


@app.route("/api/session", methods=["DELETE"])
def delete_session():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    return jsonify({"ok": True})


@app.route("/api/result/<path:filename>")
def result(filename):
    safe = os.path.basename(filename)
    path = os.path.join(DATA_DIR, safe)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="application/json")


@app.route("/view/<path:filename>")
def view_result(filename):
    safe = os.path.basename(filename)
    path = _safe_data_path(safe)
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        abort(500)
    posts = []
    if isinstance(data, dict):
        raw_posts = data.get("posts", [])
        if isinstance(raw_posts, list):
            posts = raw_posts
        elif isinstance(data.get("urls"), list):
            posts = [{"post_url": url, "blocks": []} for url in data["urls"] if isinstance(url, str) and url.strip()]
    return render_template(
        "view.html",
        filename=safe,
        data=data,
        posts=posts if isinstance(posts, list) else [],
        raw_json=json.dumps(data, ensure_ascii=False, indent=2),
    )


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", "5000"))
    print(f"Web interface: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
