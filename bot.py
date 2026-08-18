import os
import io
import re
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}

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
        print(f"DB Error: {e}")
    finally:
        conn.close()

# --- Affiliate Formatter ---
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

# --- Real-Time Multi-Source Scraper ---
def fetch_live_marketplace_deals():
    deals = []

    # Stream 1: IndiaFreeStuff Live Feed
    try:
        res = requests.get("https://indiafreestuff.in/feed", headers=HEADERS, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:10]:
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
        print(f"IFS Feed Scrape Error: {e}")

    # Stream 2: OfferNLoot Live Scraper
    try:
        res2 = requests.get("https://www.offernloot.com/feed/", headers=HEADERS, timeout=8)
        if res2.status_code == 200:
            root2 = ET.fromstring(res2.content)
            for item in root2.findall('.//item')[:8]:
                title2 = item.find('title').text.strip() if item.find('title') is not None else ""
                link2 = item.find('link').text.strip() if item.find('link') is not None else ""
                desc2 = item.find('description').text if item.find('description') is not None else ""

                soup2 = BeautifulSoup(desc2, "html.parser")
                img2 = soup2.find("img")
                img_url2 = img2.get("src") if img2 else ""

                deals.append({
                    "id": f"onl_{hash(title2)}",
                    "title": title2,
                    "url": link2,
                    "photo": img_url2
                })
    except Exception as e:
        print(f"ONL Feed Scrape Error: {e}")

    print(f"Total live deals fetched from internet: {len(deals)}")
    return deals

# --- Direct Telegram Broadcaster ---
def send_telegram_deal(deal):
    clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])
    safe_title = html.escape(clean_title)
    aff_link = format_affiliate_url(deal["url"])

    caption = (
        f"🔥 <b>LIVE LOOT DEAL / PRICE DROP</b> 🔥\n\n"
        f"📦 <b>{safe_title}</b>\n\n"
        f"⚡ <i>Limited Period Offer! Jaldi order karein!</i>"
    )

    reply_markup = {
        "inline_keyboard": [[{"text": "🛒 Buy Now / Loot Deal", "url": aff_link}]]
    }

    # Direct Photo Upload Stream
    if deal.get("photo") and deal["photo"].startswith("http"):
        try:
            img_res = requests.get(deal["photo"], headers=HEADERS, timeout=7)
            if img_res.status_code == 200 and len(img_res.content) > 1000:
                files = {"photo": ("deal.jpg", io.BytesIO(img_res.content), "image/jpeg")}
                data = {
                    "chat_id": CHANNEL_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": requests.utils.json.dumps(reply_markup)
                }
                resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=12)
                if resp.status_code == 200:
                    print(f"Photo deal posted successfully: {clean_title[:30]}")
                    return True
                else:
                    print(f"Telegram Photo Error: {resp.text}")
        except Exception as e:
            print(f"Image download error: {e}")

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
            print(f"Text deal posted successfully: {clean_title[:30]}")
            return True
        else:
            print(f"Telegram Text Error: {resp.text}")
    except Exception as e:
        print(f"Direct Message Error: {e}")

    return False

# --- Continuous 24/7 Engine Loop ---
def continuous_deals_poster():
    print("Background Deals Engine Started...")
    
    # Startup test check - runs immediately on boot
    time.sleep(5)
    
    while True:
        try:
            deals = fetch_live_marketplace_deals()
            posted_count = 0

            for deal in deals:
                if not is_already_posted(deal["id"]):
                    success = send_telegram_deal(deal)
                    if success:
                        mark_as_posted(deal["id"])
                        posted_count += 1
                        time.sleep(3)
                        # Har run mein 2 fresh deals post karega
                        if posted_count >= 2:
                            break
            
            if posted_count == 0:
                print("No new unposted deals found in this cycle.")

        except Exception as e:
            print(f"Main loop error: {e}")

        # Agli live deal ke liye 3 minute wait
        time.sleep(180)

# --- Keep-Alive Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Engine Live 24/7 (Real-Time Scraper Active)")
    def log_message(self, format, *args):
        return

def main():
    init_db()
    
    poster_thread = threading.Thread(target=continuous_deals_poster, daemon=True)
    poster_thread.start()

    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    print(f"Health check running on port {PORT}...")
    server.serve_forever()

if __name__ == "__main__":
    main()
