import os
import re
import sqlite3
import threading
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "@bipin_loot_deals").strip()
PORT = int(os.getenv("PORT", 8080))

# --- Database ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS posted_deals (id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT UNIQUE)")
    conn.commit()
    conn.close()

def is_already_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (deal_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO posted_deals (deal_id) VALUES (?)", (deal_id,))
    conn.commit()
    conn.close()

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

# --- Bot ---
async def post_deals(bot, force=False):
    deals = fetch_live_deals()
    for deal in deals:
        if not force and is_already_posted(deal["id"]): continue
        caption = f"🔥 **LOOT DEAL** 🔥\n\n📦 {deal['title']}\n\n⚡ *Grab Now!*"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now", url=deal['url'])]])
        try:
            if deal['image']: await bot.send_photo(CHANNEL_ID, deal['image'], caption=caption, reply_markup=btn, parse_mode="Markdown")
            else: await bot.send_message(CHANNEL_ID, caption, reply_markup=btn, parse_mode="Markdown")
            mark_as_posted(deal["id"])
        except Exception as e: print(f"Post Error: {e}")

async def post_cmd(update, context):
    await post_deals(context.bot, force=True)
    await update.message.reply_text("✅ Deals posted!")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is Live!")
    def log_message(self, format, *args): return

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthHandler).serve_forever(), daemon=True).start()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("postnow", post_cmd))
    app.job_queue.run_repeating(lambda ctx: post_deals(ctx.bot), interval=300, first=5)
    app.run_polling()

if __name__ == "__main__":
    main()
