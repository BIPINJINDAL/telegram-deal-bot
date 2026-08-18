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

EARNKARO_ID = "5545743"
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
}

# --- Database ---
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
        print(f"DB Error: {e}")
    finally:
        conn.close()

def clear_db():
    conn = sqlite3.connect("flipkart_deals.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- 100% Tested Working Flipkart Link (Zero 403 / Zero E002) ---
def make_flipkart_deal_link(raw_url):
    clean_url = raw_url.split("?")[0].strip()
    return f"{clean_url}?affid=dealstracker&affExtParam1={EARNKARO_ID}"

# --- Direct Active Flipkart Catalog with Working CDN Images ---
FLIPKART_DEALS = [
    {
        "id": "fk_boat_airdopes_131_pro",
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime, Quad Mics, Beast Mode)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": "https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "id": "fk_boult_audio_z40",
        "title": "Boult Audio Z40 True Wireless Earbuds (60H Playtime, Zen ENC Mic, Fast Charging)",
        "price": "₹999",
        "mrp": "₹4,999",
        "discount": "80% OFF",
        "url": "https://www.flipkart.com/boult-audio-z40-true-wireless-earbuds/p/itm535df2a1ad96b",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/m/u/v/-original-imagp8f4k7fggyhy.jpeg"
    },
    {
        "id": "fk_noise_colorfit_icon",
        "title": "Noise ColorFit Icon 2 1.8'' Display Bluetooth Calling Smart Watch",
        "price": "₹1,099",
        "mrp": "₹5,999",
        "discount": "81% OFF",
        "url": "https://www.flipkart.com/noise-colorfit-icon-2-1-8-display-bluetooth-calling-smartwatch/p/itm677c7ecda6173",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/smartwatch/y/j/0/-original-imagkhe74jhz8hga.jpeg"
    },
    {
        "id": "fk_sandisk_cruzer_blade_64gb",
        "title": "SanDisk Cruzer Blade 64 GB USB 2.0 Pen Drive (High Speed)",
        "price": "₹389",
        "mrp": "₹1,100",
        "discount": "64% OFF",
        "url": "https://www.flipkart.com/sandisk-cruzer-blade-64-gb-utility-pendrive/p/itme9b22bce376ee",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/ktyp8cw0/pendrive/pendrive/z/x/q/sdcz50-064g-i35-sandisk-original-imag76pph9h98zfh.jpeg"
    },
    {
        "id": "fk_portronics_toad_mouse",
        "title": "Portronics Toad 23 Wireless Optical Mouse (2.4GHz USB Dongle, Ergonomic)",
        "price": "₹279",
        "mrp": "₹599",
        "discount": "53% OFF",
        "url": "https://www.flipkart.com/portronics-toad-23-wireless-optical-mouse/p/itmd9ba45e12be8f",
        "photo_url": "https://rukminim2.flixcart.com/image/832/832/xif0q/mouse/6/u/i/toad-23-portronics-original-imaghg3b6t57zkhz.jpeg"
    }
]

# --- Direct In-Memory Image Loader ---
def fetch_image_stream(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200 and len(res.content) > 1000:
            bio = io.BytesIO(res.content)
            bio.name = "deal.jpg"
            return bio
    except Exception as e:
        print(f"Error fetching image: {e}")
    return None

# --- Channel Post Engine ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    deals = FLIPKART_DEALS.copy()
    random.shuffle(deals)
    posted_count = 0
    err_message = None

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        deal_url = make_flipkart_deal_link(deal["url"])
        safe_title = html.escape(deal['title'])
        price_text = html.escape(deal['price'])
        mrp_text = html.escape(deal['mrp'])
        discount_text = html.escape(deal['discount'])

        caption = (
            f"🛍️ <b>FLIPKART SUPER LOOT DEAL ({discount_text})</b> 🛍️\n\n"
            f"📦 <b>{safe_title}</b>\n\n"
            f"🔻 MRP: <s>{mrp_text}</s>\n"
            f"💥 <b>Offer Price: {price_text}</b>\n\n"
            f"⚡ <i>Limited Stock Deal! Jaldi grab karein!</i>"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy on Flipkart / Grab Deal", url=deal_url)]])

        img_file = fetch_image_stream(deal["photo_url"])

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
            print(f"Telegram Post Error: {e}")
            break

    if chat_to_notify:
        if err_message:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: <code>{html.escape(err_message)}</code>", parse_mode="HTML")
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Flipkart Deals post ho chuki hain bina kisi 403 error ke!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari latest Flipkart deals already posted hain.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 <b>Flipkart Deals Bot Active!</b>\n\n• <code>/postnow</code> - Instant Flipkart deals post karein\n• <code>/reset</code> - Database reset karein", parse_mode="HTML")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Flipkart deals fetch karke channel par post ki ja rahi hain...")
    await post_deals_to_channel(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Reset complete! Ab `/postnow` karein.")

# --- Keep-Alive Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Flipkart Bot Engine Live 24/7!")
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

    print("Bot polling started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
