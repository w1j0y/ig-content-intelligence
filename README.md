# IG Content Intelligence (Restaurant-Focused)

IG Content Intelligence is an open-core Instagram analysis pipeline designed primarily for restaurants and food businesses.

It helps answer three key questions:

1. How is my Instagram content performing?
2. What are customers actually saying in comments?
3. What food content is trending right now in my category?

This repository contains the public, open-source components of the IG Content Intelligence pipeline:
- Instagram data collection
- Basic AI-based post classification
- Category-level trend detection

The strategy, recommendations, and reporting layers are intentionally kept private.

## Applicability Beyond Restaurants

Although IG Content Intelligence is optimized and documented using restaurants as the primary example, the underlying pipeline is category-agnostic.

By changing:

- target categories
- hashtag sets
- interpretation logic

the same workflow can be applied to:

- gyms and fitness studios
- cafes and bakeries
- beauty and cosmetics brands
- retail and e-commerce pages
- creators and influencers

Restaurants are used as the reference implementation because they combine:

- fast trend cycles
- high comment volume
- strong visual patterns

This makes them an ideal baseline for generalization.
---

## How the Pipeline Works (End-to-End)

The system is designed as a sequential data pipeline where each step produces structured output used by the next step.

Instagram Profile
        |
        v
[1] Profile Scraper
        |
        v
[2] Post Classification (Basic AI)
        |
        v
[3] Category Trend Scraper (Restaurants)
        |
        v
------------------ PRIVATE / PRO ------------------
        |
[4] Trend to Strategy Engine
        |
[5] Client-Ready PDF Reports

The public scripts focus on collecting and structuring data.
The private scripts transform that data into business decisions.

---

## Public Scripts (Open Source)

### 1. Instagram Profile Scraper

File:
ig_scrape_profile.py

Purpose:
Scrapes a business Instagram profile and extracts:
- post URLs
- captions
- comments (optional deep scan)
- timestamps
- engagement signals

Command example:
```
python3 ig_scrape_profile.py --handle instagram --posts 30 --deep --login
```
Output:
```
data/{handle}_YYYYMMDD_HHMM.json
```
Important note about login:
The first time you run this script, Chromium will open and you must manually log in to Instagram and complete any security verification (email or SMS code).
After this one-time step, future runs can be executed without the --login flag.

---

### 2. Post Classification (Basic AI)

File:
ig_classify_posts_basic.py

Purpose:
Adds high-level interpretation to each post:
- sentiment (positive, mixed, negative)
- recurring themes extracted from comments

This step provides structured understanding, not strategy.

Command example:
```
python3 ig_classify_posts_basic.py --input data/profile.json --output data/profile_classified.json --mode basic
```
Example output per post:
```
{
  "sentiment": "mixed",
  "themes": ["service_delay", "pricing"],
  "key_comments": [],
  "insight": "Upgrade to PRO for actionable insights."
}
```
---

### 3. Category Trend Scraper

File:
ig_scrape_trends_v2.py

Purpose:
Scrapes recently viral food-related Instagram content using:
- restaurant and food-centric hashtags
- recency filtering (default 72 hours)
- engagement-weighted ranking (comments weighted higher than likes)
- reel audio extraction for reuse

Command example:
```
python3 ig_scrape_trends_v2.py --category restaurant --max-reels 40 --max-hours 72 --login
```
Output:
```
data/restaurant/trends_restaurant_YYYYMMDD_HHMM.json
```
This output shows what content formats are currently working in the food niche, without explaining why.

---

## Private / PRO Modules (Not Open-Sourced)

### 4. Trend to Strategy Engine (PRO)

This internal module converts raw trend data into:
- restaurant-specific reel ideas
- content angles and hooks
- format recommendations
- posting strategy guidance
- local and cultural context awareness

This logic represents the core business intelligence of IG Trend Radar and is not open-sourced.

---

### 5. Client-Ready Reports (PRO)

The final stage generates professional deliverables, including:
- executive summaries
- positive, mixed, and negative content analysis
- trend radar breakdowns
- actionable operational and marketing recommendations

Reports are delivered as structured PDF documents suitable for restaurant owners or agencies.

---

## PRO Access

Access to the private modules is available via:
- one-time license
- private repository access
- managed reporting service

Contact:
contact@rycron.com

---

## Installation

Requirements:
- Python 3.9+
- Chromium (installed via Playwright)

Setup:
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
Create a .env file using .env.example and provide the required environment variables.

---

## Disclaimer

This project is provided for educational and research purposes.
Users are responsible for complying with Instagram’s Terms of Service and applicable laws.

---

## Summary

- Public code provides data collection and structured analysis
- Private modules provide strategy and decision-making
- Restaurant-focused by design
- Built to support real business use, not vanity metrics
