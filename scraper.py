import os
import re
import time
import sqlite3
import requests
import numpy as np
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "properties.db")

MORNINGSIDE_URL = "https://www.property24.com/apartments-for-sale/morningside/sandton/gauteng/4258"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # 🧹 Reset tables for a clean slate
    c.execute("DROP TABLE IF EXISTS raw_listings")
    c.execute("DROP TABLE IF EXISTS area_stats")

    # Table for all scraped property details
    c.execute('''
        CREATE TABLE raw_listings (
            id TEXT PRIMARY KEY,
            title TEXT,
            price REAL,
            sqm REAL,
            bedrooms REAL,
            bathrooms REAL,
            rate_sqm REAL,
            url TEXT
        )
    ''')
    
    # Table for area metrics
    c.execute('''
        CREATE TABLE area_stats (
            id INTEGER PRIMARY KEY,
            total_raw INTEGER,
            total_clean INTEGER,
            real_min REAL,
            real_max REAL,
            median_rate REAL,
            top_5_percentile REAL
        )
    ''')

    # Table to track Telegram alerts (prevents duplicates)
    c.execute('''
        CREATE TABLE IF NOT EXISTS sent_alerts (
            listing_id TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def clean_area_data(rates):
    if len(rates) < 3:
        return rates, min(rates) if rates else 0, max(rates) if rates else 0, 0, 0

    rates = sorted(rates)
    
    # Pass 1: Physical Reality Filter (R6,500/m² to R80,000/m²)
    filtered = [r for r in rates if 6500 <= r <= 80000]
    if not filtered:
        filtered = rates

    # Pass 2: Density / Isolation Gap Detection
    diffs = np.diff(filtered)
    median_diff = np.median(diffs) if len(diffs) > 0 else 1.0

    low_idx = 0
    for i in range(min(5, len(diffs))):
        if diffs[i] > max(1500, median_diff * 4):
            low_idx = i + 1

    high_idx = len(filtered)
    for i in range(len(diffs) - 1, max(len(diffs) - 5, 0), -1):
        if diffs[i] > max(3000, median_diff * 4):
            high_idx = i + 1
            break

    clean_rates = filtered[low_idx:high_idx]
    if not clean_rates:
        clean_rates = filtered

    real_min = min(clean_rates)
    real_max = max(clean_rates)
    median_rate = float(np.median(clean_rates))
    
    # Top 5% Threshold (Lowest 5% of valid rates)
    top_5_thresh = float(np.percentile(clean_rates, 5))

    return clean_rates, real_min, real_max, median_rate, top_5_thresh

def is_alert_already_sent(listing_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM sent_alerts WHERE listing_id=?", (str(listing_id),))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_alert_as_sent(listing_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO sent_alerts (listing_id) VALUES (?)", (str(listing_id),))
    conn.commit()
    conn.close()

def send_telegram_alert(listing_id, price, sqm, beds, baths, rate_sqm, true_percentile, pct_below_median, url):
    if is_alert_already_sent(listing_id):
        print(f"⏩ Alert already sent for ID {listing_id}. Skipping.")
        return

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️ Missing Telegram credentials. Cannot send alert.")
        return

    # Calculate dynamic bracket for the title (e.g., 1.24% becomes Top 2%)
    bracket_percentile = int(np.ceil(true_percentile))
    bracket_percentile = max(1, bracket_percentile) # Ensure it never says Top 0%

    message = (
        f"🔥 *Top {bracket_percentile}% apartment*\n\n"
        f"🏆 *Percentile Rank:* Top {true_percentile:.2f}%\n"
        f"📉 *Discount:* {pct_below_median:.1f}% below median R/m²\n\n"
        f"💰 *Price:* R {price:,.0f}\n"
        f"📐 *Size:* {sqm:.0f} m²\n"
        f"⚡ *Rate:* R {rate_sqm:,.2f} / m²\n"
        f"🛏️ *Bedrooms:* {int(beds) if beds else 'N/A'}\n"
        f"🛁 *Bathrooms:* {float(baths) if baths else 'N/A'}\n\n"
        f"🔗 [View Listing]({url})"
    )
    
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(api_url, json=payload, timeout=5)
        res.raise_for_status()
        mark_alert_as_sent(listing_id)
        print(f"✅ Telegram alert sent for Top {bracket_percentile}% deal (ID: {listing_id})")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def run_scraper(max_pages=50):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    print(f"\n--- Scraping Morningside Apartments ---")

    page = 1
    listings = []

    while page <= max_pages:
        page_url = MORNINGSIDE_URL if page == 1 else f"{MORNINGSIDE_URL}/p{page}"
        print(f"Fetching page {page}...")
        
        try:
            res = requests.get(page_url, headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"Reached end of pages or hit a wall (HTTP {res.status_code}). Stopping.")
                break

            soup = BeautifulSoup(res.text, "html.parser")
            tiles = soup.find_all("div", class_=re.compile("p24_tile|js_resultTile"))
            
            if not tiles:
                print(f"No property tiles found on page {page}. Scraping complete.")
                break

            for tile in tiles:
                link_tag = tile.find("a", href=True)
                if not link_tag:
                    continue
                href = link_tag['href']
                full_url = href if href.startswith("http") else f"https://www.property24.com{href}"
                
                listing_id_match = re.search(r'/(\d+)$', href)
                listing_id = listing_id_match.group(1) if listing_id_match else href

                title_tag = tile.find("span", class_="p24_title") or tile.find("div", class_="p24_title")
                title = title_tag.text.strip() if title_tag else "Apartment"

                price_tag = tile.find("div", class_="p24_price") or tile.find("span", class_="p24_price")
                if not price_tag:
                    continue
                price_digits = re.sub(r'[^\d]', '', price_tag.text)
                if not price_digits:
                    continue
                price = float(price_digits)

                # Extract Size
                sqm_tag = tile.find("span", title="Erf Size") or tile.find("span", title="Floor Size") or tile.find("span", class_="p24_size")
                sqm = None
                if not sqm_tag:
                    sqm_match = re.search(r'(\d+)\s*m²', tile.text)
                    if sqm_match: sqm = float(sqm_match.group(1))
                else:
                    sqm_digits = re.sub(r'[^\d]', '', sqm_tag.text)
                    if sqm_digits: sqm = float(sqm_digits)

                if not sqm or sqm <= 0:
                    continue
                
                # Extract Bedrooms
                bed_tag = tile.find("span", title="Bedrooms")
                beds = float(re.sub(r'[^\d\.]', '', bed_tag.text)) if bed_tag and re.search(r'\d', bed_tag.text) else None
                
                # Extract Bathrooms
                bath_tag = tile.find("span", title="Bathrooms")
                baths = float(re.sub(r'[^\d\.]', '', bath_tag.text)) if bath_tag and re.search(r'\d', bath_tag.text) else None

                rate_sqm = price / sqm

                listings.append({
                    "id": str(listing_id),
                    "title": title,
                    "price": price,
                    "sqm": sqm,
                    "bedrooms": beds,
                    "bathrooms": baths,
                    "rate_sqm": rate_sqm,
                    "url": full_url
                })

            page += 1
            time.sleep(0.5)  # 500ms delay to prevent Property24 timeouts

        except Exception as e:
            print(f"Error scraping page {page}: {e}")
            break

    if not listings:
        print("No valid listings scraped.")
        conn.close()
        return

    raw_count = len(listings)
    print(f"Scraped {raw_count} total raw listings.")

    for item in listings:
        c.execute(
            "INSERT OR REPLACE INTO raw_listings (id, title, price, sqm, bedrooms, bathrooms, rate_sqm, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["title"], item["price"], item["sqm"], item["bedrooms"], item["bathrooms"], item["rate_sqm"], item["url"])
        )
    conn.commit()

    rates = [x["rate_sqm"] for x in listings]
    clean_rates, real_min, real_max, median_rate, top_5_thresh = clean_area_data(rates)

    valid_items = [x for x in listings if real_min <= x["rate_sqm"] <= real_max]
    valid_items.sort(key=lambda x: x["rate_sqm"])
    total_clean = len(valid_items)

    c.execute(
        "INSERT INTO area_stats (total_raw, total_clean, real_min, real_max, median_rate, top_5_percentile) VALUES (?, ?, ?, ?, ?, ?)",
        (raw_count, total_clean, real_min, real_max, median_rate, top_5_thresh)
    )
    conn.commit()

    # Calculate True Top 5% Limit
    top_5_count = int(np.ceil(total_clean * 0.05))

    # Evaluate new Top 5% deals and send alerts
    for idx, item in enumerate(valid_items[:top_5_count]):
        rank_num = idx + 1
        true_percentile = (rank_num / total_clean) * 100 if total_clean > 0 else 100.0
        pct_below_median = ((median_rate - item["rate_sqm"]) / median_rate) * 100 if median_rate > 0 else 0.0

        send_telegram_alert(
            listing_id=item["id"],
            price=item["price"],
            sqm=item["sqm"],
            beds=item["bedrooms"],
            baths=item["bathrooms"],
            rate_sqm=item["rate_sqm"],
            true_percentile=true_percentile,
            pct_below_median=pct_below_median,
            url=item["url"]
        )

    conn.close()

if __name__ == "__main__":
    run_scraper()
