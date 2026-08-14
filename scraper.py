import requests
from bs4 import BeautifulSoup
import re
import sqlite3
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")

def send_alert(title, price, floor_size, price_sqm, url):
    msg = (
        f"🚨 *PROPERTY24 BARGAIN DETECTED!*\n\n"
        f"🏡 *{title}*\n"
        f"💰 **Price:** R {price:,.0f}\n"
        f"📐 **Floor Size:** {floor_size} m²\n"
        f"📊 **Rate:** *R {price_sqm:,.2f} / m²*\n\n"
        f"🔗 [View Property24 Listing]({url})"
    )
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(api, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})

def parse_num(text):
    clean = re.sub(r"[^\d]", "", text)
    return float(clean) if clean else 0.0

def run_scraper():
    conn = sqlite3.connect("property_app.db")
    c = conn.cursor()
    
    # Get active user searches
    c.execute("SELECT id, p24_url, max_price_sqm FROM user_searches")
    searches = c.fetchall()
    
    # Default fallback URL if none entered in DB
    if not searches:
        searches = [(0, "https://www.property24.com/for-sale/morningside/sandton/gauteng/4258", 10000)]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for search_id, p24_url, target_max_rate in searches:
        res = requests.get(p24_url, headers=headers)
        if res.status_code != 200:
            continue
            
        soup = BeautifulSoup(res.text, "html.parser")
        listings = soup.find_all("div", class_=re.compile(r"p24_regularTile|js_resultTile"))

        for item in listings:
            try:
                link_tag = item.find("a", href=True)
                if not link_tag: continue
                
                rel_url = link_tag['href']
                full_url = f"https://www.property24.com{rel_url}" if rel_url.startswith("/") else rel_url
                
                prop_id_match = re.search(r"/(\d+)$", full_url)
                prop_id = prop_id_match.group(1) if prop_id_match else full_url

                # Check if listing was already alerted
                c.execute("SELECT id FROM properties WHERE id = ?", (prop_id,))
                if c.fetchone(): continue

                price_tag = item.find("span", class_=re.compile(r"p24_price"))
                sqm_tag = item.find("span", class_=re.compile(r"p24_size"))
                title_tag = item.find("span", class_=re.compile(r"p24_title"))

                if not price_tag or not sqm_tag: continue

                price = parse_num(price_tag.text)
                floor_size = parse_num(sqm_tag.text)
                title = title_tag.text.strip() if title_tag else "Morningside Property"

                if price > 0 and floor_size > 0:
                    price_sqm = price / floor_size

                    # Save to DB
                    c.execute(
                        "INSERT OR REPLACE INTO properties (id, title, price, floor_size, price_per_sqm, url) VALUES (?, ?, ?, ?, ?, ?)",
                        (prop_id, title, price, floor_size, price_sqm, full_url)
                    )
                    conn.commit()

                    # Trigger alert if rate meets target limit
                    if price_sqm <= target_max_rate:
                        send_alert(title, price, floor_size, price_sqm, full_url)

            except Exception as e:
                continue

    conn.close()

if __name__ == "__main__":
    run_scraper()
