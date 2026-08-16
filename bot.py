import os
import re
import json
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
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# --- Database ---
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
        c.execute("INSERT INTO posted_deals (deal_id, title, url) VALUES (?, ?, ?)", (deal_id, title, url))
        conn.commit()
    except:
        pass
    finally:
        conn.close()

# --- Affiliate Converter ---
def convert_to_affiliate(original_url):
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url or "fkrt.it" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- Live Deals Stream ---
def fetch_trending_loot_deals():
    deals = []
    try:
        res = requests.get("https://www.desidime.com/feed", headers=HEADERS, timeout=12)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:10]:
                deal_url = item.find('link').text.strip() if item.find('link') is not None else ""
                title = item.find('title').text.strip() if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                guid = item.find('guid').text if item.find('guid') is not None else deal_url

                soup = BeautifulSoup(desc, "html.parser")
                img_tag = soup.find("img")
                img_url = img_tag.get("src") if img_tag else ""

                store_link_tag = soup.find("a", href=True)
                target_url = store_link_tag['href'] if store_link_tag else deal_url

                discount_match = re.search(r'([0-9]{2}%|₹\s*[0-9,]+)', title)
                badge = discount_match.group(1) if discount_match else "HOT LOOT"

                if target_url and title:
                    deals.append({
                        "id": guid,
                        "title": title,
                        "url": target_url,
                        "image_url": img_url,
                        "badge": badge
                    })
    except Exception as e:
        print(f"Fetch Error: {e}")
    return deals

# --- Core Posting Function ---
async def post_deals_to_channel(bot):
    deals = fetch_trending_loot_deals()
    for deal in deals:
        if is_already_posted(deal["id"]):
            continue

        affiliate_link = convert_to_affiliate(deal["url"])
        caption = (
            f"🔥 **SUPER LOOT / PRICE DROP** 🔥\n\n"
            f"📦 **{deal['title']}**\n\n"
            f"⚡ **Offer:** `{deal['badge']}`\n"
            f"🚨 *Limited Time Deal! Grab Fast!*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=affiliate_link)]
        ])

        try:
            if deal.get("image_url") and deal["image_url"].startswith("http"):
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=deal["image_url"],
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )

            mark_as_posted(deal["id"], deal["title"], deal["url"])
            await asyncio.sleep(4)
        except Exception as e:
            print(f"Telegram Post Error: {e}")

# --- Background Repeating Task ---
async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot)

# --- Start & Force-Post Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Auto Deals Bot Live Hai!**\n\n"
        "Ye bot internet se live loot deals auto-fetch karke aapke channel par bhejta rahega.\n\n"
        "👉 Turant deal channel par check karne ke liye send karein: `/postnow`",
        parse_mode="Markdown"
    )

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Live deals fetch karke channel par post ki ja rahi hain...")
    await post_deals_to_channel(context.bot)
    await update.message.reply_text("✅ Deals channel par successfully post ho gayi hain!")

# --- Keep-Alive Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Auto Deal Bot is Live 24/7!")

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

    # Run every 5 minutes (300 seconds), starting immediately (first=2)
    app.job_queue.run_repeating(auto_job, interval=300, first=2)

    print("Bot is live...")
    app.run_polling()

if __name__ == "__main__":
    main()
