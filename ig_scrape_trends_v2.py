#!/usr/bin/env python3
"""
ig_scrape_trends_v2.py — Advanced Instagram Trend Scraper (Mode C)
------------------------------------------------------------------

Goal:
    Detect *recent* trending REELS for a given business category (e.g. restaurant, gym)
    using a combination of category-specific hashtags and engagement filters.

Features:
    - Login with Playwright (uses IG_USERNAME / IG_PASSWORD from .env)
    - Category → hashtag presets (restaurant, gym, cafe, generic fallback)
    - Scrapes recent posts & reels (prioritizes reels) (a[href*="/reel/"])
    - For each candidate reel:
        * Opens the reel page
        * Extracts: timestamp, likes, comments, caption, audio name, hashtags
        * Computes age in hours, engagement_score = likes + comments*3
        * Skips reels older than max_hours (default 72h)
    - Keeps only TOP N reels sorted by engagement_score
    - Saves JSON:
        {
          "category": "...",
          "hashtags_used": [...],
          "scraped_at": "...",
          "max_hours": 72,
          "max_reels": 40,
          "reels": [
             {...}
          ]
        }

Usage:
    python3 ig_scrape_trends_v2.py --category restaurant --max-reels 40 --max-hours 72 --login
"""

import os
import re
import json
import argparse
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ===========================
# ENV / PATHS
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
# CATEGORY → HASHTAGS
# ===========================

CATEGORY_HASHTAGS = {
    # ===== FOOD & BEVERAGE =====
    "restaurant": [
        "restaurant",
        "foodie",
        "foodreels",
        "viralfood",
        "streetfood",
        "foodlover",
        "fypfood",
        "foodvibes",
        "cheesepull",
        "dinnerdate",
        "lunchideas",
        "foodporn",          # classic, still used a lot
        "forkyeah",
    ],
    "burger": [
        "burgerlover",
        "burgertime",
        "burgerreels",
        "smashburger",
        "cheeseburger",
        "burgersoftiktok",
        "burgersofinstagram",
    ],
    "pizza": [
        "pizzatime",
        "pizzanight",
        "pizzareels",
        "pizzalover",
        "pizzalovers",
        "pizzalove",
    ],
    "cafe": [
        "coffee",
        "coffeereels",
        "coffeetime",
        "coffeelover",
        "latteart",
        "coffeeshop",
        "coffeebar",
        "coffeebreak",
    ],
    "bakery": [
        "bakery",
        "bakerylove",
        "croissant",
        "pastry",
        "dessertreels",
        "sweettreats",
        "chocolatelover",
        "dessertlover",
    ],
    "bar": [
        "cocktails",
        "cocktailreels",
        "mixology",
        "bartenderlife",
        "nightout",
        "happyhour",
        "drinkswithfriends",
    ],

    # ===== FITNESS & WELLNESS =====
    "gym": [
        "gym",
        "gymreels",
        "fitness",
        "workout",
        "fitreels",
        "gymmotivation",
        "gymlife",
        "legday",
        "pushpulllegs",
        "hypertrophy",
    ],
    "personal_trainer": [
        "personaltrainer",
        "ptlife",
        "onlinetraining",
        "onlinecoach",
        "fitnessmotivation",
        "homeworkout",
    ],
    "yoga": [
        "yoga",
        "yogareels",
        "yogapractice",
        "yogainspiration",
        "yogaflow",
        "mindfulness",
    ],

    # ===== BEAUTY & CLINICS =====
    "beauty_salon": [
        "hairreels",
        "hairtransformation",
        "hairgoals",
        "salonreels",
        "nailart",
        "nailsreels",
        "beautysalon",
    ],
    "clinic": [
        "skincareclinic",
        "dermatology",
        "aestheticclinic",
        "beforeandafter",
        "skinreels",
        "facialtreatment",
    ],
    "dentist": [
        "dentalreels",
        "smilemakeover",
        "teethwhitening",
        "dentist",
        "dentalclinic",
        "beforeandafter",
    ],

    # ===== HOSPITALITY & EXPERIENCES =====
    "hotel": [
        "hotelreels",
        "hotellife",
        "staycation",
        "luxuryhotel",
        "boutiquehotel",
        "hotelview",
    ],
    "resort": [
        "beachresort",
        "poolday",
        "resortlife",
        "vacationvibes",
        "summerreels",
    ],
    "party": [
        "partyreels",
        "nightlife",
        "clubreels",
        "djlife",
        "festivalseason",
    ],

    # ===== RETAIL / SUPERMARKET / STORES =====
    "supermarket": [
        "groceryhaul",
        "supermarket",
        "shoppingreels",
        "groceryshopping",
        "budgetshopping",
    ],
    "fashion_store": [
        "outfitinspo",
        "ootdreels",
        "fashionreels",
        "tryonhaul",
        "streetstyle",
    ],

    # ===== FALLBACK =====
    "_generic": [
        "trending",
        "viral",
        "explorepage",
        "reels",
        "fyp",
    ],
}


