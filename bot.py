import os
import time
import html
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

# --- 1. Credentials ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()
try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

EARNKARO_ID = "5545743"
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# --- 2. Database ---
def init_db():
    conn = sqlite3.connect("real_deals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def is_already_posted(deal_id):
    conn = sqlite3.connect("real_deals.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (str(deal_id),))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id):
    conn = sqlite3.connect("real_deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id) VALUES (?)", (str(deal_id),))
        conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()

# --- 3. Link Formatter (No Dummy Links) ---
def clean_and_tag_url(url):
    clean_url = url.split("?")[0].strip()
    if "amazon.in" in clean_url or "amzn.to" in clean_url:
        return f"{clean_url}?tag=dealstracker-21&ascsubtag={EARNKARO_ID}"
    elif "flipkart.com" in clean_url:
        return f"{clean_url}?affExtParam1={EARNKARO_ID}"
    return clean_url

# --- 4. Genuine Real-Time Scraper (Multiple Sources) ---
def fetch_real_live_deals():
    deals = []
    feeds = [
        "https://www.offernloot.com/feed/",
        "https://indiafreestuff.in/feed/"
    ]
    
    for feed in feeds:
        try:
            res = requests.get(feed, headers=HEADERS, timeout=12)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                # Parse top 10 latest deals from each feed
                for item in root.findall(".//item")[:10]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    
                    soup = BeautifulSoup(desc, "html.parser")
                    store_link = None
                    platform = "Store"
                    
                    # Extract raw store link
                    for a in soup.find_all("a", href=True):
                        href = a['href']
                        if "amazon.in" in href or "amzn.to" in href:
                            store_link = href
                            platform = "Amazon"
                            break
                        elif "flipkart.com" in href:
                            store_link = href
                            platform = "Flipkart"
                            break
                    
                    if store_link and title:
                        deal_id = f"deal_{hash(title.strip())}"
                        deals.append({
                            "id": deal_id,
                            "title": title.strip(),
                            "url": store_link,
                            "platform": platform
                        })
        except Exception as e:
            print(f"Failed to scrape {feed}: {e}")
            
    return deals

# --- 5. Direct Telegram API Poster ---
def send_telegram_post(deal):
    deal_link = clean_and_tag_url(deal["url"])
    safe_title = html.escape(deal["title"])
    platform = deal["platform"]

    caption = (
        f"🚨 <b>LATEST PRICE DROP ALERT</b> 🚨\n\n"
        f"📦 <b>{safe_title}</b>\n\n"
        f"🔗 <b>Platform:</b> {platform}\n\n"
        f"⚡ <i>Limited Time Loot! Jaldi Check Karein!</i>"
    )
    
    reply_markup = {
        "inline_keyboard": [[{"text": f"🛒 Grab Deal on {platform}", "url": deal_link}]]
    }
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": caption,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
        "disable_web_page_preview": False
    }
    
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"Telegram API Error: {e}")
        return False

# --- 6. 24/7 Background Engine ---
def background_deals_loop():
    print("Background Scraper Started. Hunting for real deals...")
    time.sleep(5) # Give server time to boot
    
    while True:
        try:
            live_deals = fetch_real_live_deals()
            
            if live_deals:
                for deal in live_deals:
                    if not is_already_posted(deal["id"]):
                        if send_telegram_post(deal):
                            mark_as_posted(deal["id"])
                            print(f"Posted Real Deal: {deal['title']}")
                            time.sleep(4) # Anti-spam delay
            else:
                print("No fresh deals found in this cycle.")
                
        except Exception as e:
            print(f"Loop Engine Error: {e}")
            
        # Scan internet every 5 minutes (300 seconds)
        time.sleep(300)

# --- 7. Render Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Deals Engine Running 24/7 Without Conflicts")
    def log_message(self, format, *args):
        pass

def main():
    init_db()
    
    # Start the scraper loop in a separate thread
    threading.Thread(target=background_deals_loop, daemon=True).start()
    
    # Start the web server to keep Render happy
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    print(f"Server started on port {PORT}. Bot is now fully independent.")
    server.serve_forever()

if __name__ == "__main__":
    main()
