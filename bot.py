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

# --- 1. Bot & Channel Configuration ---
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
}

def log(msg):
    print(f"[{time.strftime('%X')}] {msg}", flush=True)

# --- 2. Database for Duplicate Prevention ---
def init_db():
    conn = sqlite3.connect("flipkart_deals.db")
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
    conn = sqlite3.connect("flipkart_deals.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (str(deal_id),))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id):
    conn = sqlite3.connect("flipkart_deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id) VALUES (?)", (str(deal_id),))
        conn.commit()
    except Exception as e:
        log(f"DB Error: {e}")
    finally:
        conn.close()

def clear_db():
    conn = sqlite3.connect("flipkart_deals.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- 3. Flipkart Affiliate Link Generator (With User ID: 5545743) ---
def make_flipkart_affiliate_link(raw_url):
    clean_url = raw_url.split("?")[0].strip()
    sep = "&" if "?" in clean_url else "?"
    # Attaches EarnKaro tracking parameter
    return f"{clean_url}{sep}affid=earnkaro&affExtParam1={EARNKARO_ID}"

# --- 4. Verified Active Flipkart Loot Deals Pool ---
FLIPKART_CATALOG = [
    {
        "id": "fk_boat_airdopes_131_pro",
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime, Quad Mics, Beast Mode)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": "https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "id": "fk_boult_audio_z40",
        "title": "Boult Audio Z40 True Wireless Earbuds (60H Playtime, Zen ENC Mic, Fast Charging)",
        "price": "₹999",
        "mrp": "₹4,999",
        "discount": "80% OFF",
        "url": "https://www.flipkart.com/boult-audio-z40-true-wireless-earbuds/p/itm535df2a1ad96b",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/m/u/v/-original-imagp8f4k7fggyhy.jpeg"
    },
    {
        "id": "fk_noise_colorfit_icon",
        "title": "Noise ColorFit Icon 2 1.8'' Display Bluetooth Calling Smart Watch",
        "price": "₹1,099",
        "mrp": "₹5,999",
        "discount": "81% OFF",
        "url": "https://www.flipkart.com/noise-colorfit-icon-2-1-8-display-bluetooth-calling-smartwatch/p/itm677c7ecda6173",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/smartwatch/y/j/0/-original-imagkhe74jhz8hga.jpeg"
    },
    {
        "id": "fk_sandisk_cruzer_blade_64gb",
        "title": "SanDisk Cruzer Blade 64 GB USB 2.0 Pen Drive (High Speed)",
        "price": "₹389",
        "mrp": "₹1,100",
        "discount": "64% OFF",
        "url": "https://www.flipkart.com/sandisk-cruzer-blade-64-gb-utility-pendrive/p/itme9b22bce376ee",
        "photo": "https://rukminim2.flixcart.com/image/832/832/ktyp8cw0/pendrive/pendrive/z/x/q/sdcz50-064g-i35-sandisk-original-imag76pph9h98zfh.jpeg"
    },
    {
        "id": "fk_portronics_toad_mouse",
        "title": "Portronics Toad 23 Wireless Optical Mouse (2.4GHz USB Dongle, Ergonomic)",
        "price": "₹279",
        "mrp": "₹599",
        "discount": "53% OFF",
        "url": "https://www.flipkart.com/portronics-toad-23-wireless-optical-mouse/p/itmd9ba45e12be8f",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/mouse/6/u/i/toad-23-portronics-original-imaghg3b6t57zkhz.jpeg"
    }
]

# --- 5. Direct Telegram Broadcaster ---
def send_flipkart_deal(deal):
    clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])
    safe_title = html.escape(clean_title)
    safe_price = html.escape(deal['price'])
    safe_mrp = html.escape(deal['mrp'])
    safe_discount = html.escape(deal['discount'])

    aff_link = make_flipkart_affiliate_link(deal["url"])

    caption = (
        f"🛍️ <b>FLIPKART SUPER LOOT DEAL ({safe_discount})</b> 🛍️\n\n"
        f"📦 <b>{safe_title}</b>\n\n"
        f"🔻 MRP: <s>{safe_mrp}</s>\n"
        f"💥 <b>Offer Price: {safe_price}</b>\n\n"
        f"⚡ <i>Limited Stock Offer! Jaldi order karein!</i>"
    )

    reply_markup = {
        "inline_keyboard": [[{"text": "🛒 Buy on Flipkart / Grab Deal", "url": aff_link}]]
    }

    # Stream photo bytes into memory
    try:
        res = requests.get(deal["photo"], headers=HEADERS, timeout=8)
        if res.status_code == 200 and len(res.content) > 1000:
            files = {"photo": ("flipkart_deal.jpg", io.BytesIO(res.content), "image/jpeg")}
            data = {
                "chat_id": CHANNEL_ID,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(reply_markup)
            }
            resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=12)
            if resp.status_code == 200:
                log(f"✅ Flipkart Deal Posted with HD Photo: {clean_title[:35]}")
                return True
            else:
                log(f"Telegram Photo Error: {resp.text}")
    except Exception as e:
        log(f"Photo download error: {e}")

    # Fallback to text message if photo times out
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

# --- 6. 24/7 Automated Posting Worker ---
def continuous_flipkart_poster():
    log("Flipkart automated deals poster engine started...")
    time.sleep(3)

    while True:
        try:
            pool = FLIPKART_CATALOG.copy()
            random.shuffle(pool)

            # Auto-reset database if all deals are posted to keep loop going
            if all(is_already_posted(d["id"]) for d in pool):
                log("All Flipkart deals cycle completed. Resetting database for continuous rotation...")
                clear_db()

            for deal in pool:
                if not is_already_posted(deal["id"]):
                    if send_flipkart_deal(deal):
                        mark_as_posted(deal["id"])
                        time.sleep(4)
                        break

        except Exception as e:
            log(f"Flipkart worker loop error: {e}")

        # Post next deal every 3 minutes
        time.sleep(180)

# --- 7. Keep-Alive Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Flipkart Loot Bot Engine Live 24/7!")
    def log_message(self, format, *args):
        return

def main():
    init_db()
    log("Flipkart Database initialized.")

    poster_thread = threading.Thread(target=continuous_flipkart_poster, daemon=True)
    poster_thread.start()

    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    log(f"Health check running on port {PORT}")
    server.serve_forever()

if __name__ == "__main__":
    main()
