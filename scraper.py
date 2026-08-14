import os
import re
import sqlite3
import requests
import numpy as np
from bs4 import BeautifulSoup

DB_NAME = "properties.db"

PRESET_SEARCHES = [
    {
        "name": "Sandton Region",
        "url": "https://www.property24.com/for-sale/advanced-search/results?sp=s%3d4258%2c11001%2c5849%2c5841%2c5862%2c5215%2c5843%2c32933%2c16732%2c5847%2c5860%2c5259%2c5176%2c5861%2c5178%2c5865%2c4262%2c4260%2c5216%2c5201%2c4253%2c4251%2c4270%2c4269%2c5211%2c4289%2c4288%2c17800%2c4285%2c5255%2c1535%2c15697%2c5832%2c5833%2c12702%2c10386%2c5828%2c5817%2c5852%2c5826%2c5836%2c5837%2c12733%2c5816%2c5846%2c4268%2c5224%2c5227%2c17212%2c15698%2c33232%2c17156%2c5850%2c5818%2c5834%2c5813%2c5269%2c5812%2c5278%2c5290%2c5270%2c5284%2c5824%2c5825%2c5926%2c4363%2c4358%2c4352%2c4374%2c4345%2c4378%2c4365%2c4348%2c4361%2c4381%2c4349%2c4366%2c4364%2c5906%2c4342%2c4373%2c4343%2c4355%2c12734%2c4380%2c4340%2c4341%2c12704%2c5266%2c5287%2c5279%2c4369%2c4346%2c4353%2c4347%2c4375%2c4249%2c32908%2c12761%2c17430%2c17431"
    }
]

MIN_SUBURB_VOLUME = 5  # Set low so all active suburbs show up cleanly

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS raw_listings")
    c.execute("DROP TABLE IF EXISTS area_stats")

    c.execute('''
        CREATE TABLE raw_listings (
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
        CREATE TABLE area_stats (
            suburb TEXT PRIMARY KEY,
            total_raw INTEGER,
            total_clean INTEGER,
            real_min REAL,
            real_max REAL,
            median_rate REAL,
            iqr_cutoff REAL,
            top_2_percentile REAL
        )
    ''')
    conn.commit()
    conn.close()

def clean_area_data(rates):
    if len(rates) < 3:
        return rates, min(rates) if rates else 0, max(rates) if rates else 0, 0, 0, 0

    rates = sorted(rates)
    
    # Pass 1: Physical Reality Filter
    filtered = [r for r in rates if 6500 <= r <= 80000]
    if not filtered:
        filtered = rates

    # Pass 2: Density Gap Isolation
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
    
    q25 = np.percentile(clean_rates, 25)
    q75 = np.percentile(clean_rates, 75)
    iqr = q75 - q25
    iqr_cutoff = float(median_rate - (1.0 * iqr))
    top_2_thresh = float(np.percentile(clean_rates, 2))

    return clean_rates, real_min, real_max, median_rate, iqr_cutoff, top_2_thresh

def send_telegram_alert(title, suburb, price, sqm, rate_sqm, true_percentile, rank_num, total_clean, url):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️ Missing Telegram credentials.")
        return

    message = (
        f"🔥 *TOP 2% BARGAIN ALERT!*\n\n"
        f"📍 *Title:* {title}\n"
        f"🏷️ *Suburb:* {suburb}\n"
        f"🏆 *Value Rank:* **Top {true_percentile:.1f}%** (#{rank_num} of {total_clean} in {suburb})\n"
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
        print(f"✅ Alert sent for: {title} in {suburb}")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def parse_suburb(tile):
    """ Extract suburb accurately from HTML location tag """
    loc_tag = tile.find("span", class_=re.compile("p24_location|p24_address")) or \
              tile.find("div", class_=re.compile("p24_location|p24_address")) or \
              tile.find("span", class_="p24_description")
              
    if loc_tag:
        text = loc_tag.text.strip()
        parts = [p.strip() for p in text.split(",")]
        if parts:
            sub = parts[0].title()
            # Clean unwanted prefixes
            sub = re.sub(r'^(Apartment|Flat|House|Townhouse|Unit)\s+In\s+', '', sub, flags=re.IGNORECASE)
            if 2 < len(sub) < 30:
                return sub
    return "Sandton Central"

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
        
        print(f"\n--- Scraping All Pages Across Region ---")

        page = 1
        max_pages = 10
        all_area_listings = []

        while page <= max_pages:
            page_url = f"{base_url}&p={page}" if page > 1 else base_url
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

                    suburb = parse_suburb(tile)

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

                    all_area_listings.append({
                        "id": listing_id,
                        "area": search_name,
                        "suburb": suburb,
                        "title": title,
                        "price": price,
                        "sqm": sqm,
                        "rate_sqm": rate_sqm,
                        "url": full_url
                    })

                page += 1

            except Exception as e:
                print(f"Error on page {page}: {e}")
                break

        if not all_area_listings:
            print("No listings found!")
            continue

        for item in all_area_listings:
            c.execute(
                "INSERT OR REPLACE INTO raw_listings (id, area, suburb, title, price, sqm, rate_sqm, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item["id"], item["area"], item["suburb"], item["title"], item["price"], item["sqm"], item["rate_sqm"], item["url"])
            )
        conn.commit()

        # Group and Process by Suburb
        suburbs = set(x["suburb"] for x in all_area_listings)
        print(f"Successfully Isolated {len(suburbs)} Suburbs: {', '.join(sorted(suburbs))}")

        for sub in suburbs:
            sub_items = [x for x in all_area_listings if x["suburb"] == sub]
            raw_count = len(sub_items)
            
            if raw_count < MIN_SUBURB_VOLUME:
                continue

            rates = [x["rate_sqm"] for x in sub_items]
            clean_rates, real_min, real_max, median_rate, iqr_cutoff, top_2_thresh = clean_area_data(rates)

            valid_sub_items = [x for x in sub_items if real_min <= x["rate_sqm"] <= real_max]
            valid_sub_items.sort(key=lambda x: x["rate_sqm"])
            total_clean = len(valid_sub_items)

            if total_clean < 1:
                continue

            c.execute(
                "INSERT OR REPLACE INTO area_stats (suburb, total_raw, total_clean, real_min, real_max, median_rate, iqr_cutoff, top_2_percentile) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sub, raw_count, total_clean, real_min, real_max, median_rate, iqr_cutoff, top_2_thresh)
            )
            conn.commit()

            for idx, item in enumerate(valid_sub_items):
                rank_num = idx + 1
                true_percentile = (rank_num / total_clean) * 100 if total_clean > 0 else 100.0

                if item["rate_sqm"] <= top_2_thresh and item["rate_sqm"] <= iqr_cutoff:
                    send_telegram_alert(
                        item["title"], sub, item["price"], item["sqm"], 
                        item["rate_sqm"], true_percentile, rank_num, total_clean, item["url"]
                    )

    conn.close()

if __name__ == "__main__":
    run_scraper()
