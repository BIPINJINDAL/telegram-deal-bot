import os
import io
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

AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = "5545743"
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
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

def clear_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- Affiliate URL Generator ---
def build_clean_affiliate_link(platform, base_url):
    clean_url = base_url.split("?")[0].strip()
    if platform == "Amazon":
        return f"{clean_url}?tag={AMAZON_TAG}&ascsubtag={EARNKARO_ID}"
    elif platform == "Flipkart":
        return f"{clean_url}?affid=dealstracker&affExtParam1={EARNKARO_ID}"
    elif platform == "Myntra":
        return f"{clean_url}?utm_source=affiliate&utm_medium={EARNKARO_ID}"
    elif platform == "Ajio":
        return f"{clean_url}?utm_source=earn_karo&utm_campaign={EARNKARO_ID}"
    elif platform == "Meesho":
        return f"{clean_url}?ref_id={EARNKARO_ID}"
    return f"{clean_url}?tag={AMAZON_TAG}"

# --- All Platforms Verified Active Inventory ---
ALL_PLATFORMS_DEALS = [
    {
        "id": "amz_boat_141",
        "platform": "Amazon",
        "badge": "⚡ AMAZON LIGHTNING DEAL",
        "title": "boAt Airdopes 141 Bluetooth TWS (42H Playtime, Low Latency, Fast Charge)",
        "price": "₹999",
        "mrp": "₹4,490",
        "discount": "78% OFF",
        "url": "https://www.amazon.in/dp/B09N3ZNHTY",
        "image_url": "https://m.media-amazon.com/images/I/61KNJav3S9L._SL1500_.jpg"
    },
    {
        "id": "fk_boat_131_pro",
        "platform": "Flipkart",
        "badge": "🛍️ FLIPKART BIG SAVINGS",
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime, Beast Mode)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": "https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315",
        "image_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "id": "amz_noise_pulse2",
        "platform": "Amazon",
        "badge": "⚡ AMAZON PRICE DROP",
        "title": "Noise ColorFit Pulse 2 Max 1.85'' HD Display Smart Watch (BT Calling)",
        "price": "₹1,199",
        "mrp": "₹5,999",
        "discount": "80% OFF",
        "url": "https://www.amazon.in/dp/B0B6BLTGTT",
        "image_url": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg"
    },
    {
        "id": "myntra_men_kurta_set",
        "platform": "Myntra",
        "badge": "👟 MYNTRA FESTIVE LOOT",
        "title": "Anouk Men Solid Pure Cotton Straight Kurta & Pyjama Set",
        "price": "₹799",
        "mrp": "₹2,499",
        "discount": "68% OFF",
        "url": "https://www.myntra.com/kurta-sets/anouk/anouk-men-solid-pure-cotton-kurta-with-pyjamas/13745230/buy",
        "image_url": "https://assets.myntassets.com/h_720,q_90,w_540/v1/assets/images/13745230/2021/4/27/5ff9cf1a-e99d-472e-8395-5cb9c20a4b081619522194883-Anouk-Men-Grey-Solid-Straight-Kurta-with-Pyjamas-6171619522194-1.jpg"
    },
    {
        "id": "ajio_denim_jacket_men",
        "platform": "Ajio",
        "badge": "🧥 AJIO TRENDS DROP",
        "title": "DNMX Men Slim Fit Washed Denim Jacket with Flap Pockets",
        "price": "₹699",
        "mrp": "₹2,299",
        "discount": "69% OFF",
        "url": "https://www.ajio.com/dnmx-men-washed-denim-jacket/p/460982542_blue",
        "image_url": "https://assets.ajio.com/medias/sys_master/root/20230624/e95w/6496ec2fa9b42d15c9d96853/-473Wx593H-460982542-blue-MODEL.jpg"
    },
    {
        "id": "meesho_women_kurta_pant",
        "platform": "Meesho",
        "badge": "🌸 MEESHO MEGA LOOT",
        "title": "Women Pure Cotton Printed Straight Kurta & Pant Combo Set",
        "price": "₹299",
        "mrp": "₹999",
        "discount": "70% OFF",
        "url": "https://www.meesho.com/women-cotton-kurta-set/p/4v919z",
        "image_url": "https://images.meesho.com/images/products/317584859/1_512.jpg"
    },
    {
        "id": "amz_ambrane_powerbank",
        "platform": "Amazon",
        "badge": "🔋 POWERBANK PRICE CRASH",
        "title": "Ambrane 10000mAh Slim Power Bank with 20W Fast Charging (Made in India)",
        "price": "₹799",
        "mrp": "₹1,999",
        "discount": "60% OFF",
        "url": "https://www.amazon.in/dp/B09V7CYVMD",
        "image_url": "https://m.media-amazon.com/images/I/71lVwl3q-kL._SL1500_.jpg"
    },
    {
        "id": "amz_portronics_mouse",
        "platform": "Amazon",
        "badge": "🔥 ACCESSORIES LOOT",
        "title": "Portronics Toad 23 Wireless Optical Mouse (2.4GHz High Precision)",
        "price": "₹279",
        "mrp": "₹599",
        "discount": "53% OFF",
        "url": "https://www.amazon.in/dp/B0BG88TWW7",
        "image_url": "https://m.media-amazon.com/images/I/51Z+859oZRL._SL1500_.jpg"
    }
]

