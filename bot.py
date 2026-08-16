import os
import time
import sqlite3
import threading
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = os.getenv("EARNKARO_ID", "").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

# --- Database ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE
        )
    """)
    conn.commit()
    conn.close()

def is_already_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (str(deal_id),))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id) VALUES (?)", (str(deal_id),))
        conn.commit()
    except:
        pass
    finally:
        conn.close()

# --- Affiliate Generator ---
def convert_to_affiliate(original_url):
    if not original_url:
        return "https://www.amazon.in"
    if EARNKARO_ID:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}&r={EARNKARO_ID}"
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- Live Deals Scraper ---
def fetch_live_deals():
    deals = []
    feeds = [
        "https://indiafreestuff.in/feed",
        "https://www.offernloot.com/feed",
        "https://dealhunt.in/feed"
    ]

    for feed in feeds:
        try:
            res = requests.get(feed, headers=HEADERS, timeout=8)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                for item in root.findall('.//item')[:10]:
                    title = item.find('title').text.strip() if item.find('title') is not None else ""
                    link = item.find('link').text.strip() if item.find('link') is not None else ""
                    desc = item.find('description').text if item.find('description') is not None else ""
                    
                    soup = BeautifulSoup(desc, "html.parser")
                    img_tag = soup.find("img")
                    img_url = img_tag.get("src") if img_tag else ""
                    
                    enclosure = item.find('enclosure')
                    if not img_url and enclosure is not None:
                        img_url = enclosure.get('url', '')

                    store_link = None
                    for a in soup.find_all("a", href=True):
                        href = a['href']
                        if any(dom in href for dom in ["amazon.in", "amzn.to", "flipkart.com", "meesho.com", "myntra.com", "ajio.com"]):
                            store_link = href
                            break

                    target_url = store_link if store_link else link

                    if title and target_url:
                        deals.append({
                            "id": link,
                            "title": title,
                            "url": target_url,
                            "image": img_url
                        })
        except Exception as e:
            print(f"Scraper warning: {e}")

    return deals

# --- Direct Telegram Broadcaster (No Conflicts) ---
def send_telegram_deal(title, image_url, buy_url):
    caption = (
        f"🔥 *SUPER LOOT DEAL* 🔥\n\n"
        f"📦 {title}\n\n"
        f"⚡ *Limited Period Deal! Grab Now!*"
    )
    inline_keyboard = {
        "inline_keyboard": [[{"text": "🛒 Buy Now / Loot Deal", "url": buy_url}]]
    }

    if image_url and image_url.startswith("http"):
        payload = {
            "chat_id": CHANNEL_ID,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "Markdown",
            "reply_markup": inline_keyboard
        }
        res = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", json=payload, timeout=10)
    else:
        payload = {
            "chat_id": CHANNEL_ID,
            "text": caption,
            "parse_mode": "Markdown",
            "reply_markup": inline_keyboard
        }
        res = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)
    
    return res.status_code == 200

def post_deals_loop():
    while True:
        try:
            deals = fetch_live_deals()
            for deal in deals:
                if not is_already_posted(deal["id"]):
                    aff_link = convert_to_affiliate(deal["url"])
                    clean_title = deal['title'].replace('*', '').replace('_', '')
                    
                    success = send_telegram_deal(clean_title, deal["image"], aff_link)
                    if success:
                        mark_as_posted(deal["id"])
                        print(f"Posted: {clean_title}")
                        time.sleep(3)
        except Exception as e:
            print(f"Loop error: {e}")
        
        # Har 5 minute (300 sec) mein internet scan karega
        time.sleep(300)

# --- Web Server for UptimeRobot ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Engine Live 24/7!")
    def log_message(self, format, *args):
        return

def main():
    init_db()
    
    # Start posting engine in background
    poster_thread = threading.Thread(target=post_deals_loop, daemon=True)
    poster_thread.start()

    # Start health check server
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    print("Server running on port", PORT)
    server.serve_forever()

if __name__ == "__main__":
    main()
