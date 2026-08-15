import os
import re
import time
import random
import sqlite3
from datetime import datetime, timezone
from html import escape
from urllib.parse import urljoin, urlsplit, urlunsplit
from email.utils import parsedate_to_datetime

import numpy as np
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DB_NAME = "properties.db"

PRESET_SEARCHES = [
    {
        "name": "Morningside",
        "url": "https://www.property24.com/apartments-for-sale/morningside/sandton/gauteng/4258",
    }
]

PHYSICAL_MIN_RATE = 6500
PHYSICAL_MAX_RATE = 80000
DEAL_PERCENT = 5
MAX_PAGE_ATTEMPTS = 6
PAGE_DELAY_SECONDS = (3.0, 6.0)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Current scrape snapshot. This can safely be rebuilt if an old schema is found.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_listings (
            id TEXT PRIMARY KEY,
            area TEXT,
            title TEXT,
            price REAL,
            sqm REAL,
            rate_sqm REAL,
            url TEXT
        )
        """
    )
    c.execute("PRAGMA table_info(raw_listings)")
    raw_columns = {row[1] for row in c.fetchall()}
    required_raw = {"id", "area", "title", "price", "sqm", "rate_sqm", "url"}
    if not required_raw.issubset(raw_columns):
        c.execute("DROP TABLE IF EXISTS raw_listings")
        c.execute(
            """
            CREATE TABLE raw_listings (
                id TEXT PRIMARY KEY,
                area TEXT,
                title TEXT,
                price REAL,
                sqm REAL,
                rate_sqm REAL,
                url TEXT
            )
            """
        )

    # Current market statistics. Also safe to rebuild from the next scrape.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS area_stats (
            area TEXT PRIMARY KEY,
            total_raw INTEGER,
            total_clean INTEGER,
            reported_total INTEGER,
            pages_scraped INTEGER,
            real_min REAL,
            real_max REAL,
            real_median REAL,
            bottom_5_cutoff REAL,
            last_scraped_at TEXT
        )
        """
    )
    c.execute("PRAGMA table_info(area_stats)")
    stats_columns = {row[1] for row in c.fetchall()}
    required_stats = {
        "area",
        "total_raw",
        "total_clean",
        "reported_total",
        "pages_scraped",
        "real_min",
        "real_max",
        "real_median",
        "bottom_5_cutoff",
        "last_scraped_at",
    }
    if not required_stats.issubset(stats_columns):
        c.execute("DROP TABLE IF EXISTS area_stats")
        c.execute(
            """
            CREATE TABLE area_stats (
                area TEXT PRIMARY KEY,
                total_raw INTEGER,
                total_clean INTEGER,
                reported_total INTEGER,
                pages_scraped INTEGER,
                real_min REAL,
                real_max REAL,
                real_median REAL,
                bottom_5_cutoff REAL,
                last_scraped_at TEXT
            )
            """
        )

    # Permanent discovery history. Do NOT drop this table because it prevents Telegram duplicates.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS listing_history (
            id TEXT PRIMARY KEY,
            area TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            first_seen_qualifies INTEGER DEFAULT 0,
            alerted INTEGER DEFAULT 0
        )
        """
    )
    c.execute("PRAGMA table_info(listing_history)")
    history_columns = {row[1] for row in c.fetchall()}
    history_additions = {
        "area": "TEXT",
        "first_seen_at": "TEXT",
        "last_seen_at": "TEXT",
        "first_seen_qualifies": "INTEGER DEFAULT 0",
        "alerted": "INTEGER DEFAULT 0",
    }
    for column, column_type in history_additions.items():
        if column not in history_columns:
            c.execute(f"ALTER TABLE listing_history ADD COLUMN {column} {column_type}")

    conn.commit()
    conn.close()


def build_session():
    session = requests.Session()
    # Retry broken connections here. HTTP status retries are handled by
    # fetch_page(), where they can be logged and use Property24's Retry-After.
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.2,
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-ZA,en;q=0.9",
            "Cache-Control": "no-cache",
        }
    )
    return session


def retry_after_seconds(response):
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def fetch_page(session, page_url, page, referer=None):
    """Fetch one results page, patiently retrying temporary server throttles."""
    transient_statuses = {429, 500, 502, 503, 504}
    headers = {"Referer": referer} if referer else {}

    for attempt in range(1, MAX_PAGE_ATTEMPTS + 1):
        response = session.get(page_url, timeout=25, headers=headers)
        if response.status_code not in transient_statuses:
            return response

        if attempt == MAX_PAGE_ATTEMPTS:
            return response

        server_delay = retry_after_seconds(response)
        backoff = min(45.0, 4.0 * (2 ** (attempt - 1)))
        delay = max(server_delay or 0.0, backoff) + random.uniform(0.5, 2.5)
        print(
            f"Property24 returned HTTP {response.status_code} on page {page}; "
            f"retrying in {delay:.1f}s ({attempt}/{MAX_PAGE_ATTEMPTS})."
        )
        response.close()
        time.sleep(delay)

    raise AssertionError("unreachable")


def normalise_url(href):
    if not href:
        return None
    full_url = urljoin("https://www.property24.com", href)
    parts = urlsplit(full_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def extract_number(text):
    if not text:
        return None
    match = re.search(r"([\d\s,.]+)", text)
    if not match:
        return None
    cleaned = re.sub(r"[^\d.]", "", match.group(1).replace(",", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def extract_reported_total(soup):
    text = soup.get_text(" ", strip=True)
    match = re.search(r"\b([\d][\d\s,]*)\s+results\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def find_listing_tiles(soup):
    # Property24 currently exposes result cards as js_resultTile/p24_tileContainer.
    selectors = [
        ".js_listingResultsContainer .js_resultTile",
        ".js_listingResultsContainer [data-listing-number]",
        ".js_resultTile",
        "[data-listing-number]",
    ]

    unique = []
    seen_objects = set()
    for selector in selectors:
        for tile in soup.select(selector):
            marker = id(tile)
            if marker not in seen_objects:
                seen_objects.add(marker)
                unique.append(tile)

    return unique


def extract_listing_url(tile):
    candidates = []
    for a in tile.find_all("a", href=True):
        href = a.get("href", "")
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        full_url = normalise_url(href)
        if not full_url:
            continue
        score = 0
        if "property24.com" in full_url:
            score += 1
        if re.search(r"/\d+$", urlsplit(full_url).path):
            score += 3
        if "/for-sale/" in full_url or "/to-rent/" in full_url:
            score += 2
        candidates.append((score, full_url))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def parse_listing_tile(tile, area):
    url = extract_listing_url(tile)

    listing_id = str(tile.get("data-listing-number") or "").strip()
    if not listing_id and url:
        match = re.search(r"/(\d+)$", urlsplit(url).path)
        if match:
            listing_id = match.group(1)

    if not listing_id:
        return None

    title = None
    meta_title = tile.select_one('meta[itemprop="name"]')
    if meta_title and meta_title.get("content"):
        title = meta_title.get("content").strip()
    if not title:
        title_tag = tile.select_one(".p24_title") or tile.select_one(".p24_location")
        if title_tag:
            title = title_tag.get_text(" ", strip=True)
    if not title:
        title = "Property Listing"

    price = None
    price_tag = tile.select_one(".p24_price")
    if price_tag:
        price = extract_number(price_tag.get_text(" ", strip=True))

    sqm = None

    # Prefer an explicitly labelled floor size.
    floor_tag = tile.find(attrs={"title": re.compile(r"^Floor Size$", re.IGNORECASE)})
    if floor_tag:
        sqm = extract_number(floor_tag.get_text(" ", strip=True))

    # Property24 commonly exposes the size in p24_size on the result card.
    if sqm is None:
        for size_tag in tile.select(".p24_size"):
            size_text = size_tag.get_text(" ", strip=True)
            if re.search(r"m\s*[²2]|sqm", size_text, flags=re.IGNORECASE):
                sqm = extract_number(size_text)
                if sqm:
                    break

    # Final fallback: search only within the listing card text.
    if sqm is None:
        tile_text = tile.get_text(" ", strip=True)
        size_match = re.search(r"([\d][\d\s,.]*)\s*m\s*[²2]\b", tile_text, flags=re.IGNORECASE)
        if size_match:
            sqm = extract_number(size_match.group(1))

    rate_sqm = None
    if price is not None and sqm is not None and sqm > 0:
        rate_sqm = price / sqm

    return {
        "id": listing_id,
        "area": area,
        "title": title,
        "price": price,
        "sqm": sqm,
        "rate_sqm": rate_sqm,
        "url": url,
    }


def clean_area_items(items):
    usable = [x for x in items if x["rate_sqm"] is not None and x["rate_sqm"] > 0]
    if not usable:
        return [], 0.0, 0.0, 0.0

    # Pass 1: broad physical plausibility bounds.
    physical = [x for x in usable if PHYSICAL_MIN_RATE <= x["rate_sqm"] <= PHYSICAL_MAX_RATE]
    if not physical:
        physical = usable

    physical = sorted(physical, key=lambda x: x["rate_sqm"])
    if len(physical) < 5:
        rates = [x["rate_sqm"] for x in physical]
        return physical, float(min(rates)), float(max(rates)), float(np.median(rates))

    # Pass 2: isolate large gaps only at the extreme tails.
    rates = [x["rate_sqm"] for x in physical]
    diffs = np.diff(rates)
    positive_diffs = [float(d) for d in diffs if d > 0]
    median_diff = float(np.median(positive_diffs)) if positive_diffs else 1.0

    low_idx = 0
    low_limit = min(8, len(diffs))
    for i in range(low_limit):
        if diffs[i] > max(1500, median_diff * 4):
            low_idx = i + 1

    high_idx = len(physical)
    high_start = len(diffs) - 1
    high_stop = max(len(diffs) - 9, -1)
    for i in range(high_start, high_stop, -1):
        if diffs[i] > max(3000, median_diff * 4):
            high_idx = i + 1
            break

    clean = physical[low_idx:high_idx]
    if not clean:
        clean = physical

    clean_rates = [x["rate_sqm"] for x in clean]
    return (
        clean,
        float(min(clean_rates)),
        float(max(clean_rates)),
        float(np.median(clean_rates)),
    )


def send_telegram_alert(item, area, median_rate, rank_num, total_clean, percentile):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ Telegram credentials are missing.")
        return False

    below_median = ((median_rate - item["rate_sqm"]) / median_rate * 100) if median_rate else 0.0
    title = escape(str(item["title"]))
    area_html = escape(str(area))
    url = escape(str(item["url"] or ""), quote=True)

    message = (
        "🔥 <b>NEW LOWEST 5% PROPERTY</b>\n\n"
        f"📍 <b>{title}</b>\n"
        f"🏷️ {area_html}\n"
        f"💰 Price: <b>R {item['price']:,.0f}</b>\n"
        f"📐 Size: <b>{item['sqm']:,.0f} m²</b>\n"
        f"⚡ Rate: <b>R {item['rate_sqm']:,.2f} / m²</b>\n"
        f"📉 Below median: <b>{below_median:.1f}%</b>\n"
        f"🏆 Percentile: <b>{percentile:.2f}%</b> (#{rank_num} of {total_clean})\n\n"
        f"🔗 <a href=\"{url}\">View on Property24</a>"
    )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            print(f"❌ Telegram rejected alert for {item['id']}: {payload}")
            return False
        print(f"✅ Telegram alert sent once for listing {item['id']}")
        return True
    except Exception as exc:
        print(f"❌ Telegram alert failed for listing {item['id']}: {exc}")
        return False


def scrape_all_pages(session, search_name, base_url):
    clean_url = re.sub(r"/p\d+/?$", "", base_url.rstrip("/"))
    listings_by_id = {}
    reported_total = None
    pages_scraped = 0
    page = 1
    previous_page_url = "https://www.property24.com/"

    while True:
        page_url = clean_url if page == 1 else f"{clean_url}/p{page}"
        print(f"Scraping {search_name} page {page}: {page_url}")

        response = fetch_page(session, page_url, page, referer=previous_page_url)
        if response.status_code in (404, 410):
            print(f"Reached the end at page {page} (HTTP {response.status_code}).")
            break
        if response.status_code != 200:
            raise RuntimeError(f"Property24 returned HTTP {response.status_code} on page {page}.")

        soup = BeautifulSoup(response.text, "html.parser")
        if page == 1:
            reported_total = extract_reported_total(soup)
            if reported_total is not None:
                print(f"Property24 reports {reported_total:,} results.")

        tiles = find_listing_tiles(soup)
        if not tiles:
            print(f"No listing cards found on page {page}; pagination complete.")
            break

        ids_before_page = set(listings_by_id.keys())
        page_ids = set()
        for tile in tiles:
            item = parse_listing_tile(tile, search_name)
            if not item:
                continue
            page_ids.add(item["id"])
            listings_by_id[item["id"]] = item

        if not page_ids:
            print(f"No usable listing IDs found on page {page}; pagination complete.")
            break

        # Stop if Property24 repeats the previous/last result page.
        new_ids = page_ids - ids_before_page
        if page > 1 and not new_ids:
            print(f"Page {page} repeated previously seen listings; pagination complete.")
            break

        pages_scraped += 1
        previous_page_url = page_url
        print(f"Page {page}: {len(page_ids)} cards, {len(listings_by_id)} unique listings so far.")

        if reported_total is not None and len(listings_by_id) >= reported_total:
            print("Captured the full Property24 reported result count.")
            break

        page += 1
        if page > 500:
            raise RuntimeError("Pagination exceeded 500 pages; stopped as a safety guard.")

        time.sleep(random.uniform(*PAGE_DELAY_SECONDS))

    return list(listings_by_id.values()), reported_total, pages_scraped


def run_scraper():
    init_db()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    session = build_session()

    summaries = []

    try:
        for search in PRESET_SEARCHES:
            search_name = search["name"]
            base_url = search["url"]
            print(f"\n--- Full-market scrape: {search_name} ---")

            # History as it existed BEFORE this scrape. This defines genuinely new listings.
            c.execute(
                "SELECT id, alerted, first_seen_qualifies FROM listing_history WHERE area = ?",
                (search_name,),
            )
            existing_history = {
                row[0]: {"alerted": int(row[1] or 0), "first_seen_qualifies": int(row[2] or 0)}
                for row in c.fetchall()
            }

            all_items, reported_total, pages_scraped = scrape_all_pages(
                session, search_name, base_url
            )
            if not all_items:
                raise RuntimeError(f"No listings were captured for {search_name}.")

            total_raw = len(all_items)
            clean_items, real_min, real_max, real_median = clean_area_items(all_items)
            clean_items.sort(key=lambda x: x["rate_sqm"])
            total_clean = len(clean_items)

            deal_count = max(1, int(np.ceil(total_clean * DEAL_PERCENT / 100))) if total_clean else 0
            deal_items = clean_items[:deal_count]
            deal_ids = {x["id"] for x in deal_items}
            bottom_5_cutoff = deal_items[-1]["rate_sqm"] if deal_items else None

            # Replace current snapshot, but NEVER delete permanent listing_history.
            c.execute("DELETE FROM raw_listings WHERE area = ?", (search_name,))
            c.executemany(
                """
                INSERT OR REPLACE INTO raw_listings
                    (id, area, title, price, sqm, rate_sqm, url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        x["id"],
                        x["area"],
                        x["title"],
                        x["price"],
                        x["sqm"],
                        x["rate_sqm"],
                        x["url"],
                    )
                    for x in all_items
                ],
            )

            now = utc_now()
            for item in all_items:
                if item["id"] not in existing_history:
                    c.execute(
                        """
                        INSERT OR IGNORE INTO listing_history
                            (id, area, first_seen_at, last_seen_at, first_seen_qualifies, alerted)
                        VALUES (?, ?, ?, ?, ?, 0)
                        """,
                        (
                            item["id"],
                            search_name,
                            now,
                            now,
                            1 if item["id"] in deal_ids else 0,
                        ),
                    )
                else:
                    c.execute(
                        "UPDATE listing_history SET last_seen_at = ? WHERE id = ?",
                        (now, item["id"]),
                    )

            c.execute(
                """
                INSERT INTO area_stats
                    (area, total_raw, total_clean, reported_total, pages_scraped,
                     real_min, real_max, real_median, bottom_5_cutoff, last_scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(area) DO UPDATE SET
                    total_raw = excluded.total_raw,
                    total_clean = excluded.total_clean,
                    reported_total = excluded.reported_total,
                    pages_scraped = excluded.pages_scraped,
                    real_min = excluded.real_min,
                    real_max = excluded.real_max,
                    real_median = excluded.real_median,
                    bottom_5_cutoff = excluded.bottom_5_cutoff,
                    last_scraped_at = excluded.last_scraped_at
                """,
                (
                    search_name,
                    total_raw,
                    total_clean,
                    reported_total,
                    pages_scraped,
                    real_min,
                    real_max,
                    real_median,
                    bottom_5_cutoff,
                    now,
                ),
            )
            conn.commit()

            # Alert only properties that qualified when they were FIRST discovered.
            # Failed Telegram sends are retried later, but successful sends are never duplicated.
            candidates = []
            for rank_num, item in enumerate(clean_items, start=1):
                if item["id"] not in deal_ids:
                    continue
                if item["id"] not in existing_history:
                    candidates.append((rank_num, item))
                else:
                    hist = existing_history[item["id"]]
                    if hist["first_seen_qualifies"] == 1 and hist["alerted"] == 0:
                        candidates.append((rank_num, item))

            for rank_num, item in candidates:
                percentile = rank_num / total_clean * 100 if total_clean else 100.0
                if send_telegram_alert(
                    item,
                    search_name,
                    real_median,
                    rank_num,
                    total_clean,
                    percentile,
                ):
                    c.execute("UPDATE listing_history SET alerted = 1 WHERE id = ?", (item["id"],))
                    conn.commit()

            print(
                f"{search_name}: {total_raw:,} total | {total_clean:,} valid | "
                f"median R {real_median:,.2f}/m² | lowest {DEAL_PERCENT}% = {deal_count} listings"
            )

            summaries.append(
                {
                    "area": search_name,
                    "total_raw": total_raw,
                    "total_clean": total_clean,
                    "reported_total": reported_total,
                    "pages_scraped": pages_scraped,
                    "deal_count": deal_count,
                }
            )

    finally:
        session.close()
        conn.close()

    return summaries


if __name__ == "__main__":
    run_scraper()
