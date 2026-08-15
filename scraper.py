import os
import re
import sqlite3
import requests
import numpy as np
from bs4 import BeautifulSoup

DB_NAME = "properties.db"

# 🛠️ AREA PRESET CONFIGURATION
PRESET_SEARCHES = [
    {
        "name": "Morningside",
        "url": "https://www.property24.com/apartments-for-sale/morningside/sandton/gauteng/4258"
    }
]


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Table for raw scraped listings
    c.execute("""
        CREATE TABLE IF NOT EXISTS raw_listings (
            id TEXT PRIMARY KEY,
            area TEXT,
            title TEXT,
            price REAL,
            sqm REAL,
            rate_sqm REAL,
            url TEXT
        )
    """)

    # Table for area statistics
    c.execute("""
        CREATE TABLE IF NOT EXISTS area_stats (
            area TEXT PRIMARY KEY,
            total_raw INTEGER,
            total_clean INTEGER,
            real_min REAL,
            real_max REAL,
            top_3_percentile REAL
        )
    """)

    # ---------------------------------------------------------
    # DATABASE MIGRATION
    # CREATE TABLE IF NOT EXISTS does NOT modify old tables.
    # This checks the existing schema and adds missing columns.
    # ---------------------------------------------------------
    c.execute("PRAGMA table_info(area_stats)")
    area_stats_columns = [row[1] for row in c.fetchall()]

    if "total_raw" not in area_stats_columns:
        c.execute("ALTER TABLE area_stats ADD COLUMN total_raw INTEGER")

    if "total_clean" not in area_stats_columns:
        c.execute("ALTER TABLE area_stats ADD COLUMN total_clean INTEGER")

    if "real_min" not in area_stats_columns:
        c.execute("ALTER TABLE area_stats ADD COLUMN real_min REAL")

    if "real_max" not in area_stats_columns:
        c.execute("ALTER TABLE area_stats ADD COLUMN real_max REAL")

    if "top_3_percentile" not in area_stats_columns:
        c.execute(
            "ALTER TABLE area_stats ADD COLUMN top_3_percentile REAL"
        )

    conn.commit()
    conn.close()


def clean_area_data(rates):
    """
    2-Pass Density Isolation Engine:

    1. Physical reality boundary filter
       R6,500/m² to R80,000/m².

    2. Density gap isolation
       Filters isolated stray points on the low/high ends.
    """

    if not rates:
        return [], 0, 0, 0

    if len(rates) < 5:
        clean_rates = sorted(rates)

        real_min = min(clean_rates)
        real_max = max(clean_rates)

        # Cheapest 3% threshold
        top_3_thresh = float(np.percentile(clean_rates, 3))

        return clean_rates, real_min, real_max, top_3_thresh

    rates = sorted(rates)

    # ---------------------------------------------------------
    # PASS 1: PHYSICAL REALITY FILTER
    # ---------------------------------------------------------
    filtered = [
        r for r in rates
        if 6500 <= r <= 80000
    ]

    # If everything gets filtered, fall back to original data
    if not filtered:
        filtered = rates.copy()

    # If only one value remains, np.diff is empty
    if len(filtered) == 1:
        return (
            filtered,
            filtered[0],
            filtered[0],
            filtered[0]
        )

    # ---------------------------------------------------------
    # PASS 2: DENSITY GAP DETECTION
    # ---------------------------------------------------------
    diffs = np.diff(filtered)

    median_diff = (
        float(np.median(diffs))
        if len(diffs) > 0
        else 1.0
    )

    # Protect against a zero median difference
    if median_diff <= 0:
        positive_diffs = [
            d for d in diffs
            if d > 0
        ]

        median_diff = (
            float(np.median(positive_diffs))
            if positive_diffs
            else 1.0
        )

    # ---------------------------------------------------------
    # LOW END CUTOFF
    # ---------------------------------------------------------
    low_idx = 0

    for i in range(min(5, len(diffs))):
        gap_threshold = max(
            1500,
            median_diff * 4
        )

        if diffs[i] > gap_threshold:
            low_idx = i + 1

    # ---------------------------------------------------------
    # HIGH END CUTOFF
    # ---------------------------------------------------------
    high_idx = len(filtered)

    start_index = len(diffs) - 1
    stop_index = max(len(diffs) - 5, -1)

    for i in range(
        start_index,
        stop_index,
        -1
    ):
        gap_threshold = max(
            3000,
            median_diff * 4
        )

        if diffs[i] > gap_threshold:
            high_idx = i + 1
            break

    clean_rates = filtered[
        low_idx:high_idx
    ]

    if not clean_rates:
        clean_rates = filtered

    real_min = float(min(clean_rates))
    real_max = float(max(clean_rates))

    # ---------------------------------------------------------
    # TOP 3% BARGAIN THRESHOLD
    #
    # Because lower R/m² = cheaper/better,
    # we want the 3rd percentile, NOT the 97th.
    # ---------------------------------------------------------
    top_3_thresh = float(
        np.percentile(
            clean_rates,
            3
        )
    )

    return (
        clean_rates,
        real_min,
        real_max,
        top_3_thresh
    )


