import os
import io
import html
import asyncio
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1. Environment & Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

EARNKARO_ID = "5545743"
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
}

# --- 2. Database Management (Duplicate Prevention) ---
def init_db():
    conn = sqlite3.connect("deals_clean.db")
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
    conn = sqlite3.connect("deals_clean.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (str(deal_id),))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id):
    conn = sqlite3.connect("deals_clean.db")
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id) VALUES (?)", (str(deal_id),))
        conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()

def clear_db():
    conn = sqlite3.connect("deals_clean.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- 3. Clean Affiliate Link Formatter ---
def make_clean_link(raw_url):
    clean_url = raw_url.split("?")[0].strip()
    return f"{clean_url}?affExtParam1={EARNKARO_ID}"

# --- 4. Verified Flipkart Deals Pool ---
FLIPKART_CATALOG = [
    {
        "id": "deal_boat_131_pro",
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": "https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "id": "deal_boult_z40",
        "title": "Boult Audio Z40 True Wireless Earbuds (60H Playtime, Fast Charging)",
        "price": "₹999",
        "mrp": "₹4,999",
        "discount": "80% OFF",
        "url": "https://www.flipkart.com/boult-audio-z40-true-wireless-earbuds/p/itm535df2a1ad96b",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/m/u/v/-original-imagp8f4k7fggyhy.jpeg"
    },
    {
        "id": "deal_noise_icon2",
        "title": "Noise ColorFit Icon 2 1.8'' Bluetooth Calling Smart Watch",
        "price": "₹1,099",
        "mrp": "₹5,999",
        "discount": "81% OFF",
        "url": "https://www.flipkart.com/noise-colorfit-icon-2-1-8-display-bluetooth-calling-smartwatch/p/itm677c7ecda6173",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/smartwatch/y/j/0/-original-imagkhe74jhz8hga.jpeg"
    },
    {
        "id": "deal_sandisk_64gb",
        "title": "SanDisk Cruzer Blade 64 GB USB 2.0 Pen Drive",
        "price": "₹389",
        "mrp": "₹1,100",
        "discount": "64% OFF",
        "url": "https://www.flipkart.com/sandisk-cruzer-blade-64-gb-utility-pendrive/p/itme9b22bce376ee",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/ktyp8cw0/pendrive/pendrive/z/x/q/sdcz50-064g-i35-sandisk-original-imag76pph9h98zfh.jpeg"
    }
]

# --- 5. Safe Image Loader ---
def fetch_image(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200 and len(res.content) > 1000:
            bio = io.BytesIO(res.content)
            bio.name = "product.jpg"
            bio.seek(0)
            return bio
    except Exception as e:
        print(f"Image fetch log: {e}")
    return None

# --- 6. Post Handler ---
async def dispatch_deal(bot, force=False, chat_to_notify=None):
    deals = FLIPKART_CATALOG.copy()
    posted = False
    error_msg = None

    # Agar saari deals post ho chuki hain, toh list auto-renew ho jaye
    if all(is_already_posted(d["id"]) for d in deals):
        clear_db()

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        deal_link = make_clean_link(deal["url"])
        safe_title = html.escape(deal["title"])
        safe_price = html.escape(deal["price"])
        safe_mrp = html.escape(deal["mrp"])
        safe_discount = html.escape(deal["discount"])

        caption = (
            f"🛍️ <b>FLIPKART SUPER LOOT DEAL ({safe_discount})</b> 🛍️\n\n"
            f"📦 <b>{safe_title}</b>\n\n"
            f"🔻 MRP: <s>{safe_mrp}</s>\n"
            f"💥 <b>Offer Price: {safe_price}</b>\n\n"
            f"⚡ <i>Limited Stock Deal! Jaldi order karein!</i>"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy on Flipkart / Grab Deal", url=deal_link)]
        ])

        img_file = fetch_image(deal["photo_url"])

        try:
            if img_file:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=img_file,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

            mark_as_posted(deal["id"])
            posted = True
            break
        except Exception as e:
            error_msg = str(e)
            print(f"Telegram dispatch error: {e}")
            break

    if chat_to_notify:
        if error_msg:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: <code>{html.escape(error_msg)}</code>", parse_mode="HTML")
        elif posted:
            await bot.send_message(chat_id=chat_to_notify, text="✅ Deal successfully posted to channel with HD Photo & Tracking!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari deals already channel par posted hain.")

# --- 7. Telegram Bot Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Flipkart Loot Deals Bot Active!</b>\n\n"
        "• <code>/postnow</code> - Instant Deal Post Karein\n"
        "• <code>/reset</code> - Posted Deals History Clear Karein",
        parse_mode="HTML"
    )

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Deal fetch karke channel par post ki ja rahi hai...")
    await dispatch_deal(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Reset complete! Ab `/postnow` karein.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await dispatch_deal(context.bot, force=False)

# --- 8. Keep-Alive Server for Render ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Deals Engine Running 24/7")
    def log_message(self, format, *args):
        return

def run_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

# --- 9. Main Function ---
def main():
    init_db()

    web_thread = threading.Thread(target=run_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    # Background auto-post every 5 minutes
    app.job_queue.run_repeating(auto_job, interval=300, first=10)

    print("Bot polling started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
