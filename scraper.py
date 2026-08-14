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
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_listings (
            id TEXT PRIMARY KEY,
            area TEXT,
            title TEXT,
            price REAL,
            sqm REAL,
            rate_sqm REAL,
            url TEXT
        )
    ''')
    # Table for area statistics
    c.execute('''
        CREATE TABLE IF NOT EXISTS area_stats (
            area TEXT PRIMARY KEY,
            total_raw INTEGER,
            total_clean INTEGER,
            real_min REAL,
            real_max REAL,
            top_3_percentile REAL
        )
    ''')
    conn.commit()
    conn.close()

def clean_area_data(rates):
    """
    2-Pass Density Isolation Engine:
    1. Physical reality boundary filter (R6,500/m² to R80,000/m²).
    2. Density gap isolation (filters isolated stray points on low/high ends).
    """
    if len(rates) < 5:
        return rates, min(rates) if rates else 0, max(rates) if rates else 0, min(rates) if rates else 0

    rates = sorted(rates)
    
    # Pass 1: Physical Reality Filter
    filtered = [r for r in rates if 6500 <= r <= 80000]
    if not filtered:
        filtered = rates

    # Pass 2: Isolation Gap Detection
    diffs = np.diff(filtered)
    median_diff = np.median(diffs) if len(diffs) > 0 else 1.0

    # Low End Cutoff
    low_idx = 0
    for i in range(min(5, len(diffs))):
        if diffs[i] > max(1500, median_diff * 4):
            low_idx = i + 1

    # High End Cutoff
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
    
    # Calculate 3rd percentile threshold (Top 3%)
    top_3_thresh = float(np.percentile(clean_rates, 3))

    return clean_rates, real_min, real_max, top_3_thresh

def send_telegram_alert(title, area, price, sqm, rate_sqm, true_percentile, rank_num, total_clean, url):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️ Missing Telegram credentials.")
        return

    message = (
        f"🔥 *TOP 3% BARGAIN ALERT!*\n\n"
        f"📍 *Title:* {title}\n"
        f"🏷️ *Area:* {area}\n"
        f"🏆 *Value Rank:* **Top {true_percentile:.1f}%** (#{rank_num} of {total_clean} in area)\n"
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

    for search in PRESET_SEARCHES:
        search_name = search["name"]
        base_url = search["url"]
        
        print(f"\n--- Scraping All Pages for Area: {search_name} ---")
        clean_url = re.sub(r'/p\d+/?$', '', base_url.rstrip('/'))

        page = 1
        max_pages = 10
        area_listings = []

        while page <= max_pages:
            page_url = clean_url if page == 1 else f"{clean_url}/p{page}"
            print(f"Scraping Page {page}...")
            
            try:
                res = requests.get(page_url, headers=headers, timeout=10)
                if res.status_code != 200:
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

                    area_listings.append({
                        "id": listing_id,
                        "area": search_name,
                        "title": title,
                        "price": price,
                        "sqm": sqm,
                        "rate_sqm": rate_sqm,
                        "url": full_url
                    })

                page += 1

            except Exception as e:
                print(f"Error scraping {page_url}: {e}")
                break

        if not area_listings:
            continue

        # Save all raw listings to database
        for item in area_listings:
            c.execute(
                "INSERT OR REPLACE INTO raw_listings (id, area, title, price, sqm, rate_sqm, url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item["id"], item["area"], item["title"], item["price"], item["sqm"], item["rate_sqm"], item["url"])
            )
        conn.commit()

        # Run Density Cleaner on the FULL scraped area dataset
        rates = [x["rate_sqm"] for x in area_listings]
        clean_rates, real_min, real_max, top_3_thresh = clean_area_data(rates)

        # Update area statistics
        c.execute(
            "INSERT OR REPLACE INTO area_stats (area, total_raw, total_clean, real_min, real_max, top_3_percentile) VALUES (?, ?, ?, ?, ?, ?)",
            (search_name, len(rates), len(clean_rates), real_min, real_max, top_3_thresh)
        )
        conn.commit()

        # Filter valid items and sort by rate
        clean_area_items = [x for x in area_listings if real_min <= x["rate_sqm"] <= real_max]
        clean_area_items.sort(key=lambda x: x["rate_sqm"])
        total_clean = len(clean_area_items)

        # Send Telegram alerts ONLY for properties meeting the Top 3% threshold
        for idx, item in enumerate(clean_area_items):
            rank_num = idx + 1
            true_percentile = (rank_num / total_clean) * 100 if total_clean > 0 else 100.0

            if item["rate_sqm"] <= top_3_thresh:
                send_telegram_alert(
                    item["title"], search_name, item["price"], item["sqm"], 
                    item["rate_sqm"], true_percentile, rank_num, total_clean, item["url"]
                )

    conn.close()

if __name__ == "__main__":
    run_scraper()
