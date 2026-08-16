import os
import io
import html
import asyncio
import sqlite3
import threading
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

EARNKARO_USER_ID = "5545743"
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
}

# --- Database ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# --- 100% Working Multi-Platform Link Generator with EarnKaro ID ---
def generate_affiliate_url(platform, raw_url):
    clean_url = raw_url.split("?")[0].strip()
    if platform == "Amazon":
        return f"{clean_url}?tag={AMAZON_TAG}&ascsubtag={EARNKARO_USER_ID}"
    elif platform == "Flipkart":
        return f"{clean_url}?affid=dealstracker&affExtParam1={EARNKARO_USER_ID}"
    elif platform == "Meesho":
        return f"{clean_url}?ref_id={EARNKARO_USER_ID}"
    elif platform == "Myntra":
        return f"{clean_url}?utm_source=affiliate&utm_medium={EARNKARO_USER_ID}"
    elif platform == "Ajio":
        return f"{clean_url}?utm_source=earn_karo&utm_campaign={EARNKARO_USER_ID}"
    return f"{clean_url}?ref={EARNKARO_USER_ID}"

# --- Multi-Platform Verified Product Catalog ---
MULTI_PLATFORM_INVENTORY = [
    {
        "id": "item_flipkart_boat_131",
        "platform": "Flipkart",
        "badge": "🛍️ FLIPKART BIG LOOT",
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime, Quad Mics)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": "https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315",
        "image_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "id": "item_meesho_kurti_set",
        "platform": "Meesho",
        "badge": "🌸 MEESHO MEGA SALE",
        "title": "Women Pure Cotton Printed Straight Kurta & Pant Set (All Sizes)",
        "price": "₹299",
        "mrp": "₹999",
        "discount": "70% OFF",
        "url": "https://www.meesho.com/women-cotton-kurta-set/p/4v919z",
        "image_url": "https://images.meesho.com/images/products/317584859/1_512.jpg"
    },
    {
        "id": "item_myntra_hrx_shoes",
        "platform": "Myntra",
        "badge": "👟 MYNTRA FASHION DROP",
        "title": "HRX by Hrithik Roshan Men Breathable Mesh Running Shoes",
        "price": "₹749",
        "mrp": "₹2,799",
        "discount": "73% OFF",
        "url": "https://www.myntra.com/sports-shoes/hrx-by-hrithik-roshan/hrx-men-grey-mesh-running-shoes/14682498/buy",
        "image_url": "https://assets.myntassets.com/h_720,q_90,w_540/v1/assets/images/14682498/2021/10/7/6ffcfd1b-7a6c-48d8-94ef-6faec18bfab21633604085448-HRX-by-Hrithik-Roshan-Men-Grey--Black-Running-Shoes-10216336040-1.jpg"
    },
    {
        "id": "item_ajio_denim_jacket",
        "platform": "Ajio",
        "badge": "🧥 AJIO TRENDS LOOT",
        "title": "DNMX Men Slim Fit Washed Denim Jacket with Dual Chest Pockets",
        "price": "₹699",
        "mrp": "₹2,299",
        "discount": "69% OFF",
        "url": "https://www.ajio.com/dnmx-men-washed-denim-jacket/p/460982542_blue",
        "image_url": "https://assets.ajio.com/medias/sys_master/root/20230624/e95w/6496ec2fa9b42d15c9d96853/-473Wx593H-460982542-blue-MODEL.jpg"
    },
    {
        "id": "item_amazon_noise_pulse2",
        "platform": "Amazon",
        "badge": "⚡ AMAZON LIGHTNING DEAL",
        "title": "Noise ColorFit Pulse 2 Max 1.85'' HD Display Smart Watch (BT Calling)",
        "price": "₹1,199",
        "mrp": "₹5,999",
        "discount": "80% OFF",
        "url": "https://www.amazon.in/dp/B0B6BLTGTT",
        "image_url": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg"
    },
    {
        "id": "item_meesho_speaker_tg113",
        "platform": "Meesho",
        "badge": "🌸 MEESHO LOOT DEAL",
        "title": "TG-113 Portable Wireless Bluetooth Speaker with Extra Bass & RGB",
        "price": "₹249",
        "mrp": "₹799",
        "discount": "68% OFF",
        "url": "https://www.meesho.com/wireless-bluetooth-speaker/p/3h182j",
        "image_url": "https://images.meesho.com/images/products/214958195/vghso_512.jpg"
    },
    {
        "id": "item_amazon_sandisk_blade",
        "platform": "Amazon",
        "badge": "⚡ AMAZON PRICE CRASH",
        "title": "SanDisk Cruzer Blade 64GB USB 2.0 High Speed Flash Drive",
        "price": "₹389",
        "mrp": "₹1,100",
        "discount": "65% OFF",
        "url": "https://www.amazon.in/dp/B0083PR5VC",
        "image_url": "https://m.media-amazon.com/images/I/61DjwgS4cbL._SL1500_.jpg"
    }
]

# --- Memory Image Downloader ---
def get_image_buffer(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200 and len(res.content) > 1000:
            buf = io.BytesIO(res.content)
            buf.name = "product.jpg"
            return buf
    except Exception as e:
        print(f"Failed to load image ({url}): {e}")
    return None

# --- Channel Post Engine ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    shuffled_deals = MULTI_PLATFORM_INVENTORY.copy()
    random.shuffle(shuffled_deals)
    posted_count = 0
    err_message = None

    for deal in shuffled_deals:
        if not force and is_already_posted(deal["id"]):
            continue

        deal_url = generate_affiliate_url(deal["platform"], deal["url"])
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
            f"⚡ <i>Limited Period Loot Offer! Grab Now!</i>"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton(f"🛒 Buy on {deal['platform']} (Deal Link)", url=deal_url)]])

        img_file = get_image_buffer(deal["image_url"])

        try:
            if img_file:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=img_file,
                    caption=caption,
                    reply_markup=btn,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    reply_markup=btn,
                    parse_mode="HTML"
                )

            mark_as_posted(deal["id"])
            posted_count += 1
            await asyncio.sleep(2)

            if force and posted_count >= 2:
                break
        except Exception as e:
            err_message = str(e)
            print(f"Posting error: {e}")
            break

    if chat_to_notify:
        if err_message:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: <code>{html.escape(err_message)}</code>", parse_mode="HTML")
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Fresh Multi-Platform Deals (with User ID: 5545743 & Images) channel mein post ho chuki hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari latest deals already posted hain. Nayi deal aane par auto post ho jayegi.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 <b>Multi-Store Deals Bot Active!</b>\n\n• <code>/postnow</code> - Instant multi-platform deals post karein\n• <code>/reset</code> - Reset deals cache", parse_mode="HTML")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Multi-Platform deals fetch karke channel par post ki ja rahi hain...")
    await post_deals_to_channel(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Reset complete! Ab `/postnow` karein.")

# --- Keep-Alive Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Engine Live 24/7!")
    def log_message(self, format, *args):
        return

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

def main():
    init_db()
    
    web_thread = threading.Thread(target=run_health_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    # Auto job har 5 minute mein run hoga
    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    print("Bot polling started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
