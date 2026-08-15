import os
import re
import time
import sqlite3
import requests
import numpy as np
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "properties.db")

PRESET_SEARCHES = [
    {"name": "Morningside", "url": "https://www.property24.com/apartments-for-sale/morningside/sandton/gauteng/4258"},
    {"name": "Bryanston", "url": "https://www.property24.com/apartments-for-sale/bryanston/sandton/gauteng/5176"},
    {"name": "Sandhurst", "url": "https://www.property24.com/apartments-for-sale/sandhurst/sandton/gauteng/5847"},
    {"name": "Sandton Central", "url": "https://www.property24.com/apartments-for-sale/sandton-central/sandton/gauteng/16732"},
    {"name": "Hyde Park", "url": "https://www.property24.com/apartments-for-sale/hyde-park/sandton/gauteng/5832"},
    {"name": "Hurlingham", "url": "https://www.property24.com/apartments-for-sale/hurlingham/sandton/gauteng/5860"},
    {"name": "Sandown", "url": "https://www.property24.com/apartments-for-sale/sandown/sandton/gauteng/5178"},
    {"name": "Benmore Gardens", "url": "https://www.property24.com/apartments-for-sale/benmore-gardens/sandton/gauteng/11001"},
    {"name": "Edenburg", "url": "https://www.property24.com/apartments-for-sale/edenburg/sandton/gauteng/4253"},
    {"name": "Houghton Estate", "url": "https://www.property24.com/apartments-for-sale/houghton-estate/johannesburg/gauteng/5926"},
    {"name": "Linden", "url": "https://www.property24.com/apartments-for-sale/linden/randburg/gauteng/5779"},
    {"name": "Illovo", "url": "https://www.property24.com/apartments-for-sale/illovo/sandton/gauteng/5833"},
    {"name": "Melrose", "url": "https://www.property24.com/apartments-for-sale/melrose/johannesburg/gauteng/5837"},
    {"name": "Woodmead", "url": "https://www.property24.com/apartments-for-sale/woodmead/sandton/gauteng/4288"},
    {"name": "Sunninghill", "url": "https://www.property24.com/apartments-for-sale/sunninghill/sandton/gauteng/4289"},
    {"name": "Waterfall", "url": "https://www.property24.com/apartments-for-sale/waterfall/midrand/gauteng/1535"}
]

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_listings (
            id TEXT PRIMARY KEY,
            suburb TEXT,
            title TEXT,
            price REAL,
            sqm REAL,
            bedrooms REAL,
            bathrooms REAL,
            rate_sqm REAL,
            url TEXT
        )
    ''')

    # 🛠️ AUTO-MIGRATION: Check if 'suburb' exists, if not, add it and assign old records to Morningside
    c.execute("PRAGMA table_info(raw_listings)")
    columns = [col[1] for col in c.fetchall()]
    if "suburb" not in columns:
        c.execute("ALTER TABLE raw_listings ADD COLUMN suburb TEXT")
        c.execute("UPDATE raw_listings SET suburb = 'Morningside' WHERE suburb IS NULL")
    
    c.execute("PRAGMA table_info(area_stats)")
    area_columns = [col[1] for col in c.fetchall()]
    if "suburb" not in area_columns or "id" in area_columns:
        c.execute("DROP TABLE IF EXISTS area_stats")

    c.execute('''
        CREATE TABLE IF NOT EXISTS area_stats (
            suburb TEXT PRIMARY KEY,
            total_raw INTEGER,
            total_clean INTEGER,
            real_min REAL,
            real_max REAL,
            median_rate REAL,
            top_5_percentile REAL
        )
    ''')

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

def send_telegram_alert(listing_id, suburb, title, price, sqm, beds, baths, rate_sqm, true_percentile, pct_below_median, url):
    if is_alert_already_sent(listing_id):
        print(f"⏩ Alert already sent for ID {listing_id}. Skipping.")
        return

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️ Missing Telegram credentials. Cannot send alert.")
        return

    bracket_percentile = int(np.ceil(true_percentile))
    bracket_percentile = max(1, bracket_percentile) 

    beds_txt = f"{int(beds)}" if beds is not None and float(beds).is_integer() else f"{beds}" if beds is not None else "N/A"
    baths_txt = f"{int(baths)}" if baths is not None and float(baths).is_integer() else f"{baths}" if baths is not None else "N/A"

    message = (
        f"🔥 *Top {bracket_percentile}% apartment ({suburb})*\n\n"
        f"🏆 *Percentile Rank:* Top {true_percentile:.2f}%\n"
        f"📉 *Discount:* {pct_below_median:.1f}% below median R/m²\n\n"
        f"💰 *Price:* R {price:,.0f}\n"
        f"📐 *Size:* {sqm:.0f} m²\n"
        f"⚡ *Rate:* R {rate_sqm:,.2f} / m²\n"
        f"🛏️ *Bedrooms:* {beds_txt}\n"
        f"🛁 *Bathrooms:* {baths_txt}\n\n"
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
        print(f"✅ Telegram alert sent for Top {bracket_percentile}% deal (ID: {listing_id}) in {suburb}")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def run_scraper(max_pages=50):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }

    for search in PRESET_SEARCHES:
        suburb_name = search["name"]
        base_url = search["url"].rstrip('/')
        
        print(f"\n--- Scraping {suburb_name} Apartments ---")

        page = 1
        listings = []
        seen_ids = set()

        while page <= max_pages:
            page_url = base_url if page == 1 else f"{base_url}/p{page}"
            print(f"Fetching page {page} for {suburb_name}...")
            
            try:
                res = requests.get(page_url, headers=headers, timeout=10)
                if res.status_code != 200:
                    print(f"Reached end of pages or hit a wall (HTTP {res.status_code}). Stopping.")
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                tiles = soup.find_all("div", class_=re.compile("p24_tile|js_resultTile"))
                
                if not tiles:
                    print(f"No property tiles found on page {page}. Scraping complete for {suburb_name}.")
                    break

                new_on_page = 0

                for tile in tiles:
                    link_tag = tile.find("a", href=True)
                    if not link_tag:
                        continue
                    href = link_tag['href']
                    full_url = href if href.startswith("http") else f"https://www.property24.com{href}"
                    
                    listing_id_match = re.search(r'/(\d+)$', href)
                    listing_id = listing_id_match.group(1) if listing_id_match else href
                    
                    if listing_id in seen_ids:
                        continue
                    seen_ids.add(listing_id)
                    new_on_page += 1

                    title_tag = tile.find("span", class_="p24_title") or tile.find("div", class_="p24_title")
                    title = title_tag.text.strip() if title_tag else "Apartment"

                    price_tag = tile.find("div", class_="p24_price") or tile.find("span", class_="p24_price")
                    if not price_tag:
                        continue
                    price_digits = re.sub(r'[^\d]', '', price_tag.text)
                    if not price_digits:
                        continue
                    price = float(price_digits)

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
                    
                    bed_tag = tile.find("span", title="Bedrooms")
                    beds = float(re.sub(r'[^\d\.]', '', bed_tag.text)) if bed_tag and re.search(r'\d', bed_tag.text) else None
                    
                    bath_tag = tile.find("span", title="Bathrooms")
                    baths = float(re.sub(r'[^\d\.]', '', bath_tag.text)) if bath_tag and re.search(r'\d', bath_tag.text) else None

                    rate_sqm = price / sqm

                    listings.append({
                        "id": str(listing_id),
                        "suburb": suburb_name,
                        "title": title,
                        "price": price,
                        "sqm": sqm,
                        "bedrooms": beds,
                        "bathrooms": baths,
                        "rate_sqm": rate_sqm,
                        "url": full_url
                    })

                if new_on_page == 0:
                    print(f"Zero new properties found on page {page}. End of unique listings reached for {suburb_name}.")
                    break

                page += 1
                time.sleep(1)

            except Exception as e:
                print(f"Error scraping {suburb_name} page {page}: {e}")
                break

        if not listings:
            print(f"No new valid listings scraped for {suburb_name}.")
            continue

        raw_count = len(listings)
        print(f"Scraped {raw_count} total unique raw listings for {suburb_name}.")

        for item in listings:
            c.execute(
                "INSERT OR REPLACE INTO raw_listings (id, suburb, title, price, sqm, bedrooms, bathrooms, rate_sqm, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item["id"], item["suburb"], item["title"], item["price"], item["sqm"], item["bedrooms"], item["bathrooms"], item["rate_sqm"], item["url"])
            )
        conn.commit()

        c.execute("SELECT rate_sqm FROM raw_listings WHERE suburb=?", (suburb_name,))
        db_rates = [row[0] for row in c.fetchall()]
        
        if not db_rates:
            continue

        clean_rates, real_min, real_max, median_rate, top_5_thresh = clean_area_data(db_rates)

        # 🛠️ THE FIX: Explicitly name the columns here so the order is guaranteed!
        c.execute("SELECT id, suburb, title, price, sqm, bedrooms, bathrooms, rate_sqm, url FROM raw_listings WHERE suburb=?", (suburb_name,))
        all_db_items = []
        for row in c.fetchall():
            all_db_items.append({
                "id": row[0], "suburb": row[1], "title": row[2], "price": row[3], "sqm": row[4], 
                "bedrooms": row[5], "bathrooms": row[6], "rate_sqm": row[7], "url": row[8]
            })

        valid_items = [x for x in all_db_items if real_min <= x["rate_sqm"] <= real_max]
        valid_items.sort(key=lambda x: x["rate_sqm"])
        total_clean = len(valid_items)

        if total_clean == 0:
            continue

        c.execute(
            "INSERT OR REPLACE INTO area_stats (suburb, total_raw, total_clean, real_min, real_max, median_rate, top_5_percentile) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (suburb_name, len(all_db_items), total_clean, real_min, real_max, median_rate, top_5_thresh)
        )
        conn.commit()

        top_5_count = int(np.ceil(total_clean * 0.05))

        for idx, item in enumerate(valid_items[:top_5_count]):
            rank_num = idx + 1
            true_percentile = (rank_num / total_clean) * 100 if total_clean > 0 else 100.0
            pct_below_median = ((median_rate - item["rate_sqm"]) / median_rate) * 100 if median_rate > 0 else 0.0

            send_telegram_alert(
                listing_id=item["id"],
                suburb=item["suburb"],
                title=item["title"],
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
