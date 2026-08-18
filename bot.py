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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}

# --- Persistent Database Setup ---
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
    except:
        pass
    finally:
        conn.close()

# --- 100% Working Referral & Commission Link Generator ---
def make_affiliate_link(url):
    if not url:
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

# --- Real-Time Live Loot Deals Scraper (DesiDime + FreeStuff) ---
def fetch_realtime_deals():
    deals = []
    
    # 1. DesiDime Live RSS Stream
    try:
        res = requests.get("https://www.desidime.com/feed", headers=HEADERS, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:10]:
                title = item.find('title').text.strip() if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                link = item.find('link').text.strip() if item.find('link') is not None else ""
                
                soup = BeautifulSoup(desc, "html.parser")
                img_tag = soup.find("img")
                img_url = img_tag.get("src") if img_tag else ""
                
                store_tag = soup.find("a", href=True)
                target_url = store_tag['href'] if store_tag else link

                if title and target_url:
                    deals.append({
                        "id": f"dd_{hash(title)}",
                        "title": title,
                        "url": target_url,
                        "photo": img_url
                    })
    except Exception as e:
        print(f"DesiDime feed warning: {e}")

    # 2. IndiaFreeStuff Real-Time Stream (Secondary Stream)
    if len(deals) < 4:
        try:
            res2 = requests.get("https://indiafreestuff.in/feed", headers=HEADERS, timeout=8)
            if res2.status_code == 200:
                root2 = ET.fromstring(res2.content)
                for item in root2.findall('.//item')[:6]:
                    title = item.find('title').text.strip() if item.find('title') is not None else ""
                    desc = item.find('description').text if item.find('description') is not None else ""
                    link = item.find('link').text.strip() if item.find('link') is not None else ""

                    soup2 = BeautifulSoup(desc, "html.parser")
                    img_tag2 = soup2.find("img")
                    img_url2 = img_tag2.get("src") if img_tag2 else ""

                    deals.append({
                        "id": f"ifs_{hash(title)}",
                        "title": title,
                        "url": link,
                        "photo": img_url2
                    })
        except Exception as e:
            print(f"IFS feed warning: {e}")

    return deals

# --- Direct Telegram Broadcaster (Conflict-Free) ---
def send_telegram_deal(deal):
    clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])
    safe_title = html.escape(clean_title)
    aff_link = make_affiliate_link(deal["url"])

    caption = (
        f"🔥 <b>SUPER PRICE DROP / LOOT OFFER</b> 🔥\n\n"
        f"📦 <b>{safe_title}</b>\n\n"
        f"⚡ <i>Limited Stock! Jaldi Grab Karein!</i>"
    )

    reply_markup = {
        "inline_keyboard": [[{"text": "🛒 Buy Now / Loot Deal", "url": aff_link}]]
    }

    # Attempt 1: Direct Photo Stream with Image Bytes
    if deal.get("photo") and deal["photo"].startswith("http"):
        try:
            img_res = requests.get(deal["photo"], headers=HEADERS, timeout=8)
            if img_res.status_code == 200 and len(img_res.content) > 1000:
                files = {"photo": ("product.jpg", io.BytesIO(img_res.content), "image/jpeg")}
                data = {
                    "chat_id": CHANNEL_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": requests.utils.json.dumps(reply_markup)
                }
                resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=12)
                if resp.status_code == 200:
                    return True
        except Exception as e:
            print(f"Photo upload fallback: {e}")

    # Attempt 2: Text Message Fallback if image fails
    try:
        payload = {
            "chat_id": CHANNEL_ID,
            "text": caption,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Message post error: {e}")
        return False

# --- 24/7 Automated Posting Loop ---
def continuous_deals_poster():
    while True:
        try:
            deals = fetch_realtime_deals()
            posted_any = False

            for deal in deals:
                if not is_already_posted(deal["id"]):
                    success = send_telegram_deal(deal)
                    if success:
                        mark_as_posted(deal["id"])
                        print(f"Posted Real-Time Deal: {deal['title']}")
                        posted_any = True
                        time.sleep(3)
                        break
        except Exception as e:
            print(f"Scraper loop warning: {e}")

        # Har 5 minute mein nayi live deals check karega
        time.sleep(300)

# --- Keep-Alive Health Server for UptimeRobot ---
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
    print(f"Server running on port {PORT}...")
    server.serve_forever()

if __name__ == "__main__":
    main()
