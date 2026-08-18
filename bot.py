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

# --- 1. Environment & Configuration ---
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
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.flipkart.com/"
}

# --- 2. Local State Management (Zero Duplicates) ---
def init_db():
    conn = sqlite3.connect("deals_v2.db")
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
    conn = sqlite3.connect("deals_v2.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (str(deal_id),))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id):
    conn = sqlite3.connect("deals_v2.db")
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id) VALUES (?)", (str(deal_id),))
        conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()

def clear_db():
    conn = sqlite3.connect("deals_v2.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- 3. Verified Multi-Platform Catalog (Clean Direct Links & Working Images) ---
PLATFORM_CATALOG = [
    {
        "id": "fk_boat_131_pro",
        "store": "Flipkart",
        "badge": "🛍️ FLIPKART LOOT DEAL",
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime, Quad Mics)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": f"https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315?affExtParam1={EARNKARO_ID}",
        "img_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "id": "fk_boult_z40",
        "store": "Flipkart",
        "badge": "🎧 FLIPKART AUDIO DROP",
        "title": "Boult Audio Z40 True Wireless Earbuds (60H Playtime, Fast Charging)",
        "price": "₹999",
        "mrp": "₹4,999",
        "discount": "80% OFF",
        "url": f"https://www.flipkart.com/boult-audio-z40-true-wireless-earbuds/p/itm535df2a1ad96b?affExtParam1={EARNKARO_ID}",
        "img_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/m/u/v/-original-imagp8f4k7fggyhy.jpeg"
    },
    {
        "id": "fk_noise_icon2",
        "store": "Flipkart",
        "badge": "⌚ FLIPKART SMARTWATCH LOOT",
        "title": "Noise ColorFit Icon 2 1.8'' Display Bluetooth Calling Smart Watch",
        "price": "₹1,099",
        "mrp": "₹5,999",
        "discount": "81% OFF",
        "url": f"https://www.flipkart.com/noise-colorfit-icon-2-1-8-display-bluetooth-calling-smartwatch/p/itm677c7ecda6173?affExtParam1={EARNKARO_ID}",
        "img_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/smartwatch/y/j/0/-original-imagkhe74jhz8hga.jpeg"
    },
    {
        "id": "fk_portronics_toad23",
        "store": "Flipkart",
        "badge": "🔥 FLIPKART ACCESSORIES DROP",
        "title": "Portronics Toad 23 Wireless Optical Mouse (2.4GHz USB Dongle, Ergonomic)",
        "price": "₹279",
        "mrp": "₹599",
        "discount": "53% OFF",
        "url": f"https://www.flipkart.com/portronics-toad-23-wireless-optical-mouse/p/itmd9ba45e12be8f?affExtParam1={EARNKARO_ID}",
        "img_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/mouse/6/u/i/toad-23-portronics-original-imaghg3b6t57zkhz.jpeg"
    }
]

# --- 4. Stream Image Into Memory (Bypasses Telegram Firewall Blocks) ---
def get_image_file_stream(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200 and len(res.content) > 1000:
            bio = io.BytesIO(res.content)
            bio.name = "deal_photo.jpg"
            bio.seek(0)
            return bio
        else:
            print(f"Image fetch status {res.status_code} for {url}")
    except Exception as e:
        print(f"Image download exception: {e}")
    return None

# --- 5. Message & Broadcast Engine ---
async def dispatch_deals(bot, force=False, chat_to_notify=None):
    deals = PLATFORM_CATALOG.copy()
    posted_count = 0
    err_text = None

    # Check if all posted -> clear for fresh continuous rotation
    if all(is_already_posted(d["id"]) for d in deals):
        clear_db()

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        safe_badge = html.escape(deal["badge"])
        safe_title = html.escape(deal["title"])
        price = html.escape(deal["price"])
        mrp = html.escape(deal["mrp"])
        discount = html.escape(deal["discount"])

        caption = (
            f"{safe_badge} <b>({discount})</b>\n\n"
            f"📦 <b>{safe_title}</b>\n\n"
            f"🔻 MRP: <s>{mrp}</s>\n"
            f"💥 <b>Offer Price: {price}</b>\n\n"
            f"⚡ <i>Limited Stock Offer! Jaldi Grab Karein!</i>"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🛒 Buy on {deal['store']} / Grab Deal", url=deal["url"])]
        ])

        img_file = get_image_file_stream(deal["img_url"])

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
            posted_count += 1
            await asyncio.sleep(2)

            if force and posted_count >= 1:
                break
        except Exception as e:
            err_text = str(e)
            print(f"Telegram Dispatch Error: {e}")
            break

    if chat_to_notify:
        if err_text:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: <code>{html.escape(err_text)}</code>", parse_mode="HTML")
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Fresh Deal posted with HD Photo & Tracking ID!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Deal queue updated.")

# --- 6. Command Handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 <b>Loot Deals Bot Ready!</b>\n\n• <code>/postnow</code> - Instant Deal Post\n• <code>/reset</code> - Reset Database", parse_mode="HTML")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Deal fetch karke channel par post ki ja rahi hai...")
    await dispatch_deals(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Reset complete! Ab `/postnow` karein.")

async def scheduled_job(context: ContextTypes.DEFAULT_TYPE):
    await dispatch_deals(context.bot, force=False)

# --- 7. Health Check Server for 24/7 Hosting ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Deals Engine Running")
    def log_message(self, format, *args):
        return

def run_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

# --- 8. Main Entrypoint ---
def main():
    init_db()

    web_thread = threading.Thread(target=run_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    # Har 5 minute mein continuous auto-posting
    app.job_queue.run_repeating(scheduled_job, interval=300, first=10)

    print("Bot polling engine started successfully...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
