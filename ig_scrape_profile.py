#!/usr/bin/env python3
"""
ig_scrape_profile.py — FINAL BULLETPROOF VERSION (2025, with deep mode + DB)
-------------------------------------------------------------------------------

✔ Uses your old reliable grid selectors (never empty)
✔ Skips pinned posts
✔ Extracts ALL comments (deep expand)
✔ Sorts posts by REAL timestamps (newest → oldest)
✔ Applies --posts limit AFTER sorting
✔ Works headless / login
✔ Fully compatible with ig_analyze_profile.py
✔ SQLite DB to avoid re-scraping already analyzed posts
✔ --deep mode: scrolls much deeper until we hit N new posts or exhaust profile
"""

import sqlite3
import os
import json
import asyncio
import argparse
import re
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ===========================
# ENV
# ===========================
load_dotenv()
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
if not IG_USERNAME or not IG_PASSWORD:
    raise RuntimeError(
        "Missing IG_USERNAME or IG_PASSWORD. "
        "Create a .env file (see .env.example)."
    )


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROFILE_DIR = os.path.join(BASE_DIR, ".pw_profile")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PROFILE_DIR, exist_ok=True)

# ===========================
# DATABASE (SQLite)
# ===========================

DB_PATH = os.path.join(BASE_DIR, "db")
os.makedirs(DB_PATH, exist_ok=True)
DB_FILE = os.path.join(DB_PATH, "ig_posts.db")


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handle TEXT NOT NULL,
            post_url TEXT NOT NULL,
            timestamp TEXT,
            added_at TEXT NOT NULL,
            UNIQUE(handle, post_url)
        );
        """
    )
    conn.commit()
    conn.close()


def load_known_posts(handle: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT post_url FROM posts WHERE handle = ?", (handle,))
    rows = c.fetchall()
    conn.close()
    return set(r[0] for r in rows)


def save_post_to_db(handle: str, post_url: str, timestamp: str | None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT OR IGNORE INTO posts (handle, post_url, timestamp, added_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (handle, post_url, timestamp),
        )
        conn.commit()
    except Exception as e:
        print("[DB ERROR]", e)
    conn.close()


# ===========================
# CLEANERS
# ===========================

UNWANTED_PATTERNS = [
    r"Sorry, we're having trouble playing this video\.?",
    r"Learn more",
    r"Original audio",
    r"View all [0-9]+ replies",
    r"View replies",
    r"See translation",
    r"Hide all replies",
    r"Meta.*",
    r"Privacy.*",
    r"Terms.*",
    r"Instagram Lite.*",
    r"Threads.*",
    r"Follow [A-Za-z0-9_.]+",
]


def clean_raw_text(text: str) -> str:
    if not text:
        return ""
    for pat in UNWANTED_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ===========================
# LOGIN
# ===========================

async def ensure_logged_in(page):
    print("[*] Checking login status...")
    await page.goto("https://www.instagram.com/", wait_until="networkidle")

    if await page.locator("img[alt*='profile picture']").count() > 0:
        print("[✓] Already logged in.")
        return True

    print("[~] Logging in...")
    await page.goto("https://www.instagram.com/accounts/login/", wait_until="networkidle")
    await page.wait_for_timeout(2000)

    await page.fill("input[name='username']", IG_USERNAME)
    await page.fill("input[name='password']", IG_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_timeout(6000)

    if await page.locator("img[alt*='profile picture']").count() > 0:
        print("[✓] Login successful.")
        return True

    print("[!] Login failed.")
    return False


# ===========================
# COMMENT EXPANDER (deep)
# ===========================

async def expand_comments(page):
    selectors = [
        "text=View all",
        "text=View all comments",
        "text=View more comments",
        "text=View replies",
        "text=See more",
        "text=More comments",
    ]

    # Repeat expansion cycles
    for _ in range(15):
        found = False
        for sel in selectors:
            button = page.locator(sel).first
            if await button.count() > 0:
                try:
                    await button.click()
                    await page.wait_for_timeout(1000)
                    found = True
                except Exception:
                    pass
        if not found:
            break


# ===========================
# POST SCRAPER
# ===========================

async def extract_post_details(page, post_url: str):
    print(f"[~] Scraping: {post_url}")

    # Load the post
    try:
        await page.goto(post_url, wait_until="networkidle", timeout=80000)
    except Exception:
        print("[!] Timeout loading post.")
        return {
            "url": post_url,
            "timestamp": None,
            "raw_text": "",
            "type": "unknown",
        }

    await page.wait_for_timeout(2000)

    # Expand comments aggressively
    await expand_comments(page)

    # Identify type
    post_type = "reel" if "/reel/" in post_url else "photo"

    # Extract timestamp
    timestamp = None
    try:
        ts = await page.locator("time").first.get_attribute("datetime")
        if ts:
            timestamp = ts
    except Exception:
        pass

    # Use article-based extraction (more reliable)
    try:
        article = page.locator("article").first
        if await article.count() > 0:
            raw = await article.inner_text()
        else:
            raw = await page.inner_text("body")
    except Exception:
        raw = ""

    cleaned = clean_raw_text(raw)

    return {
        "url": post_url,
        "timestamp": timestamp,
        "raw_text": cleaned,
        "type": post_type,
    }


# ===========================
# SCRAPE PROFILE
# ===========================

async def scrape_profile(
    handle: str,
    max_posts: int,
    headless: bool,
    deep: bool,
    dry_run: bool,
):
    # Initialize SQLite DB
    if dry_run:
        print("[~] DRY-RUN mode enabled — skipping database usage.")
        known_posts = set()
    else:
        init_db()
        known_posts = load_known_posts(handle)
        print(f"[✓] Known posts in DB for {handle}: {len(known_posts)}")


    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=headless,
            viewport={"width": 1400, "height": 2400},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await ctx.new_page()

        if not await ensure_logged_in(page):
            await ctx.close()
            return None

        print(f"[~] Opening profile: https://www.instagram.com/{handle}/")
        await page.goto(f"https://www.instagram.com/{handle}/", wait_until="networkidle")
        await page.wait_for_timeout(3000)

        urls = []

        if deep:
            # ==========================
            # DEEP MODE: SCROLL UNTIL N NEW POSTS OR END
            # ==========================
            print("[~] Collecting post URLs (deep mode)...")
            collected = set()
            stagnant_rounds = 0
            MAX_STAGNANT = 5
            MAX_SCROLL_ROUNDS = 200  # safety limit

            for round_idx in range(MAX_SCROLL_ROUNDS):
                anchors = await page.locator("a[href*='/p/'], a[href*='/reel/']").all()

                new_in_round = 0
                for el in anchors:
                    href = await el.get_attribute("href")
                    if not href:
                        continue
                    full_url = "https://www.instagram.com" + href

                    # Skip already-known posts (from DB) and duplicates in this run
                    if full_url in known_posts or full_url in collected:
                        continue

                    collected.add(full_url)
                    new_in_round += 1

                if new_in_round > 0:
                    print(
                        f"[~] Deep round {round_idx+1}: +{new_in_round} new posts "
                        f"(total new this run: {len(collected)})"
                    )
                    stagnant_rounds = 0
                else:
                    stagnant_rounds += 1
                    print(f"[~] Deep round {round_idx+1}: 0 new posts (stagnant={stagnant_rounds})")

                # Stop if we reached the requested number of new posts
                if len(collected) >= max_posts:
                    print(f"[✓] Reached requested {max_posts} NEW posts in deep mode.")
                    break

                # Stop if feed seems exhausted
                if stagnant_rounds >= MAX_STAGNANT:
                    print("[!] No new posts appearing for several rounds — assuming end of profile.")
                    break

                # Scroll further
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(1200)

            urls = list(collected)
            print(f"[✓] Deep-mode collected {len(urls)} NEW candidate posts (excluding DB-known)")

        else:
            # ==========================
            # NORMAL MODE: QUICK SCROLL (~12 ROUNDS)
            # ==========================
            print("[~] Scrolling feed to collect candidate posts (normal mode)...")
            for _ in range(12):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(900)

            anchors = await page.locator("a[href*='/p/'], a[href*='/reel/']").all()
            seen = set()

            for el in anchors:
                href = await el.get_attribute("href")
                if not href:
                    continue

                full_url = "https://www.instagram.com" + href

                if full_url in seen or full_url in known_posts:
                    continue

                seen.add(full_url)

            urls = list(seen)
            print(f"[✓] Normal mode collected {len(urls)} candidate posts (excluding DB-known)")

        if not urls:
            print("[!] No new posts to scrape (all posts already in DB or profile empty).")
            await ctx.close()
            return {
                "handle": handle,
                "scraped_at": datetime.utcnow().isoformat(),
                "posts": [],
            }

        # SCRAPE ALL TIMESTAMPS FIRST to sort correctly
        post_entries = []
        for url in urls:
            details = await extract_post_details(page, url)
            post_entries.append(details)

            # Store in database
            if not dry_run:
                save_post_to_db(handle, details["url"], details["timestamp"])
        await ctx.close()

        # Remove pinned posts
        def is_pinned(post: dict) -> bool:
            """
            Pinned posts often have non-standard timestamps or missing datetime.
            We skip anything without a proper ISO timestamp ending with 'Z'.
            """
            ts = post["timestamp"]
            return ts is None or not isinstance(ts, str) or not ts.endswith("Z")

        filtered = [p for p in post_entries if not is_pinned(p)]
        print(f"[✓] After removing pinned posts: {len(filtered)} remain")

        # SORT NEWEST FIRST
        def parse_timestamp(ts):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return datetime.min

        filtered.sort(key=lambda p: parse_timestamp(p["timestamp"]), reverse=True)

        # Limit to newest N
        final_posts = filtered[:max_posts]
        print(f"[✓] Final returned posts (after limit): {len(final_posts)}")

        return {
            "handle": handle,
            "scraped_at": datetime.utcnow().isoformat(),
            "posts": final_posts,
        }


# ===========================
# MAIN
# ===========================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handle", required=True)
    parser.add_argument("--posts", type=int, default=30)
    parser.add_argument("--login", action="store_true", help="Run browser non-headless for debugging")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Use deep mode: scroll much more to collect as many NEW posts as possible.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without using or writing to the SQLite database",
    )

    args = parser.parse_args()

    headless = not args.login

    data = asyncio.run(
        scrape_profile(
            handle=args.handle,
            max_posts=args.posts,
            headless=headless,
            deep=args.deep,
            dry_run=args.dry_run,
        )
    )
    if not data:
        print("[!] Scrape failed.")
        return

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
    # Create per-handle directory
    handle_dir = os.path.join(DATA_DIR, args.handle)
    os.makedirs(handle_dir, exist_ok=True)

    outfile = os.path.join(handle_dir, f"{args.handle}_{ts}.json")

    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[✓] Saved → {outfile}")


if __name__ == "__main__":
    main()