# ===========================
# HELPERS
# ===========================

def clean_text(text: str) -> str:
    if not text:
        return ""
    # Remove Instagram boilerplate-ish noise (light)
    text = re.sub(r"Sorry, we're having trouble playing this video\.?", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_hashtags(caption: str) -> list[str]:
    if not caption:
        return []
    tags = re.findall(r"#\w+", caption)
    # Normalize to lowercase, unique
    return sorted(set(t.lower() for t in tags))


def parse_count(value: str) -> int | None:
    """
    Turn '12,3K', '4.5M', '12,345' into integer.
    """
    if not value:
        return None
    v = value.strip().lower()
    # Remove commas
    v = v.replace(",", "")
    multiplier = 1
    if v.endswith("k"):
        multiplier = 1_000
        v = v[:-1]
    elif v.endswith("m"):
        multiplier = 1_000_000
        v = v[:-1]

    try:
        num = float(v)
        return int(num * multiplier)
    except Exception:
        return None


async def ensure_logged_in(page):
    print("[*] Checking login status (trends scraper)...")
    await page.goto("https://www.instagram.com/", wait_until="networkidle")
    await page.wait_for_timeout(2000)

    # Heuristic: profile avatar present => logged in
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


async def expand_comments_light(page):
    """
    Light comment expansion — we just want enough text to detect high engagement.
    """
    selectors = [
        "text=View all",
        "text=View all comments",
        "text=View more comments",
        "text=More comments",
        "text=See more",
    ]
    for _ in range(5):
        found = False
        for sel in selectors:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                try:
                    await btn.click()
                    await page.wait_for_timeout(800)
                    found = True
                except Exception:
                    pass
        if not found:
            break


async def extract_reel_details(page, url: str, max_hours: int):
    """
    Extracts details from a single REEL page.
    Skips if older than max_hours.
    """
    print(f"[~] Scraping reel: {url}")
    try:
        await page.goto(url, wait_until="networkidle", timeout=80000)
    except Exception:
        print("[!] Timeout loading reel.")
        return None

    await page.wait_for_timeout(2000)

    # Try to expand some comments (light)
    await expand_comments_light(page)

    # ---------------- Timestamp & age ----------------
    timestamp_iso = None
    age_hours = None
    try:
        ts = await page.locator("time").first.get_attribute("datetime")
        if ts:
            timestamp_iso = ts
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = now - dt
            age_hours = delta.total_seconds() / 3600.0
    except Exception:
        pass

    if age_hours is None:
        # If we can't determine age, skip (to stay safe)
        print("   [!] No timestamp — skipping (unknown age).")
        return None

    if age_hours > max_hours:
        print(f"   [!] Reel is too old ({age_hours:.1f}h > {max_hours}h) — skipping.")
        return None

    # ---------------- Page text for counts ----------------
    try:
        body_text = await page.inner_text("body")
    except Exception:
        body_text = ""

    body_text_clean = clean_text(body_text)

    # Likes
    likes = None
    m_like = re.search(r"([\d.,]+)\s+likes", body_text_clean, flags=re.I)
    if m_like:
        likes = parse_count(m_like.group(1))

    # Comments
    comments = None
    m_com = re.search(r"([\d.,]+)\s+comments", body_text_clean, flags=re.I)
    if m_com:
        comments = parse_count(m_com.group(1))

    if likes is None:
        likes = 0
    if comments is None:
        comments = 0

    # ---------------- Caption ----------------
    caption = ""
    try:
        article = page.locator("article").first
        if await article.count() > 0:
            raw = await article.inner_text()
        else:
            raw = body_text
        caption = clean_text(raw)
    except Exception:
        caption = clean_text(body_text)

    # ---------------- Audio Name ----------------
    audio_name = ""
    try:
        # Typical: <a href="/audio/...">Audio name</a>
        audio_link = page.locator("a[href*='/audio/']").first
        if await audio_link.count() > 0:
            audio_name = await audio_link.inner_text()
            audio_name = clean_text(audio_name)
    except Exception:
        audio_name = ""

    # ---------------- Hashtags ----------------
    hashtags = extract_hashtags(caption)

    engagement_score = likes + comments * 3

    # Shortcode
    shortcode = None
    m_sc = re.search(r"/reel/([^/]+)/", url) or re.search(r"/p/([^/]+)/", url)
    if m_sc:
        shortcode = m_sc.group(1)


    return {
        "url": url,
        "shortcode": shortcode,
        "timestamp": timestamp_iso,
        "age_hours": round(age_hours, 2),
        "likes": likes,
        "comments": comments,
        "engagement_score": engagement_score,
        "audio_name": audio_name,
        "caption": caption,
        "hashtags": hashtags,
    }


async def scrape_trends(category: str, max_reels: int, max_hours: int, headless: bool):
    """
    Main trends scraper:
      - Picks hashtags from CATEGORY_HASHTAGS
      - Visits /explore/tags/<hashtag>/
      - Collects candidate /reel/ URLs
      - Opens each reel and extracts metrics
      - Filters by max_hours
      - Returns top N by engagement_score
    """
    # Hashtag selection
    hashtags = CATEGORY_HASHTAGS.get(category.lower())
    if not hashtags:
        print(f"[!] Unknown category '{category}', using generic hashtags.")
        hashtags = CATEGORY_HASHTAGS["_generic"]

    print(f"[+] Category: {category}")
    print(f"[+] Hashtags to scan: {hashtags}")
    print(f"[+] Max reels: {max_reels} | Max age: {max_hours}h")

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

        candidate_urls = set()

        # -----------------------------
        # Round 1: collect candidate /reel/ URLs from each hashtag
        # -----------------------------
        for tag in hashtags:
            tag_url = f"https://www.instagram.com/explore/tags/{tag}/"
            print(f"\n[~] Visiting hashtag: #{tag} -> {tag_url}")
            try:
                await page.goto(tag_url, wait_until="networkidle", timeout=60000)
            except Exception:
                print(f"[!] Timeout loading hashtag #{tag}, skipping.")
                continue

            await page.wait_for_timeout(3000)

            # Scroll a bit to load more content (focus on "top" + a bit of "recent")
            # Wait for grid links to appear (posts or reels)
            try:
                await page.wait_for_selector(
                    "a[role='link'][href*='/reel/'], a[role='link'][href*='/p/']",
                    timeout=10000,
                )
            except Exception:
                print(f"   [!] No grid links visible yet for #{tag} (after initial load).")

            # Scroll a bit to load more content (focus on 'top' + some 'recent')
            for _ in range(8):
                await page.mouse.wheel(0, 2200)
                await page.wait_for_timeout(900)

            # Collect BOTH reels and posts as candidates
            anchors = await page.locator(
                "a[role='link'][href*='/reel/'], a[role='link'][href*='/p/']"
            ).all()
            print(f"   [~] Found {len(anchors)} grid anchors under #{tag}")

            for el in anchors:
                href = await el.get_attribute("href")
                if not href:
                    continue
                full_url = "https://www.instagram.com" + href
                candidate_urls.add(full_url)


        print(f"\n[✓] Total candidate REEL URLs collected (before per-reel filtering): {len(candidate_urls)}")

        # -----------------------------
        # Round 2: open each reel, extract metrics, filter by recency
        # -----------------------------
        results = []
        for url in candidate_urls:
            details = await extract_reel_details(page, url, max_hours)
            if not details:
                continue
            results.append(details)

        await ctx.close()

        if not results:
            print("[!] No recent reels found within the given time window.")
            return {
                "category": category,
                "hashtags_used": hashtags,
                "scraped_at": datetime.utcnow().isoformat(),
                "max_hours": max_hours,
                "max_reels": max_reels,
                "reels": [],
            }

        # Sort by engagement_score DESC
        results.sort(key=lambda r: r.get("engagement_score", 0), reverse=True)

        # Keep top N
        final_reels = results[:max_reels]
        print(f"[✓] Final selected reels: {len(final_reels)} (top by engagement)")

        return {
            "category": category,
            "hashtags_used": hashtags,
            "scraped_at": datetime.utcnow().isoformat(),
            "max_hours": max_hours,
            "max_reels": max_reels,
            "reels": final_reels,
        }


# ===========================
# MAIN
# ===========================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True, help="Business category (restaurant, gym, cafe, ...)")
    parser.add_argument("--max-reels", type=int, default=40, help="Max number of reels to keep after sorting")
    parser.add_argument("--max-hours", type=int, default=72, help="Only keep reels newer than this many hours")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Run browser non-headless for debugging (show window).",
    )
    args = parser.parse_args()

    headless = not args.login

    data = asyncio.run(
        scrape_trends(
            category=args.category,
            max_reels=args.max_reels,
            max_hours=args.max_hours,
            headless=headless,
        )
    )

    if not data:
        print("[!] Trend scrape failed.")
        return

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")

    # NEW: output directory = data/<category>/
    category_dir = os.path.join(DATA_DIR, args.category)
    os.makedirs(category_dir, exist_ok=True)

    outfile = os.path.join(category_dir, f"trends_{args.category}_{ts}.json")

    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[✓] Saved trends JSON → {outfile}")


if __name__ == "__main__":
    main()
