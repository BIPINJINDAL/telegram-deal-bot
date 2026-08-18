import os
import io
import re
import json
import time
import html
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
EARNKARO_ID = "5545743"
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
}

def log(msg):
    print(f"[{time.strftime('%X')}] {msg}", flush=True)

# --- Database ---
def init_db():
    conn = sqlite3.connect("deals.db")
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
    except Exception as e:
        log(f"DB Insert Error: {e}")
    finally:
        conn.close()

# --- Affiliate Generator ---
def format_affiliate_url(url):
    if not url or not url.startswith("http"):
        return f"https://www.amazon.in?tag={AMAZON_TAG}&ascsubtag={EARNKARO_ID}"
    
    clean_url = url.split("?")[0].strip()
    sep = "&" if "?" in clean_url else "?"

    if "amazon.in" in clean_url or "amzn.to" in clean_url:
        return f"{clean_url}{sep}tag={AMAZON_TAG}&ascsubtag={EARNKARO_ID}"
    elif "flipkart.com" in clean_url:
        return f"{clean_url}{sep}affid={AMAZON_TAG}&affExtParam1={EARNKARO_ID}"
    elif "myntra.com" in clean_url:
        return f"{clean_url}{sep}utm_source=affiliate&utm_medium={EARNKARO_ID}"
    elif "ajio.com" in clean_url:
        return f"{clean_url}{sep}utm_source=earn_karo&utm_campaign={EARNKARO_ID}"
    elif "meesho.com" in clean_url:
        return f"{clean_url}{sep}ref_id={EARNKARO_ID}"
    
    return f"{clean_url}{sep}tag={AMAZON_TAG}&ref={EARNKARO_ID}"

# --- Real-Time Multi-Feed Scraper ---
def fetch_live_marketplace_deals():
    deals = []

    # Stream 1: IndiaFreeStuff
    try:
        res = requests.get("https://indiafreestuff.in/feed", headers=HEADERS, timeout=6)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:8]:
                title = item.find('title').text.strip() if item.find('title') is not None else ""
                link = item.find('link').text.strip() if item.find('link') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""

                soup = BeautifulSoup(desc, "html.parser")
                img = soup.find("img")
                img_url = img.get("src") if img else ""

                store_link = None
                for a in soup.find_all("a", href=True):
                    href = a['href']
                    if any(dom in href for dom in ["amazon.in", "amzn.to", "flipkart.com", "myntra.com", "ajio.com", "meesho.com"]):
                        store_link = href
                        break

                target_url = store_link if store_link else link
                if title and target_url:
                    deals.append({
                        "id": f"ifs_{hash(title)}",
                        "title": title,
                        "url": target_url,
                        "photo": img_url
                    })
    except Exception as e:
        log(f"IFS Feed Error: {e}")

    # Fallback Dynamic Real-Time Bestseller Pool
    if len(deals) < 3:
        deals.extend([
            {
                "id": "item_real_boat_141",
                "title": "boAt Airdopes 141 Bluetooth TWS (42H Battery, Fast Charge)",
                "url": "https://www.amazon.in/dp/B09N3ZNHTY",
                "photo": "https://m.media-amazon.com/images/I/61KNJav3S9L._SL1500_.jpg"
            },
            {
                "id": "item_real_noise_watch",
                "title": "Noise ColorFit Pulse 2 Max 1.85'' HD Bluetooth Calling Smart Watch",
                "url": "https://www.amazon.in/dp/B0B6BLTGTT",
                "photo": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg"
            },
            {
                "id": "item_real_portronics_mouse",
                "title": "Portronics Toad 23 Wireless Optical Mouse (High Precision)",
                "url": "https://www.amazon.in/dp/B0BG88TWW7",
                "photo": "https://m.media-amazon.com/images/I/51Z+859oZRL._SL1500_.jpg"
            },
            {
                "id": "item_real_ambrane_pb",
                "title": "Ambrane 10000mAh Slim 20W Fast Charging Power Bank",
                "url": "https://www.amazon.in/dp/B09V7CYVMD",
                "photo": "https://m.media-amazon.com/images/I/71lVwl3q-kL._SL1500_.jpg"
            }
        ])

    log(f"Total active deals ready for dispatch: {len(deals)}")
    return deals

# --- Telegram Broadcaster ---
def send_telegram_deal(deal):
    clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])
    safe_title = html.escape(clean_title)
    aff_link = format_affiliate_url(deal["url"])

    caption = (
        f"🔥 <b>SUPER LOOT DEAL / PRICE DROP</b> 🔥\n\n"
        f"📦 <b>{safe_title}</b>\n\n"
        f"⚡ <i>Limited Stock Offer! Jaldi order karein!</i>"
    )

    reply_markup = {
        "inline_keyboard": [[{"text": "🛒 Buy Now / Loot Deal", "url": aff_link}]]
    }

    # Direct Photo Upload
    if deal.get("photo") and deal["photo"].startswith("http"):
        try:
            img_res = requests.get(deal["photo"], headers=HEADERS, timeout=6)
            if img_res.status_code == 200 and len(img_res.content) > 1000:
                files = {"photo": ("deal.jpg", io.BytesIO(img_res.content), "image/jpeg")}
                data = {
                    "chat_id": CHANNEL_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(reply_markup)
                }
                resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=10)
                if resp.status_code == 200:
                    log(f"✅ Photo Deal Posted: {clean_title[:35]}")
                    return True
                else:
                    log(f"Telegram API Photo Error: {resp.text}")
        except Exception as e:
            log(f"Image fetch error: {e}")

    # Fallback to Text Message
    try:
        payload = {
            "chat_id": CHANNEL_ID,
            "text": caption,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)
        if resp.status_code == 200:
            log(f"✅ Text Deal Posted: {clean_title[:35]}")
            return True
        else:
            log(f"Telegram API Message Error: {resp.text}")
    except Exception as e:
        log(f"Message Send Exception: {e}")

    return False

# --- Continuous 24/7 Engine Loop ---
def continuous_deals_poster():
    log("Background deals poster worker started...")
    time.sleep(3)
    
    while True:
        try:
            deals = fetch_live_marketplace_deals()
            posted = 0

            for deal in deals:
                if not is_already_posted(deal["id"]):
                    if send_telegram_deal(deal):
                        mark_as_posted(deal["id"])
                        posted += 1
                        time.sleep(4)
                        if posted >= 2:
                            break
            
            if posted == 0:
                log("All current deals already posted. Sleeping for next cycle...")

        except Exception as e:
            log(f"Main loop error: {e}")

        # 3 minute sleep between deal batches
        time.sleep(180)

# --- Keep-Alive Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Engine Live 24/7!")
    def log_message(self, format, *args):
        return

def main():
    init_db()
    log("Database initialized.")
    
    poster_thread = threading.Thread(target=continuous_deals_poster, daemon=True)
    poster_thread.start()

    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    log(f"Health Server running on port {PORT}")
    server.serve_forever()

if __name__ == "__main__":
    main()
