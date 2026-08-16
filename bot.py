import os
import re
import sqlite3
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "@bipin_loot_deals").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = os.getenv("EARNKARO_ID", "").strip()
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = f"https://telegram-deal-bot-dh1q.onrender.com" # Apne Render URL se match karein

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS posted_deals (id INTEGER PRIMARY KEY, deal_id TEXT UNIQUE, title TEXT, url TEXT)")
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
    c.execute("INSERT OR REPLACE INTO posted_deals (deal_id, title, url) VALUES (?, ?, ?)", (deal_id, title, url))
    conn.commit()
    conn.close()

# --- Affiliate Logic ---
def convert_to_affiliate(original_url):
    if EARNKARO_ID:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}&r={EARNKARO_ID}"
    if "amazon.in" in original_url or "amzn.to" in original_url:
        return f"{original_url}&tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- Scraper ---
def fetch_live_deals():
    deals = []
    try:
        res = requests.get("https://www.desidime.com/feed", timeout=10)
        root = ET.fromstring(res.content)
        for item in root.findall('.//item')[:8]:
            link = item.find('link').text.strip()
            title = item.find('title').text.strip()
            desc = item.find('description').text
            soup = BeautifulSoup(desc, "html.parser")
            img = soup.find("img")
            img_url = img.get("src") if img else ""
            deals.append({"id": link, "title": title, "url": link, "image": img_url})
    except: pass
    return deals

# --- Bot Functions ---
async def post_now(bot, force=False):
    deals = fetch_live_deals()
    for deal in deals:
        if not force and is_already_posted(deal["id"]): continue
        
        aff_link = convert_to_affiliate(deal["url"])
        caption = f"🔥 **LOOT DEAL** 🔥\n\n📦 {deal['title']}\n\n⚡ *Limited Time! Grab Now!*"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now", url=aff_link)]])
        
        try:
            if deal['image']: await bot.send_photo(CHANNEL_ID, deal['image'], caption=caption, reply_markup=btn, parse_mode="Markdown")
            else: await bot.send_message(CHANNEL_ID, caption, reply_markup=btn, parse_mode="Markdown")
            mark_as_posted(deal["id"], deal["title"], deal["url"])
            await asyncio.sleep(2)
        except Exception as e: print(f"Post Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot Webhook Mode mein live hai!")

async def post_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await post_now(context.bot, force=True)
    await update.message.reply_text("✅ Deals posted!")

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("postnow", post_cmd))
    
    # WEBHOOK MODE (Conflict khatam)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        secret_token=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
