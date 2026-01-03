#!/usr/bin/env python3
"""
ig_classify_posts.py — Per-post sentiment + insight enricher
------------------------------------------------------------

For each post, adds:
- sentiment: "positive" | "mixed" | "negative"
- themes: ["tag1", "tag2", ...]
- key_comments: ["...", "..."]
- insight: short operational/marketing insight
"""

import os
import json
import argparse
import time
from typing import Dict, Any

from dotenv import load_dotenv
from openai import OpenAI

# ================================
# ENV & CLIENT
# ================================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set in .env")

client = OpenAI(api_key=OPENAI_API_KEY)


# ================================
# HELPERS
# ================================
def clean_text_for_model(text: str) -> str:
    """
    Clean raw_text to something more manageable for the model:
    - Strip huge Instagram footer junk.
    - Trim to max length (e.g. 4000 chars) to avoid token explosion.
    """
    if not text:
        return ""

    # Cut at "More posts from" etc if present
    cut_markers = [
        "More posts from",
        "About Blog Jobs Help",
        "Instagram from",
        "Uploading & Non-Users",
        "Privacy Terms",
        "Meta ©",
    ]
    for marker in cut_markers:
        if marker in text:
            text = text.split(marker)[0]

    # Trim to 4000 chars max
    max_len = 4000
    if len(text) > max_len:
        text = text[:max_len] + " ... [TRUNCATED]"

    return text.strip()


def classify_single_post(
    post: Dict[str, Any],
    model: str = "gpt-4o-mini",
    mode: str = "basic",
) -> Dict[str, Any]:
    """
    Call OpenAI once for a single post and return a small dict:
    {
      "sentiment": "...",
      "themes": [...],
      "key_comments": [...],
      "insight": "..."
    }
    """
    raw_text = post.get("raw_text", "") or ""
    cleaned = clean_text_for_model(raw_text)

    if not cleaned:
        return {
            "sentiment": "mixed",
            "themes": ["no_text"],
            "key_comments": [],
            "insight": "No caption or comments were available for this post.",
        }

    # ============================
    # SYSTEM PROMPT (MODE-AWARE)
    # ============================
    if mode == "basic":
        system_msg = (
            "You analyze ONE Instagram post.\n"
            "Return ONLY a JSON object with keys:\n"
            '{ "sentiment": "positive|mixed|negative",\n'
            '  "themes": ["short_tag1","short_tag2"] }\n'
            "Return ONLY valid JSON."
        )
    else:
        system_msg = (
            "You are an assistant that reads ONE Instagram post: caption + stacked comments.\n"
            "You must return ONLY a single JSON object with keys:\n"
            '{ "sentiment": "positive|mixed|negative",\n'
            '  "themes": ["short_tag1","short_tag2",...],\n'
            '  "key_comments": ["exact short comment snippet 1","..."],\n'
            '  "insight": "1–3 sentence operational/marketing insight based on comments" }\n'
            "- sentiment: overall mood of the comments about the BRAND (not just emojis).\n"
            "- key_comments: 2–4 the most informative short snippets, copy them exactly.\n"
            "- themes: 2–5 very short tags, lower-case.\n"
            "- insight: concise, concrete, actionable.\n"
            "Return ONLY valid JSON."
        )

    user_msg = (
        "Here is the caption + stacked comments from ONE Instagram post.\n"
        "Text:\n"
        "------------------\n"
        f"{cleaned}\n"
        "------------------\n"
        "Now respond ONLY with the JSON object."
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        )

        content = resp.choices[0].message.content
        data = json.loads(content)

        # ============================
        # SANITIZE OUTPUT
        # ============================
        sentiment = str(data.get("sentiment", "mixed")).lower()
        if sentiment not in {"positive", "mixed", "negative"}:
            sentiment = "mixed"

        themes = data.get("themes", [])
        if not isinstance(themes, list):
            themes = []

        key_comments = data.get("key_comments", [])
        if not isinstance(key_comments, list):
            key_comments = []

        insight = data.get("insight", "").strip()
        if not insight:
            insight = "No specific insight was generated."

        # ============================
        # MODE-SPECIFIC RETURN
        # ============================
        if mode == "basic":
            return {
                "sentiment": sentiment,
                "themes": themes,
                "key_comments": [],
                "insight": "Upgrade to PRO for actionable insights.",
            }

        return {
            "sentiment": sentiment,
            "themes": themes,
            "key_comments": key_comments,
            "insight": insight,
        }

    except Exception as e:
        print(f"[!] Error classifying post {post.get('url','')}: {e}")
        return {
            "sentiment": "mixed",
            "themes": ["fallback"],
            "key_comments": [],
            "insight": "Automatic classification failed; treat this post as mixed sentiment.",
        }


# ================================
# MAIN
# ================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input JSON from ig_scrape_profile_v2")
    parser.add_argument("--output", required=True, help="Output JSON with sentiment & insights")
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="OpenAI model to use for per-post classification (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--mode",
        choices=["basic", "pro"],
        default="basic",
        help="Classification mode: basic (open) or pro (paid)"
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.3,
        help="Sleep seconds between API calls to be gentle on rate limits (default: 0.3)",
    )
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    posts = data.get("posts", [])
    if not posts:
        print("[!] No posts found in input JSON.")
        return

    total = len(posts)
    print(f"[*] Loaded {total} posts from {args.input}")

    for i, post in enumerate(posts, start=1):
        url = post.get("url", "")
        print(f"[~] Classifying post {i}/{total}: {url}")

        classification = classify_single_post(
            post,
            model=args.model,
            mode=args.mode,
        )


        # Attach to post
        post["sentiment"] = classification["sentiment"]
        post["themes"] = classification["themes"]
        post["key_comments"] = classification["key_comments"]
        post["insight"] = classification["insight"]

        # Light rate-limiting
        time.sleep(args.sleep)

    # Write enriched JSON
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[✓] Saved classified JSON → {args.output}")


if __name__ == "__main__":
    main()