def send_telegram_alert(
    title,
    area,
    price,
    sqm,
    rate_sqm,
    true_percentile,
    rank_num,
    total_clean,
    url
):
    token = os.getenv(
        "TELEGRAM_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:
        print(
            "⚠️ Missing Telegram credentials."
        )
        return

    message = (
        f"🔥 *TOP 3% BARGAIN ALERT!*\n\n"
        f"📍 *Title:* {title}\n"
        f"🏷️ *Area:* {area}\n"
        f"🏆 *Value Rank:* "
        f"Top {true_percentile:.1f}% "
        f"(#{rank_num} of {total_clean} in area)\n"
        f"💰 *Price:* R {price:,.0f}\n"
        f"📐 *Size:* {sqm:.0f} m²\n"
        f"⚡ *Rate:* R {rate_sqm:,.2f} / m²\n\n"
        f"🔗 [View Listing on Property24]({url})"
    )

    api_url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }

    try:
        res = requests.post(
            api_url,
            json=payload,
            timeout=10
        )

        res.raise_for_status()

        print(
            f"✅ Alert sent for: {title}"
        )

    except Exception as e:
        print(
            f"❌ Failed to send Telegram alert: {e}"
        )


def run_scraper():
    init_db()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/115.0.0.0 "
            "Safari/537.36"
        )
    }

    try:
        for search in PRESET_SEARCHES:

            search_name = search["name"]
            base_url = search["url"]

            print(
                f"\n--- Scraping All Pages for Area: "
                f"{search_name} ---"
            )

            clean_url = re.sub(
                r"/p\d+/?$",
                "",
                base_url.rstrip("/")
            )

            page = 1
            max_pages = 10

            area_listings = []

            while page <= max_pages:

                page_url = (
                    clean_url
                    if page == 1
                    else f"{clean_url}/p{page}"
                )

                print(
                    f"Scraping Page {page}..."
                )

                try:
                    res = requests.get(
                        page_url,
                        headers=headers,
                        timeout=15
                    )

                    if res.status_code != 200:
                        print(
                            f"Stopping. HTTP "
                            f"{res.status_code} "
                            f"on page {page}."
                        )
                        break

                    soup = BeautifulSoup(
                        res.text,
                        "html.parser"
                    )

                    tiles = soup.find_all(
                        "div",
                        class_=re.compile(
                            r"p24_tile|js_resultTile"
                        )
                    )

                    if not tiles:
                        print(
                            "No listing tiles found. "
                            "Stopping pagination."
                        )
                        break

                    for tile in tiles:

                        # -----------------------------
                        # URL / LISTING ID
                        # -----------------------------
                        link_tag = tile.find(
                            "a",
                            href=True
                        )

                        if not link_tag:
                            continue

                        href = link_tag["href"]

                        full_url = (
                            href
                            if href.startswith("http")
                            else
                            f"https://www.property24.com{href}"
                        )

                        listing_id_match = re.search(
                            r"/(\d+)(?:[/?#]|$)",
                            href
                        )

                        listing_id = (
                            listing_id_match.group(1)
                            if listing_id_match
                            else full_url
                        )

                        # -----------------------------
                        # TITLE
                        # -----------------------------
                        title_tag = (
                            tile.find(
                                "span",
                                class_="p24_title"
                            )
                            or
                            tile.find(
                                "div",
                                class_="p24_title"
                            )
                        )

                        title = (
                            title_tag.get_text(
                                " ",
                                strip=True
                            )
                            if title_tag
                            else "Property Listing"
                        )

                        # -----------------------------
                        # PRICE
                        # -----------------------------
                        price_tag = (
                            tile.find(
                                "div",
                                class_="p24_price"
                            )
                            or
                            tile.find(
                                "span",
                                class_="p24_price"
                            )
                        )

                        if not price_tag:
                            continue

                        price_digits = re.sub(
                            r"[^\d]",
                            "",
                            price_tag.get_text()
                        )

                        if not price_digits:
                            continue

                        price = float(
                            price_digits
                        )

                        # -----------------------------
                        # FLOOR SIZE
                        # -----------------------------
                        sqm_tag = (
                            tile.find(
                                "span",
                                title="Floor Size"
                            )
                            or
                            tile.find(
                                "span",
                                class_="p24_size"
                            )
                            or
                            tile.find(
                                "span",
                                title="Erf Size"
                            )
                        )

                        sqm = None

                        if sqm_tag:
                            sqm_digits = re.sub(
                                r"[^\d.]",
                                "",
                                sqm_tag.get_text()
                            )

                            if sqm_digits:
                                try:
                                    sqm = float(
                                        sqm_digits
                                    )
                                except ValueError:
                                    sqm = None

                        if sqm is None:
                            tile_text = tile.get_text(
                                " ",
                                strip=True
                            )

                            sqm_match = re.search(
                                r"([\d,.]+)\s*m[²2]",
                                tile_text,
                                flags=re.IGNORECASE
                            )

                            if sqm_match:
                                sqm_text = (
                                    sqm_match
                                    .group(1)
                                    .replace(",", "")
                                )

                                try:
                                    sqm = float(
                                        sqm_text
                                    )
                                except ValueError:
                                    sqm = None

                        if not sqm or sqm <= 0:
                            continue

                        # -----------------------------
                        # RATE PER M²
                        # -----------------------------
                        rate_sqm = (
                            price / sqm
                        )

                        area_listings.append(
                            {
                                "id": listing_id,
                                "area": search_name,
                                "title": title,
                                "price": price,
                                "sqm": sqm,
                                "rate_sqm": rate_sqm,
                                "url": full_url
                            }
                        )

                    page += 1

                except Exception as e:
                    print(
                        f"Error scraping "
                        f"{page_url}: {e}"
                    )
                    break

            if not area_listings:
                print(
                    f"No usable listings found "
                    f"for {search_name}."
                )
                continue

            # -------------------------------------------------
            # REMOVE DUPLICATES FROM CURRENT SCRAPE
            # -------------------------------------------------
            unique_listings = {}

            for item in area_listings:
                unique_listings[
                    item["id"]
                ] = item

            area_listings = list(
                unique_listings.values()
            )

            # -------------------------------------------------
            # SAVE RAW LISTINGS
            # -------------------------------------------------
            for item in area_listings:
                c.execute(
                    """
                    INSERT OR REPLACE INTO raw_listings
                    (
                        id,
                        area,
                        title,
                        price,
                        sqm,
                        rate_sqm,
                        url
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["area"],
                        item["title"],
                        item["price"],
                        item["sqm"],
                        item["rate_sqm"],
                        item["url"]
                    )
                )

            conn.commit()

            # -------------------------------------------------
            # DENSITY CLEANING
            # -------------------------------------------------
            rates = [
                x["rate_sqm"]
                for x in area_listings
            ]

            (
                clean_rates,
                real_min,
                real_max,
                top_3_thresh
            ) = clean_area_data(
                rates
            )

            # -------------------------------------------------
            # CREATE CLEAN ITEM SET
            # -------------------------------------------------
            clean_area_items = [
                x
                for x in area_listings
                if (
                    real_min
                    <= x["rate_sqm"]
                    <= real_max
                )
            ]

            clean_area_items.sort(
                key=lambda x: x[
                    "rate_sqm"
                ]
            )

            total_clean = len(
                clean_area_items
            )

            # -------------------------------------------------
            # SAVE AREA STATS
            #
            # Use UPSERT rather than INSERT OR REPLACE.
            # This is safer and clearer.
            # -------------------------------------------------
            c.execute(
                """
                INSERT INTO area_stats
                (
                    area,
                    total_raw,
                    total_clean,
                    real_min,
                    real_max,
                    top_3_percentile
                )
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(area)
                DO UPDATE SET
                    total_raw = excluded.total_raw,
                    total_clean = excluded.total_clean,
                    real_min = excluded.real_min,
                    real_max = excluded.real_max,
                    top_3_percentile =
                        excluded.top_3_percentile
                """,
                (
                    search_name,
                    len(area_listings),
                    total_clean,
                    real_min,
                    real_max,
                    top_3_thresh
                )
            )

            conn.commit()

            print(
                f"{search_name}: "
                f"{len(area_listings)} raw | "
                f"{total_clean} clean | "
                f"R{real_min:,.2f} - "
                f"R{real_max:,.2f}/m² | "
                f"Top 3% ceiling "
                f"R{top_3_thresh:,.2f}/m²"
            )

            # -------------------------------------------------
            # TELEGRAM ALERTS
            # -------------------------------------------------
            for idx, item in enumerate(
                clean_area_items
            ):

                rank_num = idx + 1

                true_percentile = (
                    (
                        rank_num /
                        total_clean
                    ) * 100
                    if total_clean > 0
                    else 100.0
                )

                if (
                    item["rate_sqm"]
                    <= top_3_thresh
                ):
                    send_telegram_alert(
                        item["title"],
                        search_name,
                        item["price"],
                        item["sqm"],
                        item["rate_sqm"],
                        true_percentile,
                        rank_num,
                        total_clean,
                        item["url"]
                    )

    finally:
        conn.close()


if __name__ == "__main__":
    run_scraper()
