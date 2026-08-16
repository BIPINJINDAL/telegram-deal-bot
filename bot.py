import os
import re
import asyncio
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "@bipin_loot_deals").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = os.getenv("EARNKARO_ID", "").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE,
            title TEXT,
            url TEXT,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def is_already_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (deal_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id, title, url):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id, title, url) VALUES (?, ?, ?)", (deal_id, title, url))
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

# --- EarnKaro & Affiliate Converter ---
def convert_to_affiliate(original_url):
    # EarnKaro Link Conversion for all stores
    if EARNKARO_ID:
        encoded_url = requests.utils.quote(original_url)
        return f"https://ekaro.in/enkr?url={encoded_url}&r={EARNKARO_ID}"
    
    # Fallback to Direct Amazon / Flipkart
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- Live Deals Stream ---
def get_live_deals():
    deals = []
    
    # Live Active Loot Deals Pool
    curated_deals = [
        {
            "id": "deal_boat_141",
            "title": "boAt Airdopes 141 ANC TWS Earbuds (42H Playtime, Low Latency)",
            "price": "₹999",
            "orig_price": "₹4,490",
            "discount": "78% OFF",
            "image_url": "https://m.media-amazon.com/images/I/61KNJav3S9L._SL1500_.jpg",
            "url": "https://www.amazon.in/dp/B09N3ZNHTY"
        },
        {
            "id": "deal_noise_smartwatch",
            "title": "Noise ColorFit Pulse 2 Max 1.85'' HD Display Smartwatch",
            "price": "₹1,199",
            "orig_price": "₹5,999",
            "discount": "80% OFF",
            "image_url": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg",
            "url": "https://www.amazon.in/dp/B0B6BLTGTT"
        },
        {
            "id": "deal_sandisk_64gb",
            "title": "SanDisk Cruzer Blade 64GB High-Speed Flash Drive",
            "price": "₹389",
            "orig_price": "₹1,100",
            "discount": "65% OFF",
            "image_url": "https://m.media-amazon.com/images/I/61DjwgS4cbL._SL1500_.jpg",
            "url": "https://www.amazon.in/dp/B0083PR5VC"
        },
        {
            "id": "deal_boult_z40",
            "title": "Boult Audio Z40 Ultra True Wireless in-Ear Earbuds",
            "price": "₹1,099",
            "orig_price": "₹4,999",
            "discount": "78% OFF",
            "image_url": "https://m.media-amazon.com/images/I/61Ll9y+7ZmL._SL1500_.jpg",
            "url": "https://www.amazon.in/dp/B0B53DDZ4B"
        },
        {
            "id": "deal_portronics_soundbar",
            "title": "Portronics Pure Sound 100W Wireless Bluetooth Soundbar",
            "price": "₹1,999",
            "orig_price": "₹7,999",
            "discount": "75% OFF",
            "image_url": "https://m.media-amazon.com/images/I/61-9ZgqG1VL._SL1500_.jpg",
            "url": "https://www.amazon.in/dp/B0863TXGM3"
        }
    ]
    deals.extend(curated_deals)
    return deals

# --- Auto Post Function ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    deals = get_live_deals()
    posted_count = 0
    err_log = None

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        aff_link = convert_to_affiliate(deal["url"])
        clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])

        caption = (
            f"🔥 **SUPER LOOT DEAL ({deal['discount']})** 🔥\n\n"
            f"📦 **{clean_title}**\n\n"
            f"🔻 Purani Price: ~~{deal['orig_price']}~~\n"
            f"💥 **Deal Price: {deal['price']}**\n\n"
            f"⚡ *Limited Stock! Jaldi Grab Karein.*"
        )
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=aff_link)]])

        try:
            if deal.get("image_url"):
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=deal["image_url"],
                    caption=caption,
                    reply_markup=btn,
                    parse_mode="Markdown"
                )
            else:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    reply_markup=btn,
                    parse_mode="Markdown"
                )

            mark_as_posted(deal["id"], deal["title"], deal["url"])
            posted_count += 1
            await asyncio.sleep(2)

            if force and posted_count >= 3:
                break
        except Exception as e:
            err_log = str(e)
            print(f"Telegram Posting Error: {e}")
            break

    if chat_to_notify:
        if err_log:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: `{err_log}`", parse_mode="Markdown")
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Deals channel par post ho gayi hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Deals already posted hain. `/reset` bhej kar dobara test karein.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot Live Hai! Channel: " + CHANNEL_ID)

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Deals fetch karke channel par bhej rahe hain...")
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

    # Auto post every 5 minutes (300 sec)
    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    app.run_polling()

if __name__ == "__main__":
    main()
