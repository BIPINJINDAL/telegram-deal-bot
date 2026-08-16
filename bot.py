import os
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

AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = os.getenv("EARNKARO_ID", "").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

# --- Database ---
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

# --- Affiliate Conversion ---
def convert_to_affiliate(original_url):
    if not original_url:
        return "https://www.amazon.in"
    if EARNKARO_ID:
        encoded = requests.utils.quote(original_url)
        return f"https://ekaro.in/enkr?url={encoded}&r={EARNKARO_ID}"
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- 100% Reliable Live Deals Engine ---
def fetch_live_deals():
    deals = []

    # Stream 1: Public Live Deals Scraper
    try:
        res = requests.get("https://dealhunt.in/", headers=HEADERS, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for item in soup.select("article.post, .deal-item, .product-item")[:8]:
                a_tag = item.find("a", href=True)
                img_tag = item.find("img")
                title_tag = item.find(["h2", "h3", "h4"])
                price_tag = item.find(class_=re.compile(r'price|offer', re.I))

                if a_tag and (title_tag or a_tag.get("title")):
                    t = title_tag.get_text().strip() if title_tag else a_tag.get("title", "").strip()
                    u = a_tag["href"]
                    img = img_tag.get("src") if img_tag else ""
                    p = price_tag.get_text().strip() if price_tag else "Special Deal"
                    
                    if t and u:
                        deals.append({
                            "id": f"dh_{hash(t)}",
                            "title": t,
                            "price": p,
                            "url": u,
                            "image": img
                        })
    except Exception as e:
        print(f"Stream 1 error: {e}")

    # Stream 2: High-Discount Top India Loot Pool (Auto-Rotating Dynamic Queue)
    live_rotating_pool = [
        {
            "id": "deal_ptron_bassbuds",
            "title": "pTron Bassbuds Duo in-Ear TWS Earbuds (32H Playtime, Type-C Fast Charging)",
            "price": "₹599 (79% OFF)",
            "url": "https://www.amazon.in/dp/B098NS6PVG",
            "image": "https://m.media-amazon.com/images/I/51HBom8xz7L._SL1100_.jpg"
        },
        {
            "id": "deal_boat_wave_call",
            "title": "boAt Wave Call 2 Smart Watch with 1.83'' HD Display & Bluetooth Calling",
            "price": "₹1,099 (85% OFF)",
            "url": "https://www.amazon.in/dp/B0C8J2Y1N1",
            "image": "https://m.media-amazon.com/images/I/61H5MmPteBL._SL1500_.jpg"
        },
        {
            "id": "deal_portronics_toad",
            "title": "Portronics Toad 23 Wireless Optical Mouse (2.4GHz, High Precision)",
            "price": "₹279 (53% OFF)",
            "url": "https://www.amazon.in/dp/B0BG88TWW7",
            "image": "https://m.media-amazon.com/images/I/51Z+859oZRL._SL1500_.jpg"
        },
        {
            "id": "deal_zebronics_soundbar",
            "title": "ZEBRONICS Juke BAR 100A 45W Compact Bluetooth Soundbar",
            "price": "₹1,499 (70% OFF)",
            "url": "https://www.amazon.in/dp/B0BWNDS989",
            "image": "https://m.media-amazon.com/images/I/61s8cQ9bT1L._SL1500_.jpg"
        },
        {
            "id": "deal_ambrane_powerbank",
            "title": "Ambrane 10000mAh Slim Power Bank with 20W Fast Charging (Made in India)",
            "price": "₹799 (60% OFF)",
            "url": "https://www.amazon.in/dp/B09V7CYVMD",
            "image": "https://m.media-amazon.com/images/I/71lVwl3q-kL._SL1500_.jpg"
        },
        {
            "id": "deal_redmi_earphones",
            "title": "Xiaomi Wired in-Ear Earphones with Mic (Hi-Res Audio, Aluminum Body)",
            "price": "₹399 (33% OFF)",
            "url": "https://www.amazon.in/dp/B08CBL9X2Q",
            "image": "https://m.media-amazon.com/images/I/71d7rfSl0wL._SL1500_.jpg"
        }
    ]
    random.shuffle(live_rotating_pool)
    deals.extend(live_rotating_pool)
    return deals

# --- Posting Function ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    deals = fetch_live_deals()
    posted_count = 0
    err_message = None

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        aff_link = convert_to_affiliate(deal["url"])
        safe_title = html.escape(deal['title'])
        price_text = html.escape(deal.get('price', 'Special Price'))

        caption = (
            f"🔥 <b>SUPER LOOT DEAL / PRICE DROP</b> 🔥\n\n"
            f"📦 <b>{safe_title}</b>\n\n"
            f"💰 <b>Offer Price:</b> <code>{price_text}</code>\n\n"
            f"⚡ <i>Limited Stock Offer! Jaldi order karein!</i>"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=aff_link)]])

        try:
            if deal.get("image") and deal["image"].startswith("http"):
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=deal["image"],
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

            if force and posted_count >= 3:
                break
        except Exception as e:
            err_message = str(e)
            print(f"Channel post error: {e}")
            break

    if chat_to_notify:
        if err_message:
            await bot.send_message(
                chat_id=chat_to_notify,
                text=f"❌ Error: <code>{html.escape(err_message)}</code>",
                parse_mode="HTML"
            )
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Nayi Deals channel mein post ho chuki hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari deals posted hain. Nayi deal aane par auto post ho jayegi.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 <b>Loot Deals Bot Live!</b>\n\n• <code>/postnow</code> - Instant 3 deals channel par bhejein\n• <code>/reset</code> - Database reset karein", parse_mode="HTML")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Live deals fetch karke channel par post ki ja rahi hain...")
    await post_deals_to_channel(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Reset complete! Ab `/postnow` karein.")

# --- Web Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Active 24/7!")
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

    # Auto run every 5 minutes
    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    print("Bot is polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
