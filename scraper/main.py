import argparse
from .config import GROUP_IDS, POST_LIMIT, SESSION_FILE
from .session import login_once, open_browser, _wait_for_login, _session_has_login
from .group_feed import collect_post_links
from .post_comments import collect_post_data
from .exporters import export_group


def cmd_login(args):
    login_once()


def cmd_scrape(args):
    if not GROUP_IDS:
        print("ERROR: GRUP_ID_1 is not configured in .env.")
        print("Example: GRUP_ID_1=815718713544103")
        return

    p, browser, context, page = open_browser()
    try:
        if not _session_has_login():
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
            ok = _wait_for_login(page, context)
            if ok:
                context.storage_state(path=SESSION_FILE)
                print(f"Session saved: {SESSION_FILE}")
            else:
                print("[ERROR] Facebook login was not completed. Closing the browser.")
                return

        for gid in GROUP_IDS:
            print(f"[INFO] Processing group {gid}...")
            links = collect_post_links(page, gid, limit=args.posts or POST_LIMIT)
            if not links:
                print(f"[WARNING] No post URLs found for group {gid}.")
                continue
            print(f"[INFO] Found {len(links)} posts.")

            posts = []
            for i, url in enumerate(links, 1):
                print(f"  [{i}/{len(links)}] {url}")
                try:
                    data = collect_post_data(page, url)
                    posts.append(data)
                except Exception as e:
                    print(f"    [ERROR] {e}")

            out = export_group(posts, gid)
            print(f"[OK] Saved: {out}")
    finally:
        try:
            context.storage_state(path=SESSION_FILE)
        except Exception:
            pass
        browser.close()
        p.stop()


def main():
    parser = argparse.ArgumentParser(prog="scraper", description="Facebook group scraper (Playwright)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="Log in to Facebook and save the session")

    p_scrape = sub.add_parser("scrape", help="Collect posts and comments from groups")
    p_scrape.add_argument("--posts", type=int, default=POST_LIMIT, help=f"Maximum posts per group (default {POST_LIMIT})")

    args = parser.parse_args()

    if args.cmd == "login":
        cmd_login(args)
    elif args.cmd == "scrape":
        cmd_scrape(args)


if __name__ == "__main__":
    main()
