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

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

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

def convert_to_affiliate(original_url):
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url or "fkrt.it" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

def fetch_trending_loot_deals():
    deals = []
    try:
        res = requests.get("https://www.desidime.com/feed", headers=HEADERS, timeout=12)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:15]:
                deal_url = item.find('link').text.strip() if item.find('link') is not None else ""
                title = item.find('title').text.strip() if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                guid = item.find('guid').text if item.find('guid') is not None else deal_url

                soup = BeautifulSoup(desc, "html.parser")
                img_tag = soup.find("img")
                img_url = img_tag.get("src") if img_tag else ""

                store_link_tag = soup.find("a", href=True)
                target_url = store_link_tag['href'] if store_link_tag else deal_url

                if target_url and title:
                    deals.append({
                        "id": guid,
                        "title": title,
                        "url": target_url,
                        "image_url": img_url
                    })
    except Exception as e:
        print(f"Feed error: {e}")
    return deals

async def post_deals_to_channel(bot, chat_to_notify=None):
    deals = fetch_trending_loot_deals()
    posted_count = 0
    err_log = ""

    for deal in deals:
        if is_already_posted(deal["id"]):
            continue

        aff_link = convert_to_affiliate(deal["url"])
        
        # Clean text
        title_clean = deal['title'].replace('*', '').replace('_', '')
        caption = (
            f"🔥 *SUPER LOOT DEAL* 🔥\n\n"
            f"📦 {title_clean}\n\n"
            f"⚡ *Limited Period Offer!*"
        )
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Check Deal", url=aff_link)]])

        try:
            if deal.get("image_url") and deal["image_url"].startswith("http"):
                await bot.send_photo(chat_id=CHANNEL_ID, photo=deal["image_url"], caption=caption, reply_markup=btn, parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=CHANNEL_ID, text=caption, reply_markup=btn, parse_mode="Markdown")

            mark_as_posted(deal["id"], deal["title"], deal["url"])
            posted_count += 1
            await asyncio.sleep(3)
        except Exception as e:
            err_log = str(e)
            print(f"Post error: {e}")
            break

    if chat_to_notify:
        if posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Nayi Deals channel mein post ho chuki hain!")
        elif err_log:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Channel Error: `{err_log}`\nCheck karein ki CHANNEL_ID aur Admin permissions sahi hain.", parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari trending deals pehle se posted hain. Agle 5 min mein nayi aate hi post ho jayengi.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot Live Hai! Channel check karne ke liye send karein: `/postnow`", parse_mode="Markdown")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Live deals fetch karke channel par bheji ja rahi hain...")
    await post_deals_to_channel(context.bot, chat_to_notify=update.effective_chat.id)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Active 24/7!")
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
    app.job_queue.run_repeating(auto_job, interval=300, first=3)

    app.run_polling()

if __name__ == "__main__":
    main()
