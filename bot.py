import os
import io
import re
import json
import time
import html
import sqlite3
import threading
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

# Aapka Verified EarnKaro User ID
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
        log(f"DB Error: {e}")
    finally:
        conn.close()

def clear_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- 100% Working Clean Link Generator (With User ID 5545743) ---
def build_earn_url(platform, raw_url):
    clean_url = raw_url.split("?")[0].strip()
    if platform == "Amazon":
        return f"{clean_url}?ref_id={EARNKARO_ID}&tag=ekaro-{EARNKARO_ID}"
    elif platform == "Flipkart":
        return f"{clean_url}?affid=ekaro&affExtParam1={EARNKARO_ID}"
    elif platform == "Myntra":
        return f"{clean_url}?utm_source=earnkaro&utm_medium={EARNKARO_ID}"
    elif platform == "Ajio":
        return f"{clean_url}?utm_source=earnkaro&utm_campaign={EARNKARO_ID}"
    elif platform == "Meesho":
        return f"{clean_url}?ref={EARNKARO_ID}"
    return f"{clean_url}?ref_id={EARNKARO_ID}"

# --- Multi-Platform Active Loot Pool (Amazon, Flipkart, Myntra, Ajio, Meesho) ---
MULTI_STORE_POOL = [
    {
        "id": "deal_fk_boat131",
        "platform": "Flipkart",
        "badge": "🛍️ FLIPKART BIG LOOT DEAL",
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime, Beast Mode)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": "https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "id": "deal_myntra_hrx",
        "platform": "Myntra",
        "badge": "👟 MYNTRA FASHION LOOT",
        "title": "HRX by Hrithik Roshan Men Breathable Mesh Running Shoes",
        "price": "₹749",
        "mrp": "₹2,799",
        "discount": "73% OFF",
        "url": "https://www.myntra.com/sports-shoes/hrx-by-hrithik-roshan/hrx-men-grey-mesh-running-shoes/14682498/buy",
        "photo": "https://assets.myntassets.com/h_720,q_90,w_540/v1/assets/images/14682498/2021/10/7/6ffcfd1b-7a6c-48d8-94ef-6faec18bfab21633604085448-HRX-by-Hrithik-Roshan-Men-Grey--Black-Running-Shoes-10216336040-1.jpg"
    },
    {
        "id": "deal_amz_boat141",
        "platform": "Amazon",
        "badge": "⚡ AMAZON LIGHTNING DEAL",
        "title": "boAt Airdopes 141 Bluetooth TWS (42H Playtime, Low Latency, Fast Charge)",
        "price": "₹999",
        "mrp": "₹4,490",
        "discount": "78% OFF",
        "url": "https://www.amazon.in/dp/B09N3ZNHTY",
        "photo": "https://m.media-amazon.com/images/I/61KNJav3S9L._SL1500_.jpg"
    },
    {
        "id": "deal_ajio_jacket",
        "platform": "Ajio",
        "badge": "🧥 AJIO TRENDS DROP",
        "title": "DNMX Men Slim Fit Washed Denim Jacket with Flap Pockets",
        "price": "₹699",
        "mrp": "₹2,299",
        "discount": "69% OFF",
        "url": "https://www.ajio.com/dnmx-men-washed-denim-jacket/p/460982542_blue",
        "photo": "https://assets.ajio.com/medias/sys_master/root/20230624/e95w/6496ec2fa9b42d15c9d96853/-473Wx593H-460982542-blue-MODEL.jpg"
    },
    {
        "id": "deal_meesho_kurta",
        "platform": "Meesho",
        "badge": "🌸 MEESHO MEGA SALE",
        "title": "Women Pure Cotton Printed Straight Kurta & Pant Combo Set",
        "price": "₹299",
        "mrp": "₹999",
        "discount": "70% OFF",
        "url": "https://www.meesho.com/women-cotton-kurta-set/p/4v919z",
        "photo": "https://images.meesho.com/images/products/317584859/1_512.jpg"
    },
    {
        "id": "deal_amz_noisepulse",
        "platform": "Amazon",
        "badge": "⚡ AMAZON PRICE DROP",
        "title": "Noise ColorFit Pulse 2 Max 1.85'' HD Display Smart Watch (BT Calling)",
        "price": "₹1,199",
        "mrp": "₹5,999",
        "discount": "80% OFF",
        "url": "https://www.amazon.in/dp/B0B6BLTGTT",
        "photo": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg"
    }
]

# --- Telegram Broadcaster ---
def send_telegram_deal(deal):
    clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])
    safe_title = html.escape(clean_title)
    safe_badge = html.escape(deal['badge'])
    safe_price = html.escape(deal['price'])
    safe_mrp = html.escape(deal['mrp'])
    safe_discount = html.escape(deal['discount'])
    
    aff_link = build_earn_url(deal["platform"], deal["url"])

    caption = (
        f"🔥 <b>{safe_badge} ({safe_discount})</b> 🔥\n\n"
        f"📦 <b>{safe_title}</b>\n\n"
        f"🔻 MRP: <s>{safe_mrp}</s>\n"
        f"💥 <b>Offer Price: {safe_price}</b>\n\n"
        f"⚡ <i>Limited Period Offer! Jaldi order karein!</i>"
    )

    reply_markup = {
        "inline_keyboard": [[{"text": f"🛒 Buy on {deal['platform']} / Grab Deal", "url": aff_link}]]
    }

    # Upload Photo Directly
    try:
        res = requests.get(deal["photo"], headers=HEADERS, timeout=8)
        if res.status_code == 200 and len(res.content) > 1000:
            files = {"photo": ("deal.jpg", io.BytesIO(res.content), "image/jpeg")}
            data = {
                "chat_id": CHANNEL_ID,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(reply_markup)
            }
            resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=12)
            if resp.status_code == 200:
                log(f"✅ [{deal['platform']}] Photo Deal Posted: {clean_title[:30]}")
                return True
    except Exception as e:
        log(f"Photo upload error: {e}")

    # Fallback to Text Message
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
        log(f"Text send error: {e}")
        return False

# --- Continuous 24/7 Engine Loop ---
def continuous_deals_poster():
    log("Multi-Platform deals poster worker started...")
    time.sleep(3)
    
    while True:
        try:
            pool = MULTI_STORE_POOL.copy()
            random.shuffle(pool)

            # Agar sabhi deals post ho chuki hain, reset database for continuous loop
            if all(is_already_posted(d["id"]) for d in pool):
                log("All multi-store deals posted. Resetting cycle...")
                clear_db()

            for deal in pool:
                if not is_already_posted(deal["id"]):
                    if send_telegram_deal(deal):
                        mark_as_posted(deal["id"])
                        time.sleep(4)
                        break

        except Exception as e:
            log(f"Main loop error: {e}")

        # Post next platform deal every 3 minutes
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
