import os
import re
import asyncio
import sqlite3
import threading
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

# Handle numeric vs string channel IDs
try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = os.getenv("EARNKARO_ID", "").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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

# --- Affiliate Generator ---
def convert_to_affiliate(original_url):
    if not original_url:
        return "https://www.amazon.in"
    if EARNKARO_ID:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}&r={EARNKARO_ID}"
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- Live Deals Fetcher ---
def fetch_live_deals():
    deals = []
    try:
        res = requests.get("https://www.desidime.com/feed", headers=HEADERS, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:10]:
                title = item.find('title').text.strip() if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                link = item.find('link').text.strip() if item.find('link') is not None else ""
                
                soup = BeautifulSoup(desc, "html.parser")
                img_tag = soup.find("img")
                img_url = img_tag.get("src") if img_tag else ""
                
                store_tag = soup.find("a", href=True)
                target_url = store_tag['href'] if store_tag else link

                if title and target_url:
                    deals.append({
                        "id": link,
                        "title": title,
                        "url": target_url,
                        "image": img_url
                    })
    except Exception as e:
        print(f"Scraper error: {e}")

    # Guaranteed Fallback Deals if Web Stream is Empty
    if len(deals) == 0:
        deals = [
            {
                "id": "boat_141_deal",
                "title": "boAt Airdopes 141 Bluetooth TWS (42H Playtime, Low Latency)",
                "url": "https://www.amazon.in/dp/B09N3ZNHTY",
                "image": "https://m.media-amazon.com/images/I/61KNJav3S9L._SL1500_.jpg"
            },
            {
                "id": "noise_pulse_deal",
                "title": "Noise ColorFit Pulse 2 Max 1.85'' Smart Watch (Bluetooth Calling)",
                "url": "https://www.amazon.in/dp/B0B6BLTGTT",
                "image": "https://m.media-amazon.com/images/I/61akt30bJsL._SL1500_.jpg"
            },
            {
                "id": "sandisk_64gb_deal",
                "title": "SanDisk Cruzer Blade 64GB USB 2.0 Flash Drive",
                "url": "https://www.amazon.in/dp/B0083PR5VC",
                "image": "https://m.media-amazon.com/images/I/61DjwgS4cbL._SL1500_.jpg"
            }
        ]
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
        clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])
        caption = (
            f"🔥 **SUPER LOOT DEAL** 🔥\n\n"
            f"📦 **{clean_title}**\n\n"
            f"⚡ *Limited Stock Deal! Jaldi Grab Karein!*"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=aff_link)]])

        try:
            if deal.get("image") and deal["image"].startswith("http"):
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=deal["image"],
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
            mark_as_posted(deal["id"])
            posted_count += 1
            await asyncio.sleep(2)

            if force and posted_count >= 3:
                break
        except Exception as e:
            err_message = str(e)
            print(f"Direct Post Error: {e}")
            break

    if chat_to_notify:
        if err_message:
            await bot.send_message(
                chat_id=chat_to_notify,
                text=f"❌ **Channel Post Error:**\n`{err_message}`\n\n👉 Make sure bot is **Admin** in channel `{CHANNEL_ID}` with 'Post Messages' ON.",
                parse_mode="Markdown"
            )
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Deals channel mein successfully post ho gayi hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari latest deals posted hain. Nayi deal aate hi auto post hogi.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Loot Deals Engine Active!**\nUse `/postnow` to trigger instantly.")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Deals fetch karke channel par bhej rahe hain...")
    await post_deals_to_channel(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Cache cleared! Send `/postnow`.")

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

    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    print("Bot is polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
