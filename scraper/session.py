import os
import time
from playwright.sync_api import sync_playwright
from .config import SESSION_FILE, HEADLESS, TIMEOUT


def _has_login_cookie(context):
    try:
        cookies = context.cookies("https://www.facebook.com/")
        return any(c["name"] == "c_user" for c in cookies)
    except Exception:
        return False


def _wait_for_login(page, context, timeout=900):
    print("=" * 55)
    print("Log in to Facebook in the Chrome window.")
    print("Processing will continue automatically after login.")
    print("=" * 55)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _has_login_cookie(context):
            print("Login detected; continuing.")
            return True
        time.sleep(3)
    print("Timed out before login was detected.")
    return False


def login_once():
    if os.path.exists(SESSION_FILE) and _session_has_login():
        print(f"An active session already exists: {SESSION_FILE}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        ok = _wait_for_login(page, context)
        if ok:
            context.storage_state(path=SESSION_FILE)
            print(f"Session saved: {SESSION_FILE}")
        else:
            print("Session was not saved.")
        browser.close()


def _session_has_login():
    try:
        import json
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return any(c.get("name") == "c_user" for c in data.get("cookies", []))
    except Exception:
        return False


def open_browser():
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=HEADLESS)
    if os.path.exists(SESSION_FILE):
        context = browser.new_context(storage_state=SESSION_FILE)
    else:
        context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(TIMEOUT)
    return p, browser, context, page