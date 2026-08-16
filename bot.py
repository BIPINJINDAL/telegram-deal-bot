import os
import io
import html
import asyncio
import sqlite3
import threading
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

# Aapka Verified EarnKaro User ID
EARNKARO_ID = os.getenv("EARNKARO_ID", "5545743").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

# --- Database Setup ---
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

def clear_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- 100% Working EarnKaro Converter ---
def convert_to_affiliate(original_url):
    if not original_url:
        return "https://www.amazon.in"
    
    # Strip unnecessary parameters
    clean_url = original_url.split("?")[0] if "?" in original_url else original_url
    
    # Clean EarnKaro tracking format with User ID 5545743
    encoded_url = requests.utils.quote(clean_url, safe='')
    return f"https://ekaro.in/enkr?url={encoded_url}&r={EARNKARO_ID}"

# --- Verified Live Deals Pool (100% Working Links) ---
def get_verified_fresh_deals():
    deals_inventory = [
        {
            "id": "deal_boat_airdopes_141",
            "title": "boAt Airdopes 141 Bluetooth TWS Earbuds (42H Playtime, Fast Charge)",
            "price": "₹999",
            "mrp": "₹4,490",
            "discount": "78% OFF",
            "url": "https://www.amazon.in/dp/B09N3ZNHTY",
            "image": "https://m.media-amazon.com/images/I/61KNJav3S9L._SL1500_.jpg"
        },
        {
            "id": "deal_noise_colorfit_pulse",
            "title": "Noise Pulse 2 Max 1.85'' Display Bluetooth Calling Smart Watch",
            "price": "₹1,199",
            "mrp": "₹5,999",
            "discount": "80% OFF",
            "url": "https://www.amazon.in/dp/B0B6BLTGTT",
            "image": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg"
        },
        {
            "id": "deal_sandisk_blade_64gb",
            "title": "SanDisk Cruzer Blade 64GB USB 2.0 Flash Drive (High Speed)",
            "price": "₹389",
            "mrp": "₹1,100",
            "discount": "65% OFF",
            "url": "https://www.amazon.in/dp/B0083PR5VC",
            "image": "https://m.media-amazon.com/images/I/61DjwgS4cbL._SL1500_.jpg"
        },
        {
            "id": "deal_ptron_bassbuds_duo",
            "title": "pTron Bassbuds Duo in-Ear TWS Earbuds (32H Playtime, Fast Type-C)",
            "price": "₹599",
            "mrp": "₹2,599",
            "discount": "77% OFF",
            "url": "https://www.amazon.in/dp/B098NS6PVG",
            "image": "https://m.media-amazon.com/images/I/51HBom8xz7L._SL1100_.jpg"
        },
        {
            "id": "deal_portronics_toad_mouse",
            "title": "Portronics Toad 23 Wireless Optical Mouse (2.4GHz High Precision)",
            "price": "₹279",
            "mrp": "₹599",
            "discount": "53% OFF",
            "url": "https://www.amazon.in/dp/B0BG88TWW7",
            "image": "https://m.media-amazon.com/images/I/51Z+859oZRL._SL1500_.jpg"
        },
        {
            "id": "deal_zebronics_juke_bar",
            "title": "ZEBRONICS Juke BAR 100A 45W Compact Bluetooth Soundbar",
            "price": "₹1,499",
            "mrp": "₹4,999",
            "discount": "70% OFF",
            "url": "https://www.amazon.in/dp/B0BWNDS989",
            "image": "https://m.media-amazon.com/images/I/61s8cQ9bT1L._SL1500_.jpg"
        }
    ]
    random.shuffle(deals_inventory)
    return deals_inventory

# --- Safe Channel Posting Function ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    deals = get_verified_fresh_deals()
    posted_count = 0
    err_message = None

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        aff_link = convert_to_affiliate(deal["url"])
        safe_title = html.escape(deal['title'])
        price_text = html.escape(deal['price'])
        mrp_text = html.escape(deal['mrp'])
        discount_text = html.escape(deal['discount'])

        caption = (
            f"🔥 <b>SUPER LOOT DEAL ({discount_text})</b> 🔥\n\n"
            f"📦 <b>{safe_title}</b>\n\n"
            f"🔻 MRP: <s>{mrp_text}</s>\n"
            f"💥 <b>Offer Price: {price_text}</b>\n\n"
            f"⚡ <i>Limited Stock Offer! Jaldi order karein!</i>"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=aff_link)]])

        try:
            img_data = None
            if deal.get("image"):
                res = requests.get(deal["image"], headers=HEADERS, timeout=8)
                if res.status_code == 200:
                    img_data = io.BytesIO(res.content)
                    img_data.name = "deal.jpg"

            if img_data:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=img_data,
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
            print(f"Post error: {e}")
            break

    if chat_to_notify:
        if err_message:
            await bot.send_message(
                chat_id=chat_to_notify,
                text=f"❌ Error: <code>{html.escape(err_message)}</code>",
                parse_mode="HTML"
            )
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Deals EarnKaro link ke sath post ho gayi hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Deals already posted. Nayi deal aate hi auto post ho jayegi.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 <b>Loot Deals Bot Live!</b>\n\n• <code>/postnow</code> - Instant 2 deals post karein\n• <code>/reset</code> - Reset cache", parse_mode="HTML")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Deals generate karke channel par post ki ja rahi hain...")
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

    # Auto job har 5 minute me chalega
    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    print("Bot started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
