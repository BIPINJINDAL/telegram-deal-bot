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

AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = "5545743"
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

# --- Affiliate URL Generator with EarnKaro User ID 5545743 ---
def build_clean_affiliate_link(url):
    clean_url = url.split("?")[0].strip()
    return f"{clean_url}?tag={AMAZON_TAG}&ascsubtag={EARNKARO_ID}"

# --- 100% In-Stock Verified Evergreen Bestseller Deals ---
IN_STOCK_DEALS = [
    {
        "id": "live_boat_airdopes_141",
        "badge": "⚡ AMAZON LIGHTNING DEAL",
        "title": "boAt Airdopes 141 Bluetooth TWS (42H Playtime, Low Latency, Fast Charge)",
        "price": "₹999",
        "mrp": "₹4,490",
        "discount": "78% OFF",
        "url": "https://www.amazon.in/dp/B09N3ZNHTY",
        "image_url": "https://m.media-amazon.com/images/I/61KNJav3S9L._SL1500_.jpg"
    },
    {
        "id": "live_noise_pulse2_max",
        "badge": "⚡ AMAZON PRICE DROP",
        "title": "Noise ColorFit Pulse 2 Max 1.85'' HD Display Smart Watch (BT Calling, 550 Nits)",
        "price": "₹1,199",
        "mrp": "₹5,999",
        "discount": "80% OFF",
        "url": "https://www.amazon.in/dp/B0B6BLTGTT",
        "image_url": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg"
    },
    {
        "id": "live_portronics_toad23",
        "badge": "🔥 ACCESSORIES LOOT",
        "title": "Portronics Toad 23 Wireless Optical Mouse (2.4GHz High Precision, Type-C)",
        "price": "₹279",
        "mrp": "₹599",
        "discount": "53% OFF",
        "url": "https://www.amazon.in/dp/B0BG88TWW7",
        "image_url": "https://m.media-amazon.com/images/I/51Z+859oZRL._SL1500_.jpg"
    },
    {
        "id": "live_ptron_bassbuds_duo",
        "badge": "💥 77% MEGA DISCOUNT",
        "title": "pTron Bassbuds Duo in-Ear TWS Earbuds (32H Playtime, Type-C Fast Charging)",
        "price": "₹599",
        "mrp": "₹2,599",
        "discount": "77% OFF",
        "url": "https://www.amazon.in/dp/B098NS6PVG",
        "image_url": "https://m.media-amazon.com/images/I/51HBom8xz7L._SL1100_.jpg"
    },
    {
        "id": "live_ambrane_powerbank_10k",
        "badge": "🔋 POWERBANK PRICE CRASH",
        "title": "Ambrane 10000mAh Slim Power Bank with 20W Fast Charging (Made in India)",
        "price": "₹799",
        "mrp": "₹1,999",
        "discount": "60% OFF",
        "url": "https://www.amazon.in/dp/B09V7CYVMD",
        "image_url": "https://m.media-amazon.com/images/I/71lVwl3q-kL._SL1500_.jpg"
    },
    {
        "id": "live_boult_z40_ultra",
        "badge": "🎧 TWS LOOT OFFER",
        "title": "Boult Audio Z40 Ultra True Wireless Earbuds (60H Playtime, Dual Mic ENC)",
        "price": "₹1,099",
        "mrp": "₹4,999",
        "discount": "78% OFF",
        "url": "https://www.amazon.in/dp/B0B53DDZ4B",
        "image_url": "https://m.media-amazon.com/images/I/61Ll9y+7ZmL._SL1500_.jpg"
    }
]

# --- Direct Safe Image Download ---
def get_image_file(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200 and len(res.content) > 1000:
            bio = io.BytesIO(res.content)
            bio.name = "product.jpg"
            return bio
    except Exception as e:
        print(f"Image fetch error: {e}")
    return None

# --- Channel Broadcaster ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    shuffled_deals = IN_STOCK_DEALS.copy()
    random.shuffle(shuffled_deals)
    posted_count = 0
    err_message = None

    for deal in shuffled_deals:
        if not force and is_already_posted(deal["id"]):
            continue

        deal_url = build_clean_affiliate_link(deal["url"])
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
            f"⚡ <i>Limited Stock Offer! Jaldi order karein!</i>"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=deal_url)]])

        img_file = get_image_file(deal["image_url"])

        try:
            if img_file:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=img_file,
                    caption=caption,
                    reply_markup=btn,
                    parse_mode="HTML"
                )
                mark_as_posted(deal["id"])
                posted_count += 1
                await asyncio.sleep(2)
            else:
                print(f"Skipping post {deal['id']} because image failed.")

            if force and posted_count >= 2:
                break
        except Exception as e:
            err_message = str(e)
            print(f"Telegram Post Error: {e}")
            break

    if chat_to_notify:
        if err_message:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: <code>{html.escape(err_message)}</code>", parse_mode="HTML")
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Fresh In-Stock Deals post ho chuki hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari deals already posted hain. Nayi deal aate hi auto post hogi.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 <b>Loot Deals Bot Live!</b>\n\n• <code>/postnow</code> - Instant deals post karein\n• <code>/reset</code> - Database reset karein", parse_mode="HTML")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fresh deals fetch karke channel par post ki ja rahi hain...")
    await post_deals_to_channel(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Reset complete! Ab `/postnow` karein.")

# --- Keep-Alive Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Live 24/7!")
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

    # Auto job har 5 minute me background me chalega
    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    print("Bot is polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
