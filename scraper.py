import os
import re
import sqlite3
import requests
from bs4 import BeautifulSoup

# --- DATABASE SETUP ---
DB_NAME = "properties.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table for stored user searches
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_name TEXT,
            p24_url TEXT,
            max_price_sqm REAL
        )
    ''')
    # Table for scraped listings
    c.execute('''
        CREATE TABLE IF NOT EXISTS listings (
            id TEXT PRIMARY KEY,
            title TEXT,
            price REAL,
            sqm REAL,
            rate_sqm REAL,
            url TEXT,
            notified INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

# --- TELEGRAM NOTIFIER ---
def send_telegram_alert(title, price, sqm, rate_sqm, url):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️ Missing Telegram Token or Chat ID environment variables.")
        return

    message = (
        f"🔥 *BARGAIN PROPERTY ALERT!*\n\n"
        f"📍 *Title:* {title}\n"
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
        res = requests.post(api_url, json=payload)
        res.raise_for_status()
        print(f"✅ Alert sent for: {title}")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

# --- SCRAPER ENGINE ---
def run_scraper():
    init_db()  # Ensures tables exist before executing queries
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT id, search_name, p24_url, max_price_sqm FROM user_searches")
    searches = c.fetchall()
    
    if not searches:
        print("No user searches found in database.")
        conn.close()
        return

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    for search_id, search_name, url, max_price_sqm in searches:
        print(f"Scraping search: {search_name} -> {url}")
        try:
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            
            # Find Property24 listing cards
            tiles = soup.find_all("div", class_=re.compile("p24_tile|js_resultTile"))
            
            for tile in tiles:
                # Extract URL & ID
                link_tag = tile.find("a", href=True)
                if not link_tag:
                    continue
                href = link_tag['href']
                full_url = href if href.startswith("http") else f"https://www.property24.com{href}"
                
                listing_id_match = re.search(r'/(\d+)$', href)
                listing_id = listing_id_match.group(1) if listing_id_match else href

                # Extract Title
                title_tag = tile.find("span", class_="p24_title") or tile.find("div", class_="p24_title")
                title = title_tag.text.strip() if title_tag else "Property Listing"

                # Extract Price
                price_tag = tile.find("div", class_="p24_price") or tile.find("span", class_="p24_price")
                if not price_tag:
                    continue
                price_digits = re.sub(r'[^\d]', '', price_tag.text)
                if not price_digits:
                    continue
                price = float(price_digits)

                # Extract Size (m²)
                sqm_tag = tile.find("span", title="Erf Size") or tile.find("span", title="Floor Size") or tile.find("span", class_="p24_size")
                if not sqm_tag:
                    # Search text for m² numbers
                    sqm_match = re.search(r'(\d+)\s*m²', tile.text)
                    sqm = float(sqm_match.group(1)) if sqm_match else None
                else:
                    sqm_digits = re.sub(r'[^\d]', '', sqm_tag.text)
                    sqm = float(sqm_digits) if sqm_digits else None

                if not sqm or sqm <= 0:
                    continue  # Skip listings without valid size

                rate_sqm = price / sqm

                # Save to database
                c.execute("SELECT notified FROM listings WHERE id = ?", (listing_id,))
                row = c.fetchone()

                if row is None:
                    # New listing found
                    should_notify = 1 if rate_sqm <= max_price_sqm else 0
                    c.execute(
                        "INSERT INTO listings (id, title, price, sqm, rate_sqm, url, notified) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (listing_id, title, price, sqm, rate_sqm, full_url, should_notify)
                    )
                    conn.commit()
                    
                    if should_notify:
                        send_telegram_alert(title, price, sqm, rate_sqm, full_url)

        except Exception as e:
            print(f"Error scraping {url}: {e}")

    conn.close()

if __name__ == "__main__":
    run_scraper()