# --- Direct Telegram HTTP Poster (100% Conflict Free) ---
def send_deal_direct(deal):
    deal_url = build_clean_affiliate_link(deal["platform"], deal["url"])
    safe_badge = html.escape(deal['badge'])
    safe_title = html.escape(deal['title'])
    price_text = html.escape(deal['price'])
    mrp_text = html.escape(deal['mrp'])
    discount_text = html.escape(deal['discount'])

    caption = (
        f"🔥 <b>{safe_badge} ({discount_text})</b> 🔥\n\n"
        f"📦 <b>{safe_title}</b>\n\n"
        f"🔻 MRP: <s>{mrp_text}</s>\n"
        f"💥 <b>Offer Price: {price_text}</b>\n\n"
        f"⚡ <i>Limited Period Loot Deal! Jaldi Grab Karein!</i>"
    )
    
    reply_markup = {
        "inline_keyboard": [[{"text": f"🛒 Buy on {deal['platform']} / Grab Deal", "url": deal_url}]]
    }

    try:
        res = requests.get(deal["image_url"], headers=HEADERS, timeout=8)
        if res.status_code == 200 and len(res.content) > 1000:
            files = {"photo": ("product.jpg", io.BytesIO(res.content), "image/jpeg")}
            data = {
                "chat_id": CHANNEL_ID,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": requests.utils.json.dumps(reply_markup)
            }
            resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=12)
            return resp.status_code == 200
    except Exception as e:
        print(f"Direct photo post error: {e}")

    # Fallback to sendMessage if photo upload times out
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
        print(f"Direct message post error: {e}")
        return False

# --- 24/7 Automated Posting Loop ---
def continuous_deals_poster():
    while True:
        try:
            pool = ALL_PLATFORMS_DEALS.copy()
            random.shuffle(pool)
            
            # Agar sabhi deals post ho chuki hain, toh cache auto-clear karke fresh loop chalu karein
            all_posted = all(is_already_posted(d["id"]) for d in pool)
            if all_posted:
                print("All catalog deals posted. Rotating cycle...")
                clear_db()

            for deal in pool:
                if not is_already_posted(deal["id"]):
                    success = send_deal_direct(deal)
                    if success:
                        mark_as_posted(deal["id"])
                        print(f"Successfully posted on channel: {deal['title']}")
                        time.sleep(3)
                        break
        except Exception as e:
            print(f"Background engine error: {e}")
        
        # Har 5 minute (300 seconds) mein 1 fresh deal auto-post karega
        time.sleep(300)

# --- Keep-Alive Health Server for UptimeRobot ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Engine Live 24/7 (Conflict-Free)")
    def log_message(self, format, *args):
        return

def main():
    init_db()
    
    # 1. Start posting loop in background thread
    poster_thread = threading.Thread(target=continuous_deals_poster, daemon=True)
    poster_thread.start()

    # 2. Start HTTP Health Server for 24/7 Uptime
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    print(f"Health Server running on port {PORT}...")
    server.serve_forever()

if __name__ == "__main__":
    main()
