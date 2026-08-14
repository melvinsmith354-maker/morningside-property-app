import os
import re
import sqlite3
import requests
import numpy as np
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "properties.db")

MORNINGSIDE_URL = "https://www.property24.com/for-sale/morningside/sandton/gauteng/4258"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_listings (
            id TEXT PRIMARY KEY,
            area TEXT,
            suburb TEXT,
            title TEXT,
            price REAL,
            sqm REAL,
            rate_sqm REAL,
            url TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS area_stats (
            suburb TEXT PRIMARY KEY,
            total_raw INTEGER,
            total_clean INTEGER,
            real_min REAL,
            real_max REAL,
            median_rate REAL,
            top_2_percentile REAL
        )
    ''')
    conn.commit()
    conn.close()

def clean_area_data(rates):
    if len(rates) < 3:
        return rates, min(rates) if rates else 0, max(rates) if rates else 0, 0, 0

    rates = sorted(rates)
    
    filtered = [r for r in rates if 6500 <= r <= 80000]
    if not filtered:
        filtered = rates

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
    top_2_thresh = float(np.percentile(clean_rates, 2))

    return clean_rates, real_min, real_max, median_rate, top_2_thresh

def send_telegram_alert(title, suburb, price, sqm, rate_sqm, true_percentile, rank_num, total_clean, pct_below_median, url):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️ Missing Telegram credentials.")
        return

    message = (
        f"🔥 *TOP 2% BARGAIN ALERT! (Morningside)*\n\n"
        f"📍 *Title:* {title}\n"
        f"🏆 *Value Rank:* **Top {true_percentile:.1f}%** (#{rank_num} of {total_clean})\n"
        f"📉 *Discount:* **{pct_below_median:.1f}% below median**\n"
        f"💰 *Price:* R {price:,.0f}\n"
        f"📐 *Size:* {sqm:.0f} m²\n"
        f"⚡ *Rate:* R {rate_sqm:,.2f} / m²\n\n"
        f"🔗 [View Listing on Property24]({url})"
    )
    
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(api_url, json=payload, timeout=10)
        res.raise_for_status()
        print(f"✅ Alert sent for: {title}")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def run_scraper():
    init_db()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    suburb_name = "Morningside"
    base_url = MORNINGSIDE_URL.rstrip('/')
    
    print(f"\n--- Scraping Morningside ---")

    page = 1
    max_pages = 15  # 🚀 Increased to 15 to capture ALL 350+ listings across Morningside
    listings = []

    while page <= max_pages:
        page_url = base_url if page == 1 else f"{base_url}/p{page}"
        print(f"Scraping Page {page}...")
        
        try:
            res = requests.get(page_url, headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"Page {page} returned status {res.status_code}")
                break

            soup = BeautifulSoup(res.text, "html.parser")
            tiles = soup.find_all("div", class_=re.compile("p24_tile|js_resultTile"))
            
            if not tiles:
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
                title = title_tag.text.strip() if title_tag else "Property Listing"

                price_tag = tile.find("div", class_="p24_price") or tile.find("span", class_="p24_price")
                if not price_tag:
                    continue
                price_digits = re.sub(r'[^\d]', '', price_tag.text)
                if not price_digits:
                    continue
                price = float(price_digits)

                sqm_tag = tile.find("span", title="Erf Size") or tile.find("span", title="Floor Size") or tile.find("span", class_="p24_size")
                if not sqm_tag:
                    sqm_match = re.search(r'(\d+)\s*m²', tile.text)
                    sqm = float(sqm_match.group(1)) if sqm_match else None
                else:
                    sqm_digits = re.sub(r'[^\d]', '', sqm_tag.text)
                    sqm = float(sqm_digits) if sqm_digits else None

                if not sqm or sqm <= 0:
                    continue

                rate_sqm = price / sqm

                listings.append({
                    "id": listing_id,
                    "area": "Sandton",
                    "suburb": suburb_name,
                    "title": title,
                    "price": price,
                    "sqm": sqm,
                    "rate_sqm": rate_sqm,
                    "url": full_url
                })

            page += 1

        except Exception as e:
            print(f"Error scraping page {page}: {e}")
            break

    if not listings:
        print("No listings found!")
        conn.close()
        return

    raw_count = len(listings)

    for item in listings:
        c.execute(
            "INSERT OR REPLACE INTO raw_listings (id, area, suburb, title, price, sqm, rate_sqm, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["area"], item["suburb"], item["title"], item["price"], item["sqm"], item["rate_sqm"], item["url"])
        )
    conn.commit()

    rates = [x["rate_sqm"] for x in listings]
    clean_rates, real_min, real_max, median_rate, top_2_thresh = clean_area_data(rates)

    valid_items = [x for x in listings if real_min <= x["rate_sqm"] <= real_max]
    valid_items.sort(key=lambda x: x["rate_sqm"])
    total_clean = len(valid_items)

    c.execute(
        "INSERT OR REPLACE INTO area_stats (suburb, total_raw, total_clean, real_min, real_max, median_rate, top_2_percentile) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (suburb_name, raw_count, total_clean, real_min, real_max, median_rate, top_2_thresh)
    )
    conn.commit()

    for idx, item in enumerate(valid_items):
        rank_num = idx + 1
        true_percentile = (rank_num / total_clean) * 100 if total_clean > 0 else 100.0
        pct_below_median = ((median_rate - item["rate_sqm"]) / median_rate) * 100 if median_rate > 0 else 0.0

        if item["rate_sqm"] <= top_2_thresh:
            send_telegram_alert(
                item["title"], suburb_name, item["price"], item["sqm"], 
                item["rate_sqm"], true_percentile, rank_num, total_clean, pct_below_median, item["url"]
            )

    conn.close()

if __name__ == "__main__":
    run_scraper()
